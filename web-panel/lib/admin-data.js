const { many, one, tableCount, getSettingsMap, setSetting, exec } = require("./db");

const DEFAULT_TOMAN_RATE = 90000;

async function safe(value, fallback) {
  try {
    return await value;
  } catch {
    return fallback;
  }
}

async function getBlockedTelegramIds() {
  const value = String((await safe(getSettingsMap(["blocked_telegram_ids"]), {}))?.blocked_telegram_ids || "").trim();
  if (!value) return new Set();
  return new Set(
    value
      .split(",")
      .map((part) => Number(part.trim()))
      .filter((id) => Number.isInteger(id) && id > 0)
  );
}

async function getWalletRate() {
  const values = await safe(getSettingsMap(["usdt_to_toman_rate"]), {});
  const raw = Number(values?.usdt_to_toman_rate || process.env.USDT_TO_TOMAN_RATE || 0);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_TOMAN_RATE;
}

async function getRecentActivity(limit = 30) {
  return safe(many(
    `SELECT
       a.*,
       u.id AS user_id,
       u.first_name AS user_first_name,
       u.username AS user_username
     FROM activity_logs a
     LEFT JOIN users u ON u.telegram_id = a.telegram_id
     ORDER BY a.created_at DESC
     LIMIT ?`,
    [limit]
  ), []);
}

async function getDashboardStats() {
  const rows = await Promise.all([
    safe(tableCount("users"), 0),
    safe(tableCount("subscriptions"), 0),
    safe(tableCount("payments"), 0),
    safe(tableCount("tickets"), 0),
    safe(one("SELECT COUNT(*) AS count FROM subscriptions WHERE status = ?", ["active"]), { count: 0 }),
    safe(one("SELECT COUNT(*) AS count FROM subscriptions WHERE status IN ('expired', 'disabled', 'depleted')"), { count: 0 }),
    safe(one("SELECT COUNT(*) AS count FROM payments WHERE status IN ('awaiting_review', 'waiting', 'confirming', 'pending')"), { count: 0 }),
    safe(one("SELECT COALESCE(SUM(amount_usdt), 0) AS total FROM payments WHERE status IN ('confirmed', 'finished')"), { total: 0 }),
    safe(one("SELECT COUNT(*) AS count FROM activity_logs"), { count: 0 }),
    getRecentActivity(12),
    safe(many(
      `SELECT p.id, p.name, p.traffic_gb, p.duration_days, p.price_usdt, p.price_toman, p.is_active,
              COUNT(s.id) AS subscription_count
       FROM plans p
       LEFT JOIN subscriptions s ON s.plan_id = p.id
       GROUP BY p.id
       ORDER BY subscription_count DESC, p.sort_order ASC, p.id ASC
       LIMIT 8`
    ), []),
    safe(many(
      `SELECT p.*, u.telegram_id, u.username, u.first_name
       FROM payments p
       LEFT JOIN users u ON u.id = p.user_id
       ORDER BY p.created_at DESC
       LIMIT 8`
    ), []),
  ]);

  return {
    users: rows[0],
    subscriptions: rows[1],
    payments: rows[2],
    tickets: rows[3],
    activeSubscriptions: Number(rows[4]?.count || 0),
    finishedSubscriptions: Number(rows[5]?.count || 0),
    pendingPayments: Number(rows[6]?.count || 0),
    revenue: Number(rows[7]?.total || 0),
    activityCount: Number(rows[8]?.count || 0),
    recentActivity: rows[9],
    planMix: rows[10],
    recentPayments: rows[11],
  };
}

