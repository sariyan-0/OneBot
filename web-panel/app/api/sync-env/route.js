import { NextResponse } from "next/server";

import { getSetting, setSetting } from "../../../lib/db";
import { getRootDir } from "../../../lib/root";

const SYNC_KEYS = [
  "BOT_TOKEN",
  "BOT_USERNAME",
  "PANEL_URL",
  "PANEL_API_TOKEN",
  "PANEL_USERNAME",
  "PANEL_PASSWORD",
  "SUB_PORT",
  "WEB_ADMIN_USERNAME",
  "WEB_ADMIN_PASSWORD",
  "WEB_ADMIN_ENABLED",
  "WEB_ADMIN_COOKIE_SECRET",
  "WEBHOOK_PORT",
  "NOWPAYMENTS_API_KEY",
  "NOWPAYMENTS_IPN_URL",
  "MAXELPAY_API_KEY",
  "MAXELPAY_WEBHOOK_SECRET",
  "DOMAIN",
  "WEB_PANEL_DOMAIN",
];

async function getDbValues(keys) {
  const values = {};
  for (const key of keys) {
    values[key] = await getSetting(key, "");
  }
  return values;
}

export async function GET() {
  const envValues = Object.fromEntries(SYNC_KEYS.map((key) => [key, String(process.env[key] || "").trim()]));
  const dbValues = await getDbValues(SYNC_KEYS);
  const outOfSync = SYNC_KEYS.filter((key) => String(envValues[key] || "") !== String(dbValues[key] || ""));

  return NextResponse.json({
    envValues,
    dbValues,
    outOfSync,
    inSync: outOfSync.length === 0,
    rootDir: getRootDir(),
  });
}

export async function POST(request) {
  const body = await request.json().catch(() => ({}));
  const force = Boolean(body.force);
  const envValues = Object.fromEntries(SYNC_KEYS.map((key) => [key, String(process.env[key] || "").trim()]));
  const dbValues = await getDbValues(SYNC_KEYS);

  const synced = [];
  for (const key of SYNC_KEYS) {
    const value = envValues[key];
    if (!value) continue;
    if (!force && String(dbValues[key] || "") === value) continue;
    await setSetting(key, value);
    if (key === "BOT_TOKEN") {
      await setSetting("BOT_TOKEN_SOURCE", "env");
    }
    synced.push(key);
  }

  return NextResponse.json({
    ok: true,
    message: synced.length ? "Environment values imported into the database." : "No import needed.",
    synced,
  });
}
