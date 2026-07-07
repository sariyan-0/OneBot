const fs = require("fs");
const path = require("path");
const { Pool } = require("pg");
const Database = require("better-sqlite3");
const { getRootDir } = require("./root");

let sqliteDb = null;
let pgPool = null;
let sqliteSchemaReady = false;
let pgSchemaReady = false;

function dbUrl() {
  return process.env.DB_URL || "sqlite+aiosqlite:///./bot_data.db";
}

function sharedDataDir() {
  const configured = String(process.env.ONEBOT_DATA_DIR || "").trim();
  if (configured) {
    return path.resolve(configured);
  }
  return path.resolve(getRootDir(), "data");
}

function isPostgres() {
  return /^postgres(ql)?:\/\//i.test(dbUrl());
}

function sqlitePath() {
  if (isPostgres()) return null;
  const raw = dbUrl()
    .replace("sqlite+aiosqlite:///", "")
    .replace("sqlite:///", "");
  const dataDir = sharedDataDir();
  if (!raw || raw === "./bot_data.db" || raw === "bot_data.db") {
    return path.join(dataDir, "bot_data.db");
  }
  if (path.isAbsolute(raw)) {
    if (path.basename(raw) === "bot_data.db") {
      return path.join(dataDir, "bot_data.db");
    }
    return raw;
  }
  return path.resolve(dataDir, raw);
}

function sqliteColumns(table) {
  try {
    return new Set(sqliteDb.prepare(`PRAGMA table_info(${table})`).all().map((row) => row.name));
  } catch {
    return new Set();
  }
}

function ensureSqliteColumn(table, column, ddl) {
  const columns = sqliteColumns(table);
  if (!columns.has(column)) {
    sqliteDb.exec(ddl);
  }
}