async function getCustomers(limit = 100, search = "") {
  const blockedIds = await getBlockedTelegramIds();
  const needle = String(search || "").trim();
  const where = needle
    ? `WHERE
         LOWER(CAST(u.telegram_id AS TEXT)) LIKE LOWER(?)
         OR LOWER(COALESCE(u.username, '')) LIKE LOWER(?)
         OR LOWER(COALESCE(u.first_name, '')) LIKE LOWER(?)`
    : "";
  const params = needle ? [`%${needle}%`, `%${needle}%`, `%${needle}%`, limit] : [limit];
  const users = await safe(many(
    `
      SELECT
        u.*,
        COUNT(DISTINCT s.id) AS subscription_count,
        COUNT(DISTINCT p.id) AS payment_count,
        COALESCE(SUM(CASE WHEN s.status = 'active' THEN 1 ELSE 0 END), 0) AS active_subscriptions,
        COALESCE(SUM(CASE WHEN p.status IN ('confirmed','finished') THEN p.amount_usdt ELSE 0 END), 0) AS paid_total
      FROM users u
      LEFT JOIN subscriptions s ON s.user_id = u.id
      LEFT JOIN payments p ON p.user_id = u.id
      ${where}
      GROUP BY u.id
      ORDER BY u.created_at DESC
      LIMIT ?
    `,
    params
  ), []);
  return users.map((user) => ({
    ...user,
    is_blocked: blockedIds.has(Number(user.telegram_id)),
  }));
}

async function getCustomerById(id) {
  const user = await safe(one(
    `SELECT
       u.*,
       ref_u.id AS referrer_user_id,
       ref_u.telegram_id AS referrer_telegram_id,
       ref_u.username AS referrer_username,
       ref_u.first_name AS referrer_first_name
     FROM users u
     LEFT JOIN users ref_u ON ref_u.id = u.referred_by
     WHERE u.id = ?`,
    [id]
  ), null);
  if (!user) return null;
  const blockedIds = await getBlockedTelegramIds();
  return {
    ...user,
    is_blocked: blockedIds.has(Number(user.telegram_id)),
  };
}

async function getCustomerReferralSummary(userId) {
  const [invited, earned, linked] = await Promise.all([
    safe(one(
      `SELECT
         COUNT(*) AS total_referrals,
         SUM(CASE WHEN reward_granted = 1 THEN 1 ELSE 0 END) AS converted_referrals
       FROM referrals
       WHERE referrer_id = ?`,
      [userId]
    ), { total_referrals: 0, converted_referrals: 0 }),
    safe(one(
      `SELECT
         COALESCE(SUM(amount_usdt), 0) AS earned_usdt,
         COALESCE(SUM(amount_toman), 0) AS earned_toman
       FROM referral_commissions
       WHERE referrer_id = ?`,
      [userId]
    ), { earned_usdt: 0, earned_toman: 0 }),
    safe(one(
      `SELECT
         r.created_at,
         ref_u.id AS referrer_user_id,
         ref_u.telegram_id AS referrer_telegram_id,
         ref_u.username AS referrer_username,
         ref_u.first_name AS referrer_first_name
       FROM referrals r
       LEFT JOIN users ref_u ON ref_u.id = r.referrer_id
       WHERE r.referred_id = ?
       LIMIT 1`,
      [userId]
    ), null),
  ]);

  return {
    total_referrals: Number(invited?.total_referrals || 0),
    converted_referrals: Number(invited?.converted_referrals || 0),
    earned_usdt: Number(earned?.earned_usdt || 0),
    earned_toman: Number(earned?.earned_toman || 0),
    linked_referrer: linked,
  };
}

async function getCustomerSubscriptions(userId) {
  return safe(many(
    `SELECT s.*, p.name AS plan_name
     FROM subscriptions s
     LEFT JOIN plans p ON p.id = s.plan_id
     WHERE s.user_id = ?
     ORDER BY s.created_at DESC`,
    [userId]
  ), []);
}

async function getCustomerPayments(userId) {
  return safe(many(
    `SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC`,
    [userId]
  ), []);
}

