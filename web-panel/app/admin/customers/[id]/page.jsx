export const dynamic = "force-dynamic";

import Link from "next/link";
import { notFound } from "next/navigation";
import {
  getCustomerActivity,
  getCustomerById,
  getCustomerPayments,
  getCustomerReferralSummary,
  getCustomerSubscriptions,
  getPlans,
} from "../../../../lib/admin-data";
import { fmtDate, integer, money } from "../../../../lib/format";
import CustomerSubscriptionManager from "../../../../components/customer-subscription-manager";
import CustomerPaymentsPanel from "../../../../components/customer-payments-panel";

export default async function CustomerDetailPage({ params }) {
  const user = await getCustomerById(params.id);
  if (!user) notFound();
  const subscriptions = await getCustomerSubscriptions(user.id);
  const payments = await getCustomerPayments(user.id);
  const activity = await getCustomerActivity(user.telegram_id, 24);
  const referral = await getCustomerReferralSummary(user.id);
  const plans = await getPlans();

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>{user.first_name || user.username || `User ${user.telegram_id}`}</h2>
          <div className="muted">@{user.username || "unknown"} · Telegram {user.telegram_id}</div>
        </div>
        <div className="actions">
          <Link href="/admin/customers" className="btn secondary">Back</Link>
        </div>
      </div>

      <div className="grid cards">
        <div className="stat">
          <span className="muted">Wallet USD</span>
          <strong>${money(user.wallet_balance_usdt)}</strong>
          <div className="muted">Stored separately from toman</div>
        </div>
        <div className="stat">
          <span className="muted">Wallet Toman</span>
          <strong>{integer(user.wallet_balance_toman || 0)} toman</strong>
          <div className="muted">Stored separately from USD</div>
        </div>
        <div className="stat">
          <span className="muted">Joined</span>
          <strong style={{ fontSize: 22 }}>{fmtDate(user.created_at)}</strong>
          <div className="muted">Updated {fmtDate(user.updated_at)}</div>
        </div>
        <div className="stat">
          <span className="muted">Access</span>
          <strong style={{ fontSize: 22 }}>{user.is_admin ? "Admin" : "Customer"}</strong>
          <div style={{ marginTop: 8 }}>
            <span className={`pill ${user.is_blocked ? "bad" : "ok"}`}>
              {user.is_blocked ? "Blocked" : "Active"}
            </span>
          </div>
        </div>
        <div className="stat">
          <span className="muted">Referral</span>
          <strong style={{ fontSize: 22 }}>{user.referral_code || "No code yet"}</strong>
          <div className="muted">
            {user.referred_by
              ? `Joined from ${user.referrer_first_name || user.referrer_username || `User ${user.referrer_telegram_id}`}`
              : "No referrer linked"}
          </div>
        </div>
      </div>

      <div className="two-col">
        <section className="section">
          <div className="toolbar">
            <div>
              <h2 style={{ margin: 0 }}>Subscriptions</h2>
              <div className="muted">Manage plan-based adds, manual imports, and removals from one place.</div>
            </div>
          </div>
          <CustomerSubscriptionManager
            userId={user.id}
            telegramId={user.telegram_id}
            plans={plans}
            subscriptions={subscriptions}
          />
        </section>

        <section className="section">
          <div className="toolbar" style={{ marginBottom: 12 }}>
            <div>
              <h2 style={{ margin: 0 }}>Customer actions</h2>
              <div className="muted">Manage access, wallet balance, and direct messages from one place.</div>
            </div>
          </div>

          <div className="card-list" style={{ marginBottom: 16 }}>
            <form action={`/api/customers/${user.id}`} method="post" className="item grid" style={{ gap: 12 }}>
              <input type="hidden" name="action" value="toggle_block" />
              <input type="hidden" name="telegram_id" value={user.telegram_id} />
              <button type="submit" className={`btn ${user.is_blocked ? "secondary" : "danger"}`}>
                {user.is_blocked ? "Unblock customer" : "Block customer"}
              </button>
            </form>

            <form action={`/api/customers/${user.id}`} method="post" className="item grid" style={{ gap: 12 }}>
              <input type="hidden" name="action" value="toggle_admin" />
              <input type="hidden" name="value" value={user.is_admin ? "0" : "1"} />
              <button type="submit" className="btn secondary">{user.is_admin ? "Remove admin" : "Make admin"}</button>
            </form>

            <form action={`/api/customers/${user.id}`} method="post" className="item grid" style={{ gap: 12 }}>
              <input type="hidden" name="action" value="wallet_adjust" />
              <div className="form-grid">
                <div>
                  <label>Mode</label>
                  <select name="mode" defaultValue="credit">
                    <option value="credit">Add funds</option>
                    <option value="debit">Remove funds</option>
                  </select>
                </div>
                <div>
                  <label>Amount USD</label>
                  <input name="amount" type="number" step="0.01" min="0" placeholder="5.00" />
                </div>
              </div>
              <div className="muted">This changes only the USD wallet.</div>
              <button type="submit">Apply USD wallet change</button>
            </form>

            <form action={`/api/customers/${user.id}`} method="post" className="item grid" style={{ gap: 12 }}>
              <input type="hidden" name="action" value="wallet_adjust_toman" />
              <div className="form-grid">
                <div>
                  <label>Mode</label>
                  <select name="mode" defaultValue="credit">
                    <option value="credit">Add funds</option>
                    <option value="debit">Remove funds</option>
                  </select>
                </div>
                <div>
                  <label>Amount Toman</label>
                  <input name="amount" type="number" step="1" min="0" placeholder="250000" />
                </div>
              </div>
              <div className="muted">This changes only the Toman wallet.</div>
              <button type="submit">Apply Toman wallet change</button>
            </form>

            <form action={`/api/customers/${user.id}`} method="post" className="item grid" style={{ gap: 12 }}>
              <input type="hidden" name="action" value="direct_message" />
              <div className="field-full">
                <label>Direct message</label>
                <textarea name="message" placeholder="Write a private message to this customer..." rows={5} required />
              </div>
              <button type="submit">Send direct message</button>
            </form>

            <form action={`/api/customers/${user.id}`} method="post" className="item grid" style={{ gap: 12 }}>
              <input type="hidden" name="action" value="delete_user" />
              <button type="submit" className="btn danger">Delete customer</button>
            </form>
          </div>

          <CustomerPaymentsPanel payments={payments} />
        </section>
      </div>

      <section className="section">
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <div>
            <h2 style={{ margin: 0 }}>Referral overview</h2>
            <div className="muted">Who brought this customer in, and what they have earned from inviting others.</div>
          </div>
        </div>

        <div className="grid cards">
          <div className="stat">
            <span className="muted">Referrals made</span>
            <strong>{referral.total_referrals}</strong>
            <div className="muted">{referral.converted_referrals} converted purchases</div>
          </div>
          <div className="stat">
            <span className="muted">Referral earnings</span>
            <strong>${money(referral.earned_usdt)}</strong>
            <div className="muted">{integer(referral.earned_toman)} Toman</div>
          </div>
          <div className="stat">
            <span className="muted">Linked referrer</span>
            <strong style={{ fontSize: 20 }}>
              {referral.linked_referrer
                ? (referral.linked_referrer.referrer_first_name || referral.linked_referrer.referrer_username || `User ${referral.linked_referrer.referrer_telegram_id}`)
                : "None"}
            </strong>
            <div className="muted">
              {referral.linked_referrer
                ? `Telegram ${referral.linked_referrer.referrer_telegram_id} · ${fmtDate(referral.linked_referrer.created_at)}`
                : "This customer has not used a referral link yet"}
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <div>
            <h2 style={{ margin: 0 }}>Recent activity</h2>
            <div className="muted">Incoming messages, outgoing replies, and admin direct messages.</div>
          </div>
        </div>

        <div className="card-list">
          {activity.map((entry) => (
            <div key={entry.id} className="item">
              <div className="item-head">
                <div>
                  <h3 className="item-title">
                    {entry.direction === "incoming" ? "Incoming" : "Outgoing"} · {entry.event_type}
                  </h3>
                  <p className="item-sub">
                    {fmtDate(entry.created_at)} · {entry.username ? `@${entry.username}` : `Telegram ${user.telegram_id}`}
                  </p>
                </div>
                <span className={`pill ${entry.direction === "incoming" ? "warn" : "ok"}`}>
                  {entry.direction}
                </span>
              </div>
              <p className="item-sub" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>
                {entry.text || "(empty)"}
              </p>
            </div>
          ))}
          {!activity.length ? <div className="muted">No activity recorded yet.</div> : null}
        </div>
      </section>
    </div>
  );
}
