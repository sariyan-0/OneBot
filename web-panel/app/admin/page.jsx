export const dynamic = "force-dynamic";

import Link from "next/link";
import { Activity, ArrowRight, BadgeDollarSign, Blocks, CircleGauge, Server, Users } from "lucide-react";

import { getDashboardStats } from "../../lib/admin-data";
import { bytes, fmtDate, money } from "../../lib/format";

function Stat({ label, value, detail, Icon }) {
  return (
    <div className="stat">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <span className="muted">{label}</span>
        <Icon size={18} />
      </div>
      <strong>{value}</strong>
      <div className="muted">{detail}</div>
    </div>
  );
}

function PlanBar({ plan }) {
  const width = Math.min(100, Math.max(8, Number(plan.subscription_count || 0) * 14));
  return (
    <div className="item" style={{ display: "grid", gap: 10 }}>
      <div className="item-head">
        <div>
          <h3 className="item-title">{plan.name}</h3>
          <p className="item-sub">
            {plan.traffic_gb === 0 ? "Unlimited" : `${plan.traffic_gb} GB`} · {plan.duration_days} days · ${money(plan.price_usdt)}
          </p>
        </div>
        <span className={`pill ${plan.is_active ? "ok" : "bad"}`}>{plan.subscription_count || 0} subs</span>
      </div>
      <div style={{ height: 10, borderRadius: 999, background: "rgba(255,255,255,.06)", overflow: "hidden" }}>
        <div style={{ width: `${width}%`, height: "100%", borderRadius: 999, background: "linear-gradient(90deg, var(--accent), var(--accent-2))" }} />
      </div>
    </div>
  );
}

function isCardPayment(payment) {
  const method = String(payment?.payment_method || "").toLowerCase();
  return method === "card" || method.includes("card");
}

function amountSummary(payment) {
  if (isCardPayment(payment)) {
    const toman = Number(payment?.amount_rial || 0) > 0
      ? `${Number(payment.amount_rial / 10).toLocaleString()} toman`
      : "Card payment";
    return toman;
  }
  return `$${money(payment.amount_usdt)}`;
}

export default async function DashboardPage() {
  const stats = await getDashboardStats();
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Dashboard</h2>
          <div className="muted">Operational overview, activity feed, and quick access to the core surface.</div>
        </div>
        <div className="actions">
          <Link className="btn secondary" href="/admin/server"><Server size={16} /> Server</Link>
          <Link className="btn" href="/admin/customers"><Users size={16} /> Customers</Link>
        </div>
      </div>

      <div className="grid cards">
        <Stat label="Customers" value={stats.users} detail="Registered Telegram users" Icon={Users} />
        <Stat label="Subscriptions" value={stats.subscriptions} detail={`${stats.activeSubscriptions} active · ${stats.finishedSubscriptions} inactive`} Icon={Blocks} />
        <Stat label="Payments" value={stats.payments} detail={`${stats.pendingPayments} pending review`} Icon={BadgeDollarSign} />
        <Stat label="Revenue" value={`$${money(stats.revenue)}`} detail="Confirmed / finished payments" Icon={CircleGauge} />
      </div>

      <div className="two-col">
        <section className="section">
          <div className="toolbar">
            <div>
              <h2 style={{ margin: 0 }}>Live activity</h2>
              <div className="muted">Outgoing messages, incoming actions, and recent bot events.</div>
            </div>
            <Link className="btn ghost" href="/admin/activity">Open full feed <ArrowRight size={16} /></Link>
          </div>
          <div className="card-list">
            {stats.recentActivity.length ? stats.recentActivity.map((item) => (
              <div key={item.id} className="item">
                <div className="item-head">
                  <div>
                    <h3 className="item-title">{item.event_type.replaceAll("_", " ")}</h3>
                    <p className="item-sub">{item.text}</p>
                    <p className="item-sub">
                      By {item.user_first_name || item.user_username || item.username || "Unknown user"}
                      {" · "}
                      {item.user_id ? (
                        <Link href={`/admin/customers/${item.user_id}`}>ID {item.telegram_id}</Link>
                      ) : (
                        <span>ID {item.telegram_id || "unknown"}</span>
                      )}
                    </p>
                  </div>
                  <span className={`pill ${item.direction === "outgoing" ? "ok" : "warn"}`}>{item.direction}</span>
                </div>
              </div>
            )) : <div className="muted">No activity recorded yet.</div>}
          </div>
        </section>

        <section className="section">
          <div className="toolbar">
            <div>
              <h2 style={{ margin: 0 }}>Recent payments</h2>
              <div className="muted">Newest confirmed, pending, and card-to-card payments.</div>
            </div>
          </div>
          <div className="card-list">
            {stats.recentPayments.length ? stats.recentPayments.map((payment) => (
              <Link key={payment.id} href={`/admin/customers/${payment.user_id}`} className="item">
                <div className="item-head">
                  <div>
                    <h3 className="item-title">{payment.order_id}</h3>
                    <p className="item-sub">
                      {amountSummary(payment)} · {payment.payment_method} · {fmtDate(payment.created_at)}
                    </p>
                  </div>
                  <span className={`pill ${payment.status === "confirmed" || payment.status === "finished" ? "ok" : payment.status === "awaiting_review" ? "warn" : "bad"}`}>
                    {payment.status}
                  </span>
                </div>
              </Link>
            )) : <div className="muted">No payments recorded yet.</div>}
          </div>
        </section>
      </div>

      <div className="two-col">
        <section className="section">
          <div className="toolbar">
            <div>
              <h2 style={{ margin: 0 }}>Plan mix</h2>
              <div className="muted">Plans ordered by current subscription count.</div>
            </div>
            <Link className="btn ghost" href="/admin/plans">Open plans <ArrowRight size={16} /></Link>
          </div>
          <div className="card-list">
            {stats.planMix.length ? stats.planMix.map((plan) => <PlanBar key={plan.id} plan={plan} />) : <div className="muted">No plans available yet.</div>}
          </div>
        </section>

        <section className="section">
          <div className="toolbar">
            <div>
              <h2 style={{ margin: 0 }}>Quick links</h2>
              <div className="muted">Direct routes for routine admin work.</div>
            </div>
          </div>
          <div className="card-list">
            {[
              ["/admin/plans", "Plans", "Edit plan prices and inbound links"],
              ["/admin/inbounds", "Allowed inbounds", "Toggle which inbounds can be used"],
              ["/admin/payments", "Payments", "Review crypto and card-to-card invoices"],
              ["/admin/banners", "Banners", "Manage the general and welcome banners"],
              ["/admin/backups", "Backups", "Download or restore archives"],
              ["/admin/security", "Security", "Rotate panel credentials"],
            ].map(([href, title, desc]) => (
              <Link key={href} href={href} className="item">
                <div className="item-head">
                  <div>
                    <h3 className="item-title">{title}</h3>
                    <p className="item-sub">{desc}</p>
                  </div>
                  <ArrowRight size={18} />
                </div>
              </Link>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
