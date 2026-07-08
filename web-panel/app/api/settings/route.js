import fs from "fs/promises";
import path from "path";

import { setSetting } from "../../../lib/db";
import { redirectSeeOther } from "../../../lib/redirect";
import { getRootDir } from "../../../lib/root";

const KEYS = [
  "BOT_TOKEN",
  "BOT_USERNAME",
  "PANEL_URL",
  "PANEL_API_TOKEN",
  "PANEL_USERNAME",
  "PANEL_PASSWORD",
  "SUB_PORT",
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
  "usdt_to_toman_rate",
  "referral_commission_percent",
  "notice_warning_text",
  "NOWPAYMENTS_API_KEY",
  "NOWPAYMENTS_IPN_SECRET",
  "NOWPAYMENTS_IPN_URL",
  "NOWPAYMENTS_PAY_CURRENCY",
  "MAXELPAY_API_KEY",
  "MAXELPAY_WEBHOOK_SECRET",
  "MAXELPAY_WEBHOOK_URL",
];

export async function POST(request) {
  const form = await request.formData();
  for (const key of KEYS) {
    if (form.has(key)) {
      const value = String(form.get(key) ?? "");
      await setSetting(key, value);
      if (key === "BOT_TOKEN") {
        await setSetting("BOT_TOKEN_SOURCE", value.trim() ? "panel" : "");
      }
    }
  }
  await fs.writeFile(path.join(getRootDir(), ".onebot-restart"), String(Date.now()), "utf8");
  return redirectSeeOther(request, "/admin/settings");
}