function bootstrapSqliteSchema() {
  const db = sqliteDb;
  if (!db) return;

  db.exec(`
    CREATE TABLE IF NOT EXISTS admin_settings (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      telegram_id BIGINT NOT NULL UNIQUE,
      username VARCHAR(64),
      first_name VARCHAR(128),
      is_admin BOOLEAN NOT NULL DEFAULT 0,
      wallet_balance_usdt FLOAT NOT NULL DEFAULT 0,
      wallet_balance_toman INTEGER NOT NULL DEFAULT 0,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      referral_code VARCHAR(16) UNIQUE,
      referred_by INTEGER,
      FOREIGN KEY(referred_by) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS plans (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name VARCHAR(64) NOT NULL,
      traffic_gb INTEGER NOT NULL DEFAULT 0,
      duration_days INTEGER NOT NULL DEFAULT 30,
      price_usdt FLOAT NOT NULL,
      price_toman INTEGER NOT NULL DEFAULT 0,
      limit_ip INTEGER NOT NULL DEFAULT 0,
      inbound_ids VARCHAR(256) NOT NULL DEFAULT '',
      is_active BOOLEAN NOT NULL DEFAULT 1,
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS payments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      order_id VARCHAR(64) NOT NULL UNIQUE,
      payment_id VARCHAR(64),
      amount_usdt FLOAT NOT NULL,
      pay_currency VARCHAR(20) NOT NULL DEFAULT 'usdttrc20',
      pay_address VARCHAR(128),
      inbound_id INTEGER NOT NULL,
      payment_method VARCHAR(10) NOT NULL DEFAULT 'crypto',
      status VARCHAR(20) NOT NULL DEFAULT 'pending',
      expires_at DATETIME,
      amount_rial INTEGER,
      receipt_file_id VARCHAR(256),
      receipt_type VARCHAR(10),
      subscription_id INTEGER,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(user_id) REFERENCES users(id),
      FOREIGN KEY(subscription_id) REFERENCES subscriptions(id)
    );
    CREATE TABLE IF NOT EXISTS subscriptions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      email VARCHAR(128) NOT NULL UNIQUE,
      client_uuid VARCHAR(36) NOT NULL,
      sub_id VARCHAR(32) NOT NULL,
      plan_id INTEGER,
      inbound_id INTEGER NOT NULL,
      traffic_limit_gb INTEGER NOT NULL DEFAULT 0,
      used_traffic_bytes BIGINT NOT NULL DEFAULT 0,
      expiry_date DATETIME,
      limit_ip INTEGER NOT NULL DEFAULT 0,
      warned_7d BOOLEAN NOT NULL DEFAULT 0,
      warned_3d BOOLEAN NOT NULL DEFAULT 0,
      warned_1d BOOLEAN NOT NULL DEFAULT 0,
      status VARCHAR(16) NOT NULL DEFAULT 'active',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(user_id) REFERENCES users(id),
      FOREIGN KEY(plan_id) REFERENCES plans(id)
    );
    CREATE TABLE IF NOT EXISTS tickets (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      subject VARCHAR(256) NOT NULL,
      status VARCHAR(16) NOT NULL DEFAULT 'open',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      closed_at DATETIME,
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS ticket_messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ticket_id INTEGER NOT NULL,
      sender_id INTEGER NOT NULL,
      body TEXT NOT NULL,
      is_admin_reply BOOLEAN NOT NULL DEFAULT 0,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(ticket_id) REFERENCES tickets(id),
      FOREIGN KEY(sender_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS referrals (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      referrer_id INTEGER NOT NULL,
      referred_id INTEGER NOT NULL UNIQUE,
      reward_days INTEGER NOT NULL DEFAULT 0,
      reward_granted BOOLEAN NOT NULL DEFAULT 0,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(referrer_id) REFERENCES users(id),
      FOREIGN KEY(referred_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS referral_commissions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      referrer_id INTEGER NOT NULL,
      referred_id INTEGER NOT NULL,
      payment_id INTEGER NOT NULL UNIQUE,
      percent FLOAT NOT NULL DEFAULT 0,
      amount_usdt FLOAT NOT NULL DEFAULT 0,
      amount_toman INTEGER NOT NULL DEFAULT 0,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(referrer_id) REFERENCES users(id),
      FOREIGN KEY(referred_id) REFERENCES users(id),
      FOREIGN KEY(payment_id) REFERENCES payments(id)
    );
    CREATE TABLE IF NOT EXISTS discount_codes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      code VARCHAR(32) NOT NULL UNIQUE,
      percent INTEGER NOT NULL,
      max_uses INTEGER,
      used_count INTEGER NOT NULL DEFAULT 0,
      is_active BOOLEAN NOT NULL DEFAULT 1,
      expires_at DATETIME,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS test_subscription_records (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      telegram_id BIGINT NOT NULL UNIQUE,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS activity_logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      direction VARCHAR(8) NOT NULL,
      event_type VARCHAR(32) NOT NULL,
      telegram_id BIGINT,
      username VARCHAR(64),
      text TEXT NOT NULL DEFAULT '',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
  `);

  ensureSqliteColumn("subscriptions", "plan_id", "ALTER TABLE subscriptions ADD COLUMN plan_id INTEGER");
  ensureSqliteColumn("users", "wallet_balance_usdt", "ALTER TABLE users ADD COLUMN wallet_balance_usdt FLOAT NOT NULL DEFAULT 0");
  ensureSqliteColumn("users", "wallet_balance_toman", "ALTER TABLE users ADD COLUMN wallet_balance_toman INTEGER NOT NULL DEFAULT 0");
  ensureSqliteColumn("plans", "price_toman", "ALTER TABLE plans ADD COLUMN price_toman INTEGER NOT NULL DEFAULT 0");
  ensureSqliteColumn("plans", "limit_ip", "ALTER TABLE plans ADD COLUMN limit_ip INTEGER NOT NULL DEFAULT 0");
  ensureSqliteColumn("plans", "inbound_ids", "ALTER TABLE plans ADD COLUMN inbound_ids VARCHAR(256) NOT NULL DEFAULT ''");
  ensureSqliteColumn("plans", "sort_order", "ALTER TABLE plans ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0");
  ensureSqliteColumn("referral_commissions", "amount_toman", "ALTER TABLE referral_commissions ADD COLUMN amount_toman INTEGER NOT NULL DEFAULT 0");
}

function getSqlite() {
  if (!sqliteDb) {
    const file = sqlitePath();
    fs.mkdirSync(path.dirname(file), { recursive: true });
    sqliteDb = new Database(file, { readonly: false, fileMustExist: false });
    sqliteDb.pragma("journal_mode = WAL");
    bootstrapSqliteSchema();
  }
  if (!sqliteSchemaReady) {
    try {
      bootstrapSqliteSchema();
      sqliteSchemaReady = true;
    } catch {
      // Schema bootstrap is best-effort in the panel; the bot performs the full migration path.
    }
  }
  return sqliteDb;
}

