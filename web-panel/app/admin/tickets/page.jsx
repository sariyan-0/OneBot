export const dynamic = "force-dynamic";

import Link from "next/link";
import { getTickets } from "../../../lib/admin-data";
import { fmtDate } from "../../../lib/format";

const TABS = [
  { key: "all", label: "All" },
  { key: "open", label: "Open" },
  { key: "in_progress", label: "In progress" },
  { key: "closed", label: "Closed" },
];

const STATUS_META = {
  open: { label: "Open", className: "pill bad", dot: "●" },
  in_progress: { label: "In progress", className: "pill warn", dot: "●" },
  closed: { label: "Closed", className: "pill ok", dot: "●" },
};

function TicketRow({ ticket }) {
  const meta = STATUS_META[ticket.status] || { label: ticket.status, className: "pill", dot: "●" };
  const title = ticket.subject || `Ticket #${ticket.id}`;
  const owner = ticket.first_name || ticket.username || `Telegram ${ticket.telegram_id}`;
  return (
    <Link href={`/admin/tickets/${ticket.id}`} className="item ticket-row">
      <div className="item-head">
        <div>
          <h3 className="item-title">#{ticket.id} {title}</h3>
          <p className="item-sub">
            {owner} · {fmtDate(ticket.updated_at || ticket.created_at)}
          </p>
        </div>
        <span className={meta.className}>{meta.dot} {meta.label}</span>
      </div>
      <div className="ticket-row-foot">
        <span className="muted">Opened {fmtDate(ticket.created_at)}</span>
        <span className="ticket-link">Open thread</span>
      </div>
    </Link>
  );
}

export default async function TicketsPage({ searchParams }) {
  const status = String(searchParams?.status || "all");
  const tickets = await getTickets(200);
  const counts = tickets.reduce((acc, ticket) => {
    acc.all += 1;
    acc[ticket.status] = (acc[ticket.status] || 0) + 1;
    return acc;
  }, { all: 0, open: 0, in_progress: 0, closed: 0 });
  const visible = status === "all" ? tickets : tickets.filter((ticket) => ticket.status === status);

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Tickets</h2>
          <div className="muted">A live support inbox with thread details, status filters, and direct replies.</div>
        </div>
      </div>

      <div className="ticket-tabs">
        {TABS.map((tab) => {
          const active = tab.key === status;
          return (
            <Link
              key={tab.key}
              href={tab.key === "all" ? "/admin/tickets" : `/admin/tickets?status=${tab.key}`}
              className={`ticket-tab${active ? " active" : ""}`}
            >
              <span>{tab.label}</span>
              <strong>{counts[tab.key] || 0}</strong>
            </Link>
          );
        })}
      </div>

      <div className="ticket-list-shell">
        <div className="ticket-list">
          {visible.map((ticket) => (
            <TicketRow key={ticket.id} ticket={ticket} />
          ))}
          {!visible.length ? <div className="muted">No tickets in this filter.</div> : null}
        </div>

        <div className="ticket-summary section">
          <div className="toolbar" style={{ marginBottom: 10 }}>
            <div>
              <h2 style={{ margin: 0 }}>Queue overview</h2>
              <div className="muted">Quick glance before opening a thread.</div>
            </div>
          </div>
          <div className="card-list">
            <div className="item">
              <div className="item-head">
                <div>
                  <h3 className="item-title">Open</h3>
                  <p className="item-sub">Waiting for an admin reply.</p>
                </div>
                <span className="pill bad">{counts.open}</span>
              </div>
            </div>
            <div className="item">
              <div className="item-head">
                <div>
                  <h3 className="item-title">In progress</h3>
                  <p className="item-sub">Already touched by support.</p>
                </div>
                <span className="pill warn">{counts.in_progress}</span>
              </div>
            </div>
            <div className="item">
              <div className="item-head">
                <div>
                  <h3 className="item-title">Closed</h3>
                  <p className="item-sub">Resolved or archived by the team.</p>
                </div>
                <span className="pill ok">{counts.closed}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
