export const dynamic = "force-dynamic";

import { getInbounds } from "../../../lib/xui";
import { getSetting } from "../../../lib/db";

function isSelected(list, id) {
  return list.includes(String(id));
}

export default async function InboundsPage() {
  const inbounds = await getInbounds().catch(() => []);
  const raw = await getSetting("enabled_inbound_ids", "").catch(() => "");
  const selected = String(raw || "").split(",").map((x) => x.trim()).filter(Boolean);

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Allowed inbounds</h2>
          <div className="muted">Choose which inbounds can be linked to plans and future subscriptions.</div>
        </div>
      </div>

      <section className="section">
        <form action="/api/inbounds" method="post" className="grid" style={{ gap: 14 }}>
          <div className="card-list">
            {inbounds.map((inbound) => (
              <label key={inbound.id} className="item" style={{ display: "grid", gap: 8, cursor: "pointer" }}>
                <div className="item-head">
                  <div>
                    <h3 className="item-title">#{inbound.id} · {inbound.remark || "Unnamed inbound"}</h3>
                    <p className="item-sub">
                      {inbound.protocol} · port {inbound.port} · {inbound.enable ? "enabled" : "disabled"}
                    </p>
                  </div>
                  <input
                    type="checkbox"
                    name="inbound_ids"
                    value={inbound.id}
                    defaultChecked={isSelected(selected, inbound.id)}
                    style={{ width: 20, height: 20, minHeight: 20 }}
                  />
                </div>
              </label>
            ))}
          </div>
          <div className="actions">
            <button type="submit">Sync selection</button>
            <div className="muted">This saves to the shared settings store and the bot will use it on the next plan sync.</div>
          </div>
        </form>
      </section>
    </div>
  );
}
