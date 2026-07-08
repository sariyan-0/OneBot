import { NextResponse } from "next/server";

import { exec, getSetting, one } from "../../../../../lib/db";
import {
  buildSubLink,
  createClient,
  deleteClient,
  getClientLinks,
  getPanelConnectionConfig,
  updateClient,
} from "../../../../../lib/xui";

function parseOrderId(orderId) {
  const raw = String(orderId || "");
  if (raw.startsWith("wallet_")) {
    return { kind: "wallet" };
  }
  if (raw.startsWith("renew_") || raw.startsWith("change_")) {
    const [action, subIdRaw, planIdRaw] = raw.split("_", 4);
    return {
      kind: action,
      subscriptionId: Number(subIdRaw || 0),
      planId: Number(planIdRaw || 0),
    };
  }
  return { kind: "new" };
}

async function getBotToken() {
  return String((await getSetting("BOT_TOKEN", "")) || process.env.BOT_TOKEN || "").trim();
}

async function sendTelegramMessage(chatId, text) {
  const botToken = await getBotToken();
  if (!botToken || !chatId) return;
  await fetch(`https://api.telegram.org/bot${botToken}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text,
      parse_mode: "HTML",
    }),
    cache: "no-store",
  }).catch(() => null);
}

async function grantReferralCommission(payment) {
  if (!payment || String(payment.payment_method || "").toLowerCase().startsWith("wallet")) {
    return null;
  }

  const existing = await one("SELECT id FROM referral_commissions WHERE payment_id = ?", [payment.id]);
  if (existing) return null;

  const referredUser = await one("SELECT id, referred_by FROM users WHERE id = ?", [payment.user_id]);
  if (!referredUser?.referred_by || Number(referredUser.referred_by) === Number(referredUser.id)) {
    return null;
  }

  const referral = await one("SELECT * FROM referrals WHERE referred_id = ?", [referredUser.id]);
  if (!referral) return null;

  const percentRaw = await getSetting("referral_commission_percent", "10");
  const percent = Math.max(0, Math.min(100, Number(percentRaw || 10) || 10));
  if (percent <= 0) return null;

  const amountUsdt = Number(payment.amount_usdt || 0);
  const method = String(payment.payment_method || "").toLowerCase();
  const exactToman = Number(payment.amount_rial || 0) > 0 ? Math.floor(Number(payment.amount_rial || 0) / 10) : 0;
  let commissionUsdt = 0;
  let commissionToman = 0;
  let walletColumn = "wallet_balance_usdt";
  let walletDelta = 0;

  if (method === "card") {
    commissionToman = exactToman > 0 ? Math.round(exactToman * percent / 100) : 0;
    if (commissionToman <= 0) return null;
    walletColumn = "wallet_balance_toman";
    walletDelta = commissionToman;
  } else {
    commissionUsdt = Number((amountUsdt * percent / 100).toFixed(8));
    if (commissionUsdt <= 0) return null;
    walletDelta = commissionUsdt;
    const rateRaw = await getSetting("usdt_to_toman_rate", process.env.USDT_TO_TOMAN_RATE || "90000");
    const rate = Number(rateRaw || 0) > 0 ? Number(rateRaw) : 90000;
    commissionToman = Math.round(commissionUsdt * rate);
  }

  await exec(
    `UPDATE users SET ${walletColumn} = ${walletColumn} + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?`,
    [walletDelta, referral.referrer_id]
  );
  await exec(
     `INSERT INTO referral_commissions(referrer_id, referred_id, payment_id, percent, amount_usdt, amount_toman, created_at)
      VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)`,
    [referral.referrer_id, referral.referred_id, payment.id, percent, commissionUsdt, commissionToman]
  );
  if (!referral.reward_granted) {
    await exec("UPDATE referrals SET reward_granted = 1 WHERE id = ?", [referral.id]);
  }
  return { referrerId: referral.referrer_id, amountUsdt: commissionUsdt, amountToman: commissionToman, percent };
}

async function createSubscriptionFromPlan(user, plan, payment) {
  const inboundIds = String(plan.inbound_ids || "")
    .split(",")
    .map((value) => Number(value.trim()))
    .filter(Boolean);

  const requestedEmail = `client-${user.telegram_id}-${Date.now()}`;
  const requestedSubId = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`.slice(0, 16);
  const created = await createClient({
    inboundIds,
    email: requestedEmail,
    trafficGb: Number(plan.traffic_gb || 0),
    expireDays: Number(plan.duration_days || 0),
    subId: requestedSubId,
    limitIp: Number(plan.limit_ip || 0),
    tgId: Number(user.telegram_id || 0),
  });

  const panelCfg = await getPanelConnectionConfig();
  const dbEmail = String(created?.email || requestedEmail).trim();
  const dbSubId = String(created?.subId || created?.sub_id || requestedSubId).trim();
  const subLink = buildSubLink(panelCfg.panelUrl || "", dbSubId, Number(panelCfg.subPort || 0));
  const configLinks = await getClientLinks(dbEmail).catch(() => []);
  const expiryTime = Number(created?.expiryTime || 0);
  const expiryDate = expiryTime > 0 ? new Date(expiryTime).toISOString() : null;
  const trafficUp = Number(created?.up ?? created?.traffic?.up ?? 0);
  const trafficDown = Number(created?.down ?? created?.traffic?.down ?? 0);
  const totalGb = Number(created?.totalGB || 0) > 0
    ? Math.floor(Number(created.totalGB) / 1024 ** 3)
    : Number(plan.traffic_gb || 0);
  const inboundId = Number(created?.inboundIds?.[0] || created?.inbound_id || inboundIds[0] || 0);
  const clientUuid = String(created?.uuid || created?.id || dbSubId).trim();

  const insertResult = await exec(
    `INSERT INTO subscriptions(
      user_id, email, client_uuid, sub_id, plan_id, inbound_id,
      traffic_limit_gb, used_traffic_bytes, expiry_date, limit_ip,
      warned_7d, warned_3d, warned_1d, status, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)`,
    [
      user.id,
      dbEmail,
      clientUuid,
      dbSubId,
      plan.id,
      inboundId,
      totalGb,
      trafficUp + trafficDown,
      expiryDate,
      Number(created?.limitIp || plan.limit_ip || 0),
      created?.enable === false ? "disabled" : "active",
    ]
  );

  const subscriptionId = Number(insertResult?.lastInsertRowid || 0) || null;
  await exec(
    "UPDATE payments SET status = ?, subscription_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
    ["confirmed", subscriptionId, payment.id]
  );

  return { subLink, configLinks, subscriptionId };
}

