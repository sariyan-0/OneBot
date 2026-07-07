export const dynamic = "force-dynamic";

import Link from "next/link";
import { notFound } from "next/navigation";
import { getTicketById, getTicketsMessages } from "../../../../lib/admin-data";
import { fmtDate } from "../../../../lib/format";
import TicketThreadClient from "../../../../components/ticket-thread-client";

const STATUS_META = {
  open: { label: "Open", className: "pill bad" },
  in_progress: { label: "In progress", className: "pill warn" },
  closed: { label: "Closed", className: "pill ok" },
};

export default async function TicketDetailPage({ params }) {
  const ticketId = Number(params.id);
  if (!Number.isFinite(ticketId)) notFound();

  const ticket = await getTicketById(ticketId);
  if (!ticket) notFound();
  const messages = await getTicketsMessages(ticketId);
  const statusMeta = STATUS_META[ticket.status] || { label: ticket.status, className: "pill" };
  const owner = ticket.first_name || ticket.username || `Telegram ${ticket.telegram_id}`;
  const customerHref = ticket.user_id ? `/admin/customers/${ticket.user_id}` : null;
  const customerTag = ticket.username ? `@${ticket.username}` : `ID ${ticket.telegram_id}`;

  const thread = messages.map((msg, index) => ({
    ...msg,
    side: msg.is_admin_reply ? "right" : "left",
    kind: msg.is_admin_reply ? "support" : "customer",
    initials: msg.is_admin_reply ? "AD" : (ticket.first_name?.[0] || ticket.username?.[0] || "U").toUpperCase(),
    isFirst: index === 0,
    timeLabel: fmtDate(msg.created_at),
  }));

  return (
    <div className="ticket-page">
      <section className="ticket-shell section">
        <div className="ticket-shell-header">
          <div>
            <div className="ticket-kicker">Ticket #{ticket.id}</div>
            <h2 className="ticket-title">{ticket.subject}</h2>
            <div className="ticket-meta">
              {customerHref ? (
                <Link href={customerHref} className="ticket-customer-link">
                  <span>{owner}</span>
                  <span className="ticket-dot" />
                  <span>{customerTag}</span>
                </Link>
              ) : (
                <>
                  <span>{owner}</span>
                  <span className="ticket-dot" />
                  <span>{customerTag}</span>
                </>
              )}
              <span className="ticket-dot" />
              <span>{ticket.telegram_id}</span>
              <span className="ticket-dot" />
              <span>{fmtDate(ticket.created_at)}</span>
            </div>
          </div>
          <div className="ticket-hero-actions">
            <span className={statusMeta.className}>{statusMeta.label}</span>
            <Link href="/admin/tickets" className="btn secondary">Back to inbox</Link>
          </div>
        </div>

        <div className="ticket-shell-body">
          <TicketThreadClient
            ticketId={ticket.id}
            owner={owner}
            customerHref={customerHref}
            customerTag={customerTag}
            initialMessages={thread}
            initialStatus={ticket.status}
          />

          <aside className="ticket-side">
            <div className="section ticket-side-card">
              <div className="toolbar" style={{ marginBottom: 10 }}>
                <div>
                  <h3 style={{ margin: 0 }}>Customer</h3>
                  <div className="muted">Ownership and support context.</div>
                </div>
              </div>
              <div className="card-list">
                <div className="item">
                  <div className="item-head">
                    <div>
                      {customerHref ? (
                        <Link href={customerHref} className="ticket-customer-card-link">
                          <h4 className="item-title">{owner}</h4>
                          <p className="item-sub">{customerTag}</p>
                          <p className="item-sub">Telegram {ticket.telegram_id}</p>
                        </Link>
                      ) : (
                        <>
                          <h4 className="item-title">{owner}</h4>
                          <p className="item-sub">{customerTag}</p>
                          <p className="item-sub">Telegram {ticket.telegram_id}</p>
                        </>
                      )}
                    </div>
                  </div>
                </div>
                <div className="item">
                  <div className="item-head">
                    <div>
                      <h4 className="item-title">Timeline</h4>
                      <p className="item-sub">Created: {fmtDate(ticket.created_at)}</p>
                      <p className="item-sub">Updated: {fmtDate(ticket.updated_at)}</p>
                      <p className="item-sub">Closed: {fmtDate(ticket.closed_at)}</p>
                    </div>
                  </div>
                </div>
                <div className="item">
                  <div className="item-head">
                    <div>
                      <h4 className="item-title">Status</h4>
                      <p className="item-sub">Current state of the thread.</p>
                    </div>
                    <span className={statusMeta.className}>{statusMeta.label}</span>
                  </div>
                </div>
              </div>
            </div>
          </aside>
        </div>
      </section>
    </div>
  );
}