function getPgPool() {
  if (!pgPool) {
    pgPool = new Pool({ connectionString: dbUrl().replace("postgresql+asyncpg://", "postgresql://") });
  }
  return pgPool;
}

async function ensurePgSchema() {
  if (pgSchemaReady) return;
  try {
    const statements = [
      `CREATE TABLE IF NOT EXISTS admin_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
      )`,
      `ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_balance_usdt DOUBLE PRECISION NOT NULL DEFAULT 0`,
      `ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_balance_toman INTEGER NOT NULL DEFAULT 0`,
      `ALTER TABLE plans ADD COLUMN IF NOT EXISTS price_toman INTEGER NOT NULL DEFAULT 0`,
      `ALTER TABLE plans ADD COLUMN IF NOT EXISTS limit_ip INTEGER NOT NULL DEFAULT 0`,
      `ALTER TABLE plans ADD COLUMN IF NOT EXISTS inbound_ids VARCHAR(256) NOT NULL DEFAULT ''`,
      `ALTER TABLE plans ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0`,
      `ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS plan_id INTEGER`,
      `ALTER TABLE referral_commissions ADD COLUMN IF NOT EXISTS amount_toman INTEGER NOT NULL DEFAULT 0`,
    ];
    for (const statement of statements) {
      try {
        await getPgPool().query(statement);
      } catch {
        // Best effort. The bot performs the full migration path on startup.
      }
    }
    pgSchemaReady = true;
  } catch {
    // Best effort. The bot runs the same migration path on startup.
  }
}

async function query(sql, params = []) {
  if (isPostgres()) {
    await ensurePgSchema();
    let statement = sql;
    let values = params;
    if (statement.includes("?") && !statement.includes("$1")) {
      let idx = 0;
      statement = statement.replace(/\?/g, () => `$${++idx}`);
    }
    const res = await getPgPool().query(statement, values);
    return { rows: res.rows, rowCount: res.rowCount };
  }
  const stmt = getSqlite().prepare(sql);
  if (/^\s*select/i.test(sql)) {
    return { rows: stmt.all(params), rowCount: undefined };
  }
  const result = stmt.run(params);
  return { rows: [], rowCount: result.changes };
}

async function one(sql, params = []) {
  const res = await query(sql, params);
  return res.rows[0] || null;
}

async function many(sql, params = []) {
  const res = await query(sql, params);
  return res.rows;
}

async function exec(sql, params = []) {
  return query(sql, params);
}

async function tableCount(table) {
  const row = await one(`SELECT COUNT(*) AS count FROM ${table}`);
  return Number(row?.count || 0);
}

async function getSettingsMap(keys = null) {
  if (keys && keys.length) {
    const qs = keys.map((_, i) => `$${i + 1}`).join(",");
    const rows = isPostgres()
      ? await many(`SELECT key, value FROM admin_settings WHERE key IN (${qs})`, keys)
      : await many(`SELECT key, value FROM admin_settings WHERE key IN (${keys.map(() => "?").join(",")})`, keys);
    return Object.fromEntries(rows.map((r) => [r.key, r.value]));
  }
  const rows = await many("SELECT key, value FROM admin_settings");
  return Object.fromEntries(rows.map((r) => [r.key, r.value]));
}

async function setSetting(key, value) {
  if (isPostgres()) {
    await exec(
      "INSERT INTO admin_settings(key, value) VALUES($1, $2) ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
      [key, String(value ?? "")]
    );
    return;
  }
  await exec(
    "INSERT INTO admin_settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
    [key, String(value ?? "")]
  );
}

async function getSetting(key, fallback = "") {
  const row = await one(isPostgres()
    ? "SELECT value FROM admin_settings WHERE key = $1"
    : "SELECT value FROM admin_settings WHERE key = ?",
  [key]);
  return row?.value ?? fallback;
}

module.exports = {
  dbUrl,
  isPostgres,
  sqlitePath,
  query,
  one,
  many,
  exec,
  tableCount,
  getSetting,
  setSetting,
  getSettingsMap,
};
