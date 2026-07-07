export const dynamic = "force-dynamic";

import { getDiscounts } from "../../../lib/admin-data";
import { fmtDate } from "../../../lib/format";

export default async function DiscountsPage() {
  const discounts = await getDiscounts();

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Discounts</h2>
          <div className="muted">Create promo codes, toggle activity, and delete stale campaigns.</div>
        </div>
      </div>

      <section className="section">
        <form action="/api/discounts" method="post" className="panel" style={{ marginBottom: 16 }}>
          <input type="hidden" name="action" value="create" />
          <div className="form-grid">
            <div>
              <label>Code</label>
              <input name="code" placeholder="SUMMER20" required />
            </div>
            <div>
              <label>Percent</label>
              <input name="percent" type="number" min="1" max="100" defaultValue="10" required />
            </div>
            <div>
              <label>Max uses</label>
              <input name="max_uses" type="number" min="1" placeholder="Unlimited" />
            </div>
            <div>
              <label>Expire in days</label>
              <input name="expire_days" type="number" min="1" placeholder="Optional" />
            </div>
            <div className="field-full">
              <button type="submit">Create discount</button>
            </div>
          </div>
        </form>

        <div className="card-list">
          {discounts.map((discount) => {
            const expired = discount.expires_at && new Date(discount.expires_at).getTime() < Date.now();
            return (
              <div key={discount.id} className="item">
                <div className="item-head">
                  <div>
                    <h3 className="item-title">{discount.code}</h3>
                    <p className="item-sub">
                      {discount.percent}% off · used {discount.used_count}
                      {discount.max_uses ? ` / ${discount.max_uses}` : ""} · {fmtDate(discount.created_at)}
                    </p>
                    <p className={`item-sub ${expired ? "bad" : ""}`}>
                      {discount.expires_at ? `Expires: ${fmtDate(discount.expires_at)}` : "No expiry"}
                    </p>
                  </div>
                  <div className={`pill ${discount.is_active ? "ok" : "bad"}`}>{discount.is_active ? "active" : "inactive"}</div>
                </div>

                <div className="actions">
                  <form action="/api/discounts" method="post">
                    <input type="hidden" name="action" value="toggle" />
                    <input type="hidden" name="id" value={discount.id} />
                    <input type="hidden" name="is_active" value={discount.is_active ? "0" : "1"} />
                    <button type="submit" className="btn secondary">{discount.is_active ? "Disable" : "Enable"}</button>
                  </form>
                  <form action="/api/discounts" method="post">
                    <input type="hidden" name="action" value="delete" />
                    <input type="hidden" name="id" value={discount.id} />
                    <button type="submit" className="btn danger">Delete</button>
                  </form>
                </div>
              </div>
            );
          })}
          {discounts.length === 0 && <div className="muted">No discount codes yet.</div>}
        </div>
      </section>
    </div>
  );
}
