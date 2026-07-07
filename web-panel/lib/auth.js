const crypto = require("crypto");

const { getSetting } = require("./db");

const COOKIE_NAME = "onebot_admin";
const COOKIE_FALLBACK_NAME = "onebot_admin_public";

async function getAuthConfig() {
  const [dbUsername, dbPassword, dbSecret] = await Promise.all([
    getSetting("WEB_ADMIN_USERNAME", ""),
    getSetting("WEB_ADMIN_PASSWORD", ""),
    getSetting("WEB_ADMIN_COOKIE_SECRET", ""),
  ]);
  return {
    username: String(dbUsername || process.env.WEB_ADMIN_USERNAME || "admin").trim(),
    password: String(dbPassword || process.env.WEB_ADMIN_PASSWORD || "admin").trim(),
    cookieSecret: String(
      dbSecret ||
      process.env.WEB_ADMIN_COOKIE_SECRET ||
      process.env.ADMIN_SECRET ||
      process.env.BOT_TOKEN ||
      "onebot-web-admin"
    ).trim(),
  };
}

async function cookieSecret() {
  const cfg = await getAuthConfig();
  return cfg.cookieSecret;
}

async function sign(value) {
  return crypto.createHmac("sha256", await cookieSecret()).update(value).digest("hex");
}

async function makeCookie(username) {
  const value = `${username}:${Math.floor(Date.now() / 1000)}`;
  return `${value}:${await sign(value)}`;
}

async function verifyCookie(raw) {
  if (!raw || typeof raw !== "string") return false;
  let valueToVerify = raw;
  try {
    valueToVerify = decodeURIComponent(raw);
  } catch {
    valueToVerify = raw;
  }

  const idx = valueToVerify.lastIndexOf(":");
  if (idx <= 0) return false;
  const value = valueToVerify.slice(0, idx);
  const sig = valueToVerify.slice(idx + 1);
  const expected = Buffer.from(await sign(value));
  const actual = Buffer.from(sig);
  return expected.length === actual.length && crypto.timingSafeEqual(expected, actual);
}

async function isAdminAuth(cookies) {
  const primary = cookies?.get(COOKIE_NAME)?.value || "";
  const fallback = cookies?.get(COOKIE_FALLBACK_NAME)?.value || "";
  return (await verifyCookie(primary)) || (await verifyCookie(fallback));
}

module.exports = { COOKIE_NAME, COOKIE_FALLBACK_NAME, getAuthConfig, makeCookie, verifyCookie, isAdminAuth };