async function getCustomerActivity(telegramId, limit = 30) {
  return safe(many(
    `SELECT *
     FROM activity_logs
     WHERE telegram_id = ?
     ORDER BY created_at DESC
     LIMIT ?`,
    [telegramId, limit]
  ), []);
}

async function getPlans() {
  return safe(many("SELECT * FROM plans ORDER BY sort_order ASC, id ASC"), []);
}

async function getTestSubscriptionUsers(limit = 200) {
  return safe(many(
    `SELECT
       tsr.id,
       tsr.telegram_id,
       tsr.created_at,
       u.id AS user_id,
       u.username,
       u.first_name,
       u.created_at AS user_created_at,
       COUNT(DISTINCT s.id) AS subscription_count
     FROM test_subscription_records tsr
     LEFT JOIN users u ON u.telegram_id = tsr.telegram_id
     LEFT JOIN subscriptions s ON s.user_id = u.id
     GROUP BY tsr.id, tsr.telegram_id, tsr.created_at, u.id, u.username, u.first_name, u.created_at
     ORDER BY tsr.created_at DESC
     LIMIT ?`,
    [limit]
  ), []);
}

async function getPlanById(id) {
  return safe(one("SELECT * FROM plans WHERE id = ?", [id]), null);
}

async function getDiscounts() {
  return safe(many("SELECT * FROM discount_codes ORDER BY created_at DESC, id DESC"), []);
}

async function getPayments(limit = 100) {
  return safe(many(
    `SELECT p.*, u.telegram_id, u.username, u.first_name
     FROM payments p
     LEFT JOIN users u ON u.id = p.user_id
     ORDER BY p.created_at DESC
     LIMIT ?`,
    [limit]
  ), []);
}

async function getReferralOverview(limit = 100) {
  const [rate, summary, hosts, recent] = await Promise.all([
    getWalletRate(),
    safe(Promise.all([
      one("SELECT COUNT(*) AS count FROM referrals"),
      one("SELECT COUNT(DISTINCT referrer_id) AS count FROM referrals"),
      one("SELECT COALESCE(SUM(amount_usdt), 0) AS total FROM referral_commissions"),
      one("SELECT COALESCE(SUM(amount_toman), 0) AS total FROM referral_commissions"),
    ]), [{ count: 0 }, { count: 0 }, { total: 0 }, { total: 0 }]),
    safe(many(
      `SELECT
         u.id, u.telegram_id, u.username, u.first_name, u.referral_code,
         COALESCE(r.total_referrals, 0) AS total_referrals,
         COALESCE(r.converted_referrals, 0) AS converted_referrals,
         COALESCE(c.earned_usdt, 0) AS earned_usdt,
         COALESCE(c.earned_toman, 0) AS earned_toman
       FROM users u
       LEFT JOIN (
         SELECT
           referrer_id,
           COUNT(*) AS total_referrals,
           SUM(CASE WHEN reward_granted = 1 THEN 1 ELSE 0 END) AS converted_referrals
         FROM referrals
         GROUP BY referrer_id
       ) r ON r.referrer_id = u.id
       LEFT JOIN (
         SELECT
           referrer_id,
           COALESCE(SUM(amount_usdt), 0) AS earned_usdt,
           COALESCE(SUM(amount_toman), 0) AS earned_toman
         FROM referral_commissions
         GROUP BY referrer_id
       ) c ON c.referrer_id = u.id
       WHERE COALESCE(r.total_referrals, 0) > 0 OR COALESCE(c.earned_usdt, 0) > 0
       ORDER BY earned_usdt DESC, total_referrals DESC, u.created_at DESC
       LIMIT ?`,
      [limit]
    ), []),
    safe(many(
      `SELECT
         rc.*,
         p.order_id,
         ref_u.telegram_id AS referrer_telegram_id,
         ref_u.username AS referrer_username,
         ref_u.first_name AS referrer_first_name,
         usr.telegram_id AS referred_telegram_id,
         usr.username AS referred_username,
         usr.first_name AS referred_first_name
       FROM referral_commissions rc
       LEFT JOIN payments p ON p.id = rc.payment_id
       LEFT JOIN users ref_u ON ref_u.id = rc.referrer_id
       LEFT JOIN users usr ON usr.id = rc.referred_id
       ORDER BY rc.created_at DESC
       LIMIT 20`
    ), []),
  ]);

  return {
    rate,
    summary: {
      totalReferrals: Number(summary[0]?.count || 0),
      activeReferrers: Number(summary[1]?.count || 0),
      totalCommissionUsdt: Number(summary[2]?.total || 0),
      totalCommissionToman: Number(summary[3]?.total || 0),
    },
    hosts,
    recent,
  };
}