async function applyPlanToExistingSubscription(user, plan, payment, parsedOrder) {
  const sub = await one("SELECT * FROM subscriptions WHERE id = ? AND user_id = ?", [parsedOrder.subscriptionId, user.id]);
  if (!sub) {
    throw new Error("Subscription not found.");
  }

  const expiryBase = sub.expiry_date ? new Date(sub.expiry_date) : new Date();
  const now = new Date();
  const base = Number.isNaN(expiryBase.getTime()) || expiryBase < now ? now : expiryBase;
  const nextExpiry = new Date(base.getTime() + Number(plan.duration_days || 0) * 86400000);

  if (parsedOrder.kind === "change") {
    if (sub.email) {
      try {
        await deleteClient(sub.email);
      } catch {
        // best effort
      }
    }
    const inboundIds = String(plan.inbound_ids || "")
      .split(",")
      .map((value) => Number(value.trim()))
      .filter(Boolean);
    const created = await createClient({
      inboundIds,
      email: sub.email,
      trafficGb: Number(plan.traffic_gb || 0),
      expireDays: Number(plan.duration_days || 0),
      subId: sub.sub_id || null,
      limitIp: Number(plan.limit_ip || 0),
      tgId: Number(user.telegram_id || 0),
    });
    sub.inbound_id = Number(created?.inboundIds?.[0] || created?.inbound_id || sub.inbound_id || 0);
  } else {
    await updateClient(sub.email, {
      trafficGb: Number(plan.traffic_gb || 0),
      expireDays: Number(plan.duration_days || 0),
      enable: true,
      tgId: Number(user.telegram_id || 0),
      limitIp: Number(plan.limit_ip || 0),
    });
  }

  const panelCfg = await getPanelConnectionConfig();
  const subLink = buildSubLink(panelCfg.panelUrl || "", sub.sub_id || "", Number(panelCfg.subPort || 0));
  const configLinks = await getClientLinks(sub.email).catch(() => []);

  await exec(
    `UPDATE subscriptions
     SET plan_id = ?, inbound_id = COALESCE(NULLIF(?, 0), inbound_id),
         traffic_limit_gb = ?, used_traffic_bytes = 0, expiry_date = ?, limit_ip = ?,
         status = 'active', updated_at = CURRENT_TIMESTAMP
     WHERE id = ?`,
    [
      plan.id,
      Number(sub.inbound_id || String(plan.inbound_ids || "").split(",").map((v) => Number(v.trim())).filter(Boolean)[0] || 0),
      Number(plan.traffic_gb || 0),
      nextExpiry.toISOString(),
      Number(plan.limit_ip || 0),
      sub.id,
    ]
  );

  await exec(
    "UPDATE payments SET status = ?, subscription_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
    ["confirmed", sub.id, payment.id]
  );

  return { subLink, configLinks, subscriptionId: sub.id };
}

