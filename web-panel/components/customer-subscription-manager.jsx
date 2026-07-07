"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Link2, Plus, Trash2 } from "lucide-react";

async function jsonRequest(url, init) {
  const res = await fetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

function getSubscriptionIdentity(sub) {
  const email = String(sub?.email || "");
  const subId = String(sub?.sub_id || sub?.client_uuid || `#${sub?.id ?? ""}`);
  if (email.endsWith("@import.local")) {
    return subId;
  }
  return email ? `@${email}` : "unknown";
}

export default function CustomerSubscriptionManager({ userId, telegramId, plans, subscriptions }) {
  const router = useRouter();
  const activePlans = useMemo(() => plans.filter((plan) => plan.is_active !== false), [plans]);
  const [planId, setPlanId] = useState(activePlans[0]?.id ? String(activePlans[0].id) : "");
  const [importValue, setImportValue] = useState("");
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [pending, startTransition] = useTransition();
  const [busySubId, setBusySubId] = useState(null);

  async function run(action, payload) {
    setError("");
    setStatus("");
    try {
      const data = await jsonRequest(`/api/customers/${userId}/subscriptions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ...payload }),
      });
      setStatus(data.subLink ? "Subscription linked." : "Updated.");
      startTransition(() => {
        router.refresh();
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    }
  }

  const selectedPlan = activePlans.find((plan) => String(plan.id) === String(planId));

  return (
    <div className="grid" style={{ gap: 16 }}>
      {error ? <div className="notice error">{error}</div> : null}
      {status ? <div className="notice">{status}</div> : null}

      <div className="section" style={{ padding: 16 }}>
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <div>
            <h2 style={{ margin: 0 }}>Subscription management</h2>
            <div className="muted">Add a subscription from a plan or import an existing UUID / sub link.</div>
          </div>
        </div>

        <div className="form-grid">
          <div className="field-full">
            <label>Plan</label>
            <select value={planId} onChange={(e) => setPlanId(e.target.value)}>
              {activePlans.map((plan) => (
                <option key={plan.id} value={plan.id}>
                  {plan.name} · ${Number(plan.price_usdt || 0).toFixed(2)}
                </option>
              ))}
            </select>
            {selectedPlan ? (
              <div className="muted" style={{ marginTop: 8 }}>
                {selectedPlan.duration_days} days · {selectedPlan.traffic_gb === 0 ? "unlimited" : `${selectedPlan.traffic_gb} GB`} · inbounds {selectedPlan.inbound_ids || "all"}
              </div>
            ) : null}
          </div>

          <div>
            <label>Optional email</label>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder={`client-${telegramId}`}
            />
          </div>

          <div className="field-full actions">
            <button
              type="button"
              className="btn"
              disabled={pending || !planId}
              onClick={() => run("add_from_plan", { planId, email })}
            >
              {pending ? <Loader2 size={16} /> : <Plus size={16} />}
              Add from plan
            </button>
          </div>
        </div>
      </div>

      <div className="section" style={{ padding: 16 }}>
        <div className="toolbar" style={{ marginBottom: 12 }}>
          <div>
            <h2 style={{ margin: 0 }}>Import existing</h2>
            <div className="muted">Paste a subscription UUID or the full subscription link.</div>
          </div>
        </div>

        <div className="form-grid">
          <div className="field-full">
            <label>UUID / sub link</label>
            <input
              value={importValue}
              onChange={(e) => setImportValue(e.target.value)}
              placeholder="vless://... or https://.../sub/..."
            />
          </div>
          <div className="field-full actions">
            <button
              type="button"
              className="btn secondary"
              disabled={pending || !importValue.trim()}
              onClick={() => run("import_link", { source: importValue })}
            >
              {pending ? <Loader2 size={16} /> : <Link2 size={16} />}
              Import from link
            </button>
          </div>
        </div>
      </div>

      <div className="card-list">
        {subscriptions.map((sub) => (
          <div key={sub.id} className="item">
            <div className="item-head">
              <div>
                <h3 className="item-title">{sub.plan_name || "Manual import"}</h3>
                <p className="item-sub wrap">
                  {getSubscriptionIdentity(sub)} · {sub.sub_id || sub.client_uuid || `#${sub.id}`}
                </p>
                <p className="item-sub">Expires: {sub.expiry_date ? new Date(sub.expiry_date).toLocaleString() : "Unlimited"}</p>
              </div>
              <div className="actions">
                <span className={`pill ${sub.status === "active" ? "ok" : sub.status === "deleted" ? "bad" : "warn"}`}>
                  {sub.status}
                </span>
                <button
                  type="button"
                  className="btn danger"
                  disabled={busySubId === sub.id}
                  onClick={async () => {
                    try {
                      setBusySubId(sub.id);
                      await run("remove_subscription", { subId: sub.id });
                    } finally {
                      setBusySubId(null);
                    }
                  }}
                >
                  {busySubId === sub.id ? <Loader2 size={16} /> : <Trash2 size={16} />}
                  Remove
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
