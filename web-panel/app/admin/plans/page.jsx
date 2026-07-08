export const dynamic = "force-dynamic";

import { getPlans } from "../../../lib/admin-data";
import { money, fmtDate } from "../../../lib/format";
import InboundPicker from "../../../components/inbound-picker";

export default async function PlansPage() {
  const plans = await getPlans();

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Plans</h2>
          <div className="muted">Editable pricing, duration, and inbound mapping.</div>
        </div>
      </div>

      <section className="section">
        <form action="/api/plans" method="post" className="panel" style={{ marginBottom: 16 }}>
          <input type="hidden" name="action" value="create" />
          <div className="form-grid">
            <div className="field-full">
              <label htmlFor="create-name">Plan name</label>
              <input id="create-name" name="name" placeholder="30 GB / 30 Days" required />
            </div>
            <div>
              <label htmlFor="create-price-usdt">Price USD</label>
              <input id="create-price-usdt" name="price_usdt" type="number" step="0.01" min="0" defaultValue="5" required />
            </div>
            <div>
              <label htmlFor="create-price-toman">Price Toman</label>
              <input id="create-price-toman" name="price_toman" type="number" min="0" defaultValue="0" />
            </div>
            <div>
              <label htmlFor="create-traffic">Traffic GB</label>
              <input id="create-traffic" name="traffic_gb" type="number" min="0" defaultValue="0" />
            </div>
            <div>
              <label htmlFor="create-days">Duration days</label>
              <input id="create-days" name="duration_days" type="number" min="1" defaultValue="30" />
            </div>
            <div className="field-full">
              <InboundPicker name="inbound_ids" label="All active inbounds" />
            </div>
            <div className="field-full">
              <button type="submit">Create plan</button>
            </div>
          </div>
        </form>

        <div className="card-list">
          {plans.map((plan) => (
            <div key={plan.id} className="item">
              <form action={`/api/plans/${plan.id}`} method="post" className="grid" style={{ gap: 12 }}>
                <div className="item-head">
                  <div>
                    <h3 className="item-title">{plan.name}</h3>
                    <p className="item-sub">
                      ${money(plan.price_usdt)}{plan.price_toman ? ` · ${Number(plan.price_toman).toLocaleString()} toman` : ""} · {plan.traffic_gb ? `${plan.traffic_gb} GB` : "Unlimited"} · {plan.duration_days} days
                    </p>
                    <p className="item-sub">Inbounds: {plan.inbound_ids || "All active inbounds"}</p>
                  </div>
                  <div className={`pill ${plan.is_active ? "ok" : "bad"}`}>{plan.is_active ? "active" : "inactive"}</div>
                </div>
                <input type="hidden" name="action" value="update" />
                <div className="form-grid">
                  <div className="field-full">
                    <label>Name</label>
                    <input name="name" defaultValue={plan.name} />
                  </div>
                  <div>
                    <label>USD</label>
                    <input name="price_usdt" type="number" step="0.01" defaultValue={plan.price_usdt} />
                  </div>
                  <div>
                    <label>Toman</label>
                    <input name="price_toman" type="number" defaultValue={plan.price_toman} />
                  </div>
                  <div>
                    <label>Traffic GB</label>
                    <input name="traffic_gb" type="number" defaultValue={plan.traffic_gb} />
                  </div>
                  <div>
                    <label>Days</label>
                    <input name="duration_days" type="number" defaultValue={plan.duration_days} />
                  </div>
                  <div className="field-full">
                    <InboundPicker
                      name="inbound_ids"
                      initialValue={plan.inbound_ids || ""}
                      label="All active inbounds"
                    />
                  </div>
                  <div>
                    <label>Active</label>
                    <select name="is_active" defaultValue={plan.is_active ? "1" : "0"}>
                      <option value="1">Enabled</option>
                      <option value="0">Disabled</option>
                    </select>
                  </div>
                  <div>
                    <label>Limit IP</label>
                    <input name="limit_ip" type="number" defaultValue={plan.limit_ip} />
                  </div>
                  <div className="field-full actions">
                    <button type="submit">Save changes</button>
                    <button type="submit" name="action" value="toggle" className="btn secondary">Toggle active</button>
                  </div>
                </div>
                <input type="hidden" name="sort_order" value={plan.sort_order} />
                <input type="hidden" name="plan_id" value={plan.id} />
              </form>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
