export const dynamic = "force-dynamic";

import Link from "next/link";
import { getCustomers } from "../../../lib/admin-data";
import { fmtDate, integer, money } from "../../../lib/format";

export default async function CustomersPage({ searchParams }) {
  const query = String(searchParams?.q || "").trim();
  const customers = await getCustomers(250, query);

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Customers</h2>
          <div className="muted">Clickable customer records with wallet and subscription context.</div>
        </div>
      </div>

      <section className="section">
        <form method="get" className="toolbar" style={{ marginBottom: 12, alignItems: "end" }}>
          <div style={{ flex: 1, minWidth: 240 }}>
            <label htmlFor="customers-search">Search</label>
            <input
              id="customers-search"
              name="q"
              defaultValue={query}
              placeholder="Telegram ID, username, or name..."
            />
          </div>
          <div className="actions">
            <button type="submit">Search</button>
            {query ? <Link href="/admin/customers" className="btn secondary">Clear</Link> : null}
          </div>
        </form>

        <table>
          <thead>
            <tr>
              <th>Customer</th>
              <th>Status</th>
              <th>Wallet</th>
              <th>Subs</th>
              <th>Payments</th>
              <th>Joined</th>
            </tr>
          </thead>
          <tbody>
            {customers.map((user) => (
              <tr key={user.id}>
                <td>
                  <Link href={`/admin/customers/${user.id}`} className="wrap">
                    <strong>{user.first_name || user.username || `User ${user.telegram_id}`}</strong>
                    <div className="muted">@{user.username || "unknown"} · {user.telegram_id}</div>
                  </Link>
                </td>
                <td>
                  <span className={`pill ${user.is_blocked ? "bad" : "ok"}`}>
                    {user.is_blocked ? "Blocked" : "Active"}
                  </span>
                </td>
                <td>
                  <div>${money(user.wallet_balance_usdt)}</div>
                  <div>{integer(user.wallet_balance_toman || 0)} toman</div>
                </td>
                <td>
                  <div>{user.subscription_count} total</div>
                  <div className="muted">{user.active_subscriptions} active</div>
                </td>
                <td>${money(user.paid_total)}</td>
                <td>{fmtDate(user.created_at)}</td>
              </tr>
            ))}
            {!customers.length ? (
              <tr>
                <td colSpan={6}>
                  <div className="muted" style={{ padding: "12px 0" }}>
                    {query ? `No customers found for "${query}".` : "No customers found."}
                  </div>
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>
    </div>
  );
}
