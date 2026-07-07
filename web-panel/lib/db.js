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

function getSqlite() {
  if (!sqliteDb) {
    const file = sqlitePath();
    fs.mkdirSync(path.dirname(file), { recursive: true });
    sqliteDb = new Database(file, { readonly: false, fileMustExist: false });
    sqliteDb.pragma("journal_mode = WAL");
    sqliteDb.exec(`
      CREATE TABLE IF NOT EXISTS admin_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
      )
    `);
  }
  if (!sqliteSchemaReady) {
    try {
      const columns = sqliteDb.prepare("PRAGMA table_info(users)").all().map((row) => row.name);
      if (!columns.includes("wallet_balance_toman")) {
        sqliteDb.exec("ALTER TABLE users ADD COLUMN wallet_balance_toman INTEGER NOT NULL DEFAULT 0");
      }
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
    await getPgPool().query("ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_balance_toman INTEGER NOT NULL DEFAULT 0");
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