async function approvePayment(payment) {
  const user = await one("SELECT * FROM users WHERE id = ?", [payment.user_id]);
  if (!user) {
    throw new Error("Customer not found.");
  }

  const parsedOrder = parseOrderId(payment.order_id);
  if (parsedOrder.kind === "wallet") {
    const exactToman = Number(payment.amount_rial || 0) > 0 ? Math.floor(Number(payment.amount_rial || 0) / 10) : 0;
    const creditAmount = exactToman > 0 ? exactToman : Math.round(Number(payment.amount_usdt || 0) || 0);
    await exec(
      "UPDATE users SET wallet_balance_toman = wallet_balance_toman + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
      [creditAmount, user.id]
    );
    await exec(
      "UPDATE payments SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
      ["confirmed", payment.id]
    );
    const referralReward = await grantReferralCommission(payment);
    const currentUser = await one("SELECT wallet_balance_toman FROM users WHERE id = ?", [user.id]);
    const addedToman = Number(payment.amount_rial || 0) > 0 ? Math.floor(Number(payment.amount_rial || 0) / 10) : creditAmount;
    await sendTelegramMessage(
      user.telegram_id,
      [
        "💼 <b>شارژ کیف پول شما تأیید شد!</b>",
        "━━━━━━━━━━━━━━━",
        `🔖 سفارش: <code>${payment.order_id}</code>`,
        "💰 مبلغ افزوده‌شده:",
        `  • <b>${addedToman.toLocaleString()} تومان</b>`,
        "💳 موجودی جدید:",
        `  • <b>${Number(currentUser?.wallet_balance_toman || 0).toLocaleString()} تومان</b>`,
      ].join("\n")
    );
    return { ok: true, kind: "wallet", referralReward };
  }

  const planId = parsedOrder.planId || Number(payment.inbound_id || 0);
  const plan = await one("SELECT * FROM plans WHERE id = ?", [planId]);
  if (!plan) {
    throw new Error("Plan not found.");
  }

  const result = parsedOrder.kind === "renew" || parsedOrder.kind === "change"
    ? await applyPlanToExistingSubscription(user, plan, payment, parsedOrder)
    : await createSubscriptionFromPlan(user, plan, payment);
  const referralReward = await grantReferralCommission(payment);

  await sendTelegramMessage(
    user.telegram_id,
    [
      "🎉 <b>پرداخت شما تأیید شد!</b>",
      "━━━━━━━━━━━━━━━",
      `🔖 سفارش: <code>${payment.order_id}</code>`,
      "",
      "اشتراک VPN شما فعال شد.",
      `🔗 <b>لینک اشتراک:</b>`,
      `<code>${result.subLink}</code>`,
    ].join("\n")
  );

  return { ok: true, kind: "subscription", referralReward, ...result };
}

export async function POST(request, { params }) {
  const paymentId = Number(params.id);
  if (!paymentId) {
    return NextResponse.json({ ok: false, error: "Invalid payment id" }, { status: 400 });
  }

  const body = await request.json().catch(() => ({}));
  const action = String(body.action || "").trim().toLowerCase();
  if (!["approve", "reject"].includes(action)) {
    return NextResponse.json({ ok: false, error: "Invalid review action" }, { status: 400 });
  }

  const payment = await one("SELECT * FROM payments WHERE id = ?", [paymentId]);
  if (!payment) {
    return NextResponse.json({ ok: false, error: "Payment not found" }, { status: 404 });
  }
  if (String(payment.payment_method || "").toLowerCase() !== "card") {
    return NextResponse.json({ ok: false, error: "Only card payments can be reviewed here" }, { status: 400 });
  }
  if (String(payment.status || "").toLowerCase() === "awaiting_review") {
    // ok
  } else if (["confirmed", "finished"].includes(String(payment.status || "").toLowerCase())) {
    return NextResponse.json({ ok: false, error: "This payment has already been approved" }, { status: 400 });
  } else if (action === "approve") {
    return NextResponse.json({ ok: false, error: `Cannot approve a payment in '${payment.status}' state` }, { status: 400 });
  }

  if (action === "reject") {
    await exec(
      "UPDATE payments SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
      ["failed", payment.id]
    );
    const user = await one("SELECT telegram_id FROM users WHERE id = ?", [payment.user_id]);
    if (user?.telegram_id) {
      await sendTelegramMessage(
        user.telegram_id,
        [
          "❌ <b>پرداخت شما تأیید نشد.</b>",
          "",
          `🔖 سفارش: <code>${payment.order_id}</code>`,
          "",
          "در صورت وجود مشکل با پشتیبانی تماس بگیرید.",
        ].join("\n")
      );
    }
    return NextResponse.json({ ok: true, status: "failed" });
  }

  try {
    const result = await approvePayment(payment);
    return NextResponse.json({ ok: true, status: "confirmed", ...result });
  } catch (error) {
    return NextResponse.json(
      { ok: false, error: error instanceof Error ? error.message : "Could not approve payment" },
      { status: 500 }
    );
  }
}
