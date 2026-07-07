export const dynamic = "force-dynamic";

import Link from "next/link";

import { getRecentActivity } from "../../../lib/admin-data";
import { fmtDate } from "../../../lib/format";

export default async function ActivityPage() {
  const recentActivity = await getRecentActivity(100);

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Live activity</h2>
          <div className="muted">Text sent by the bot and operational actions written to the activity log.</div>
        </div>
      </div>

      <section className="section">
        <div className="card-list">
          {recentActivity.map((item) => (
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
                  <p className="item-sub">{fmtDate(item.created_at)}</p>
                </div>
                <span className={`pill ${item.direction === "outgoing" ? "ok" : "warn"}`}>{item.direction}</span>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
