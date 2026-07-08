export const dynamic = "force-dynamic";

import { getBackupsSettings } from "../../../lib/admin-data";
import XuiConnectionTest from "../../../components/xui-connection-test";
import { BadgeDollarSign, Bot, Database, ShieldCheck, SlidersHorizontal } from "lucide-react";

function Section({ title, description, icon: Icon, children }) {
  return (
    <section className="section" style={{ display: "grid", gap: 14 }}>
      <div className="toolbar" style={{ marginBottom: 0 }}>
        <div>
          <h3 style={{ margin: 0, display: "flex", gap: 10, alignItems: "center" }}>
            {Icon ? <Icon size={18} /> : null}
            {title}
          </h3>
          <div className="muted">{description}</div>
        </div>
      </div>
      {children}
    </section>
  );
}

export default async function SettingsPage() {
  const settings = await getBackupsSettings();
  const panelUrl = settings.PANEL_URL || "";
  const hasToken = Boolean((settings.PANEL_API_TOKEN || "").trim());
  const cryptoGateway = settings.crypto_gateway || "nowpayments";
  const cryptoInvoiceMode = String(settings.payment_crypto_invoice || "0") === "1";
  const showNowPaymentsDirectCurrency = cryptoGateway === "nowpayments" && !cryptoInvoiceMode;

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Settings</h2>
          <div className="muted">3X-UI connection, payment toggles, test plan, and notice text.</div>
        </div>
      </div>

      <Section
        title="Telegram bot"
        description="Save the bot token here so the web panel and Python worker restart with the intended Telegram bot. The username is detected from the token."
        icon={Bot}
      >
        <form action="/api/settings" method="post" className="grid" style={{ gap: 14 }}>
          <div className="form-grid">
            <div className="field-full">
              <label>Bot token</label>
              <input
                name="BOT_TOKEN"
                defaultValue={settings.BOT_TOKEN || ""}
                placeholder="123456:ABC-DEF..."
              />
            </div>
            <div className="field-full">
              <label>Bot username (optional)</label>
              <input
                name="BOT_USERNAME"
                defaultValue={settings.BOT_USERNAME || ""}
                placeholder="auto-detected from token"
              />
            </div>
          </div>
          <div className="notice" style={{ display: "grid", gap: 6 }}>
            <strong>Restart behavior</strong>
            <div className="muted">Saving the token updates the database, marks it as a panel override, and writes a restart marker.</div>
          </div>
          <button type="submit">Save bot token</button>
        </form>
      </Section>

      <div className="two-col">
        <Section
          title="3X-UI connection"
          description="Set the panel URL and API credentials here. Token auth is preferred; username/password stay available for legacy panels."
          icon={Database}
        >
          <form action="/api/settings" method="post" className="grid" style={{ gap: 14 }}>
            <div className="form-grid">
              <div className="field-full">
                <label>Panel URL</label>
                <input
                  name="PANEL_URL"
                  defaultValue={panelUrl}
                  placeholder="https://your-host:2053/your-path"
                />
              </div>
              <div>
                <label>API token</label>
                <input
                  name="PANEL_API_TOKEN"
                  defaultValue={settings.PANEL_API_TOKEN || ""}
                  placeholder="Bearer token from 3X-UI"
                />
              </div>
              <div>
                <label>Username</label>
                <input name="PANEL_USERNAME" defaultValue={settings.PANEL_USERNAME || ""} placeholder="admin" />
              </div>
              <div>
                <label>Password</label>
                <input
                  name="PANEL_PASSWORD"
                  type="password"
                  defaultValue={settings.PANEL_PASSWORD || ""}
                  placeholder="Panel password"
                />
              </div>
              <div>
                <label>Sub port</label>
                <input name="SUB_PORT" defaultValue={settings.SUB_PORT || ""} placeholder="2096" />
              </div>
            </div>

            <div className="notice" style={{ display: "grid", gap: 6 }}>
              <strong>Current auth mode</strong>
              <div className="muted">{hasToken ? "Bearer token active" : "Legacy login fields only"}</div>
              <div className="muted">Save these values and the panel will use them immediately.</div>
            </div>

            <button type="submit">Save connection</button>
          </form>
        </Section>

        <Section
          title="Validation"
          description="Quickly verify the configured API is reachable before you save or launch jobs."
          icon={ShieldCheck}
        >
          <XuiConnectionTest />
        </Section>
      </div>

      <Section
        title="Bot controls"
        description="Payment toggles, test plan controls, and the notice message shown to customers."
        icon={SlidersHorizontal}
      >
        <form action="/api/settings" method="post" className="grid" style={{ gap: 14 }}>
          <div className="form-grid">
            <div>
              <label>Crypto payments</label>
              <select name="payment_crypto_enabled" defaultValue={settings.payment_crypto_enabled || "1"}>
                <option value="1">Enabled</option>
                <option value="0">Disabled</option>
              </select>
            </div>
            <div>
              <label>Card payments</label>
              <select name="payment_card_enabled" defaultValue={settings.payment_card_enabled || "0"}>
                <option value="1">Enabled</option>
                <option value="0">Disabled</option>
              </select>
            </div>
            <div>
              <label>Crypto invoice mode</label>
              <select name="payment_crypto_invoice" defaultValue={settings.payment_crypto_invoice || "0"}>
                <option value="1">Invoice page</option>
                <option value="0">Direct coin</option>
              </select>
            </div>
            <div>
              <label>Crypto gateway</label>
              <select name="crypto_gateway" defaultValue={settings.crypto_gateway || "nowpayments"}>
                <option value="nowpayments">NOWPayments</option>
                <option value="maxelpay">MaxelPay</option>
              </select>
            </div>
            <div>
              <label>Expiry notices</label>
              <select name="notification_expiry_enabled" defaultValue={settings.notification_expiry_enabled || "1"}>
                <option value="1">Enabled</option>
                <option value="0">Disabled</option>
              </select>
            </div>
            <div>
              <label>Traffic notices</label>
              <select name="notification_traffic_enabled" defaultValue={settings.notification_traffic_enabled || "1"}>
                <option value="1">Enabled</option>
                <option value="0">Disabled</option>
              </select>
            </div>
            <div>
              <label>USD to toman rate</label>
              <input name="usdt_to_toman_rate" defaultValue={settings.usdt_to_toman_rate || ""} />
            </div>
            <div>
              <label>Referral commission %</label>
              <input name="referral_commission_percent" defaultValue={settings.referral_commission_percent || "10"} />
            </div>
            <div>
              <label>Card number</label>
              <input name="card_number" defaultValue={settings.card_number || ""} placeholder="6037 1234 5678 9012" />
            </div>
            <div>
              <label>Card holder</label>
              <input name="card_holder" defaultValue={settings.card_holder || ""} placeholder="Account holder name" />
            </div>
            <div className="field-full">
              <label>Warning text</label>
              <textarea
                name="notice_warning_text"
                defaultValue={settings.notice_warning_text || ""}
                placeholder="Message shown when the user is near expiry or over usage."
              />
            </div>
          </div>
          <div className="panel" style={{ margin: 0 }}>
            <div className="muted">Test plan controls live on the dedicated Test Sub page now.</div>
          </div>
          <button type="submit">Save settings</button>
        </form>
      </Section>

      <div className="two-col">
        <Section
          title="NOWPayments"
          description="Configure NOWPayments keys and callback URL. The fixed pay coin only applies to direct coin mode."
          icon={BadgeDollarSign}
        >
          <form action="/api/settings" method="post" className="grid" style={{ gap: 14 }}>
            <div className="form-grid">
              <div>
                <label>API key</label>
                <input name="NOWPAYMENTS_API_KEY" defaultValue={settings.NOWPAYMENTS_API_KEY || ""} placeholder="NOWPayments API key" />
              </div>
              <div>
                <label>IPN secret</label>
                <input
                  name="NOWPAYMENTS_IPN_SECRET"
                  defaultValue={settings.NOWPAYMENTS_IPN_SECRET || ""}
                  placeholder="IPN secret"
                />
              </div>
              <div>
                <label>Callback URL</label>
                <input
                  name="NOWPAYMENTS_IPN_URL"
                  defaultValue={settings.NOWPAYMENTS_IPN_URL || ""}
                  placeholder="https://your-domain.com/webhook/nowpayments"
                />
              </div>
              {showNowPaymentsDirectCurrency ? (
                <div>
                  <label>Direct pay coin</label>
                  <select name="NOWPAYMENTS_PAY_CURRENCY" defaultValue={settings.NOWPAYMENTS_PAY_CURRENCY || "usdttrc20"}>
                    <option value="usdttrc20">USDT TRC-20</option>
                    <option value="usdtbep20">USDT BEP-20</option>
                    <option value="usdterc20">USDT ERC-20</option>
                    <option value="btc">BTC</option>
                    <option value="eth">ETH</option>
                    <option value="trx">TRX</option>
                  </select>
                </div>
              ) : null}
            </div>
            {!showNowPaymentsDirectCurrency ? (
              <div className="notice" style={{ display: "grid", gap: 6 }}>
                <strong>Direct pay coin is not used</strong>
                <div className="muted">
                  {cryptoGateway === "maxelpay"
                    ? "MaxelPay is the selected crypto gateway, so NOWPayments coin settings are ignored."
                    : "NOWPayments invoice mode lets the customer choose the payment currency on the hosted invoice page."}
                </div>
              </div>
            ) : null}
            <button type="submit">Save NOWPayments</button>
          </form>
        </Section>

        <Section
          title="MaxelPay"
          description="Set the MaxelPay API key, webhook secret, and public callback URL."
          icon={BadgeDollarSign}
        >
          <form action="/api/settings" method="post" className="grid" style={{ gap: 14 }}>
            <div className="form-grid">
              <div>
                <label>API key</label>
                <input name="MAXELPAY_API_KEY" defaultValue={settings.MAXELPAY_API_KEY || ""} placeholder="MaxelPay API key" />
              </div>
              <div>
                <label>Webhook secret</label>
                <input
                  name="MAXELPAY_WEBHOOK_SECRET"
                  defaultValue={settings.MAXELPAY_WEBHOOK_SECRET || ""}
                  placeholder="Webhook secret"
                />
              </div>
              <div className="field-full">
                <label>Webhook URL</label>
                <input
                  name="MAXELPAY_WEBHOOK_URL"
                  defaultValue={settings.MAXELPAY_WEBHOOK_URL || ""}
                  placeholder="https://your-domain.com/webhook/maxelpay"
                />
              </div>
            </div>
            <button type="submit">Save MaxelPay</button>
          </form>
        </Section>
      </div>
    </div>
  );
}