async function getTickets(limit = 100) {
  return safe(many(
    `SELECT t.*, u.telegram_id, u.username, u.first_name
     FROM tickets t
     LEFT JOIN users u ON u.id = t.user_id
     ORDER BY t.created_at DESC
     LIMIT ?`,
    [limit]
  ), []);
}

async function getTicketsMessages(ticketId) {
  return safe(many(
    `SELECT tm.*, u.telegram_id, u.username
     FROM ticket_messages tm
     LEFT JOIN users u ON u.id = tm.sender_id
     WHERE tm.ticket_id = ?
     ORDER BY tm.created_at ASC`,
    [ticketId]
  ), []);
}

async function getTicketById(ticketId) {
  return safe(one(
    `SELECT t.*, u.telegram_id, u.username, u.first_name, u.is_admin AS owner_is_admin
     FROM tickets t
     LEFT JOIN users u ON u.id = t.user_id
     WHERE t.id = ?`,
    [ticketId]
  ), null);
}

async function getBackupsSettings() {
  const keys = [
    "BOT_TOKEN",
    "PANEL_URL",
    "PANEL_API_TOKEN",
    "PANEL_USERNAME",
    "PANEL_PASSWORD",
    "SUB_PORT",
    "enabled_inbound_ids",
    "payment_crypto_enabled",
    "payment_card_enabled",
    "payment_crypto_invoice",
    "crypto_gateway",
    "card_number",
    "card_holder",
    "notification_expiry_enabled",
    "notification_traffic_enabled",
    "test_sub_enabled",
    "test_sub_traffic_gb",
    "test_sub_duration_days",
    "test_sub_inbound_id",
    "broadcast_default_photo",
    "broadcast_default_caption",
    "banner_file_id",
    "banner_preview_url",
    "welcome_banner_file_id",
    "welcome_banner_preview_url",
    "welcome_banner_caption",
    "notice_warning_enabled",
    "notice_warning_text",
    "wallet_usd_enabled",
    "wallet_toman_enabled",
    "usdt_to_toman_rate",
    "referral_commission_percent",
    "NOWPAYMENTS_API_KEY",
    "NOWPAYMENTS_IPN_SECRET",
    "NOWPAYMENTS_IPN_URL",
    "NOWPAYMENTS_PAY_CURRENCY",
    "MAXELPAY_API_KEY",
    "MAXELPAY_WEBHOOK_SECRET",
    "MAXELPAY_WEBHOOK_URL",
  ];
  const values = await safe(getSettingsMap(keys), {});
  const envFallback = Object.fromEntries(keys.map((key) => [key, process.env[key] ?? ""]));
  return { ...envFallback, ...values };
}

module.exports = {
  getDashboardStats,
  getRecentActivity,
  getCustomers,
  getCustomerById,
  getCustomerReferralSummary,
  getCustomerSubscriptions,
  getCustomerPayments,
  getCustomerActivity,
  getWalletRate,
  getPlans,
  getTestSubscriptionUsers,
  getPlanById,
  getDiscounts,
  getPayments,
  getReferralOverview,
  getTickets,
  getTicketsMessages,
  getTicketById,
  getBackupsSettings,
  setSetting,
  exec,
};
