export const dynamic = "force-dynamic";

import { getBackupsSettings } from "../../../lib/admin-data";
import { getTestSubscriptionUsers } from "../../../lib/admin-data";
import Link from "next/link";
import { fmtDate } from "../../../lib/format";
import InboundPicker from "../../../components/inbound-picker";

export default async function TestSubPage() {
  const [settings, testUsers] = await Promise.all([
    getBackupsSettings(),
    getTestSubscriptionUsers(),
  ]);

  return (
    <div className="grid" style={{ gap: 16, maxWidth: 960 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Test Subscription</h2>
          <div className="muted">Enable or disable the free test plan and choose the inbound used for new test users.</div>
        </div>
      </div>

      <section className="section">
        <form action="/api/settings" method="post" className="grid" style={{ gap: 14 }}>
          <div className="form-grid">
            <div>
              <label>Test plan enabled</label>
              <select name="test_sub_enabled" defaultValue={settings.test_sub_enabled || "1"}>
                <option value="1">Enabled</option>
                <option value="0">Disabled</option>
              </select>
            </div>
            <div>
              <label>Test traffic GB</label>
              <input name="test_sub_traffic_gb" defaultValue={settings.test_sub_traffic_gb || "1"} />
            </div>
            <div>
              <label>Test duration days</label>
              <input name="test_sub_duration_days" defaultValue={settings.test_sub_duration_days || "1"} />
            </div>
            <div className="field-full">
              <label>Selected test inbound</label>
              <InboundPicker
                name="test_sub_inbound_id"
                initialValue={settings.test_sub_inbound_id || ""}
                label="Select test inbound"
                showSummary
                allowMultiple={false}
              />
              <div className="muted" style={{ marginTop: 8 }}>
                Leave it empty to let the bot auto-select an inbound.
              </div>
            </div>
          </div>
          <div className="panel" style={{ margin: 0 }}>
            <div className="muted">Current settings</div>
            <div style={{ marginTop: 6 }}>
              Test access: <strong>{String(settings.test_sub_enabled || "1") === "1" ? "enabled" : "disabled"}</strong>
              {" · "}Traffic: <strong>{settings.test_sub_traffic_gb || "1"} GB</strong>
              {" · "}Duration: <strong>{settings.test_sub_duration_days || "1"} days</strong>
            </div>
          </div>
          <button type="submit">Save test subscription settings</button>
        </form>
      </section>

      <section className="section">
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <div>
            <h2 style={{ margin: 0 }}>Users who already got the test sub</h2>
            <div className="muted">Anyone listed here has already used their one-time free test subscription.</div>
          </div>
          <div className="pill">{testUsers.length} users</div>
        </div>

        <div className="card-list">
          {testUsers.length ? testUsers.map((entry) => (
            <div key={entry.id} className="item">
              <div className="item-head">
                <div>
                  <h3 className="item-title wrap">
                    {entry.first_name || entry.username || `Telegram ${entry.telegram_id}`}
                  </h3>
                  <p className="item-sub wrap">
                    {entry.username ? `@${entry.username} · ` : ""}Telegram {entry.telegram_id}
                  </p>
                </div>
                {entry.user_id ? (
                  <Link className="btn ghost" href={`/admin/customers/${entry.user_id}`}>Open customer</Link>
                ) : (
                  <span className="pill">No user row</span>
                )}
              </div>

              <p className="item-sub wrap" style={{ marginTop: 8 }}>
                Test sub claimed: {fmtDate(entry.created_at)}
                {entry.user_created_at ? ` · User joined: ${fmtDate(entry.user_created_at)}` : ""}
                {Number(entry.subscription_count || 0) > 0 ? ` · ${entry.subscription_count} total subscriptions` : ""}
              </p>
            </div>
          )) : <div className="muted">No one has used the test subscription yet.</div>}
        </div>
      </section>
    </div>
  );
}
