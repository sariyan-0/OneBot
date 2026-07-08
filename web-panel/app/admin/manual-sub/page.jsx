"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronUp, Infinity, Loader2, Package, Search, UserPlus } from "lucide-react";

async function jsonFetch(url, init) {
  const res = await fetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export default function ManualSubPage() {
  const [search, setSearch] = useState("");
  const [results, setResults] = useState([]);
  const [selectedUser, setSelectedUser] = useState(null);
  const [plans, setPlans] = useState([]);
  const [inbounds, setInbounds] = useState([]);
  const [mode, setMode] = useState("plan");
  const [selectedPlan, setSelectedPlan] = useState(null);
  const [selectedInbounds, setSelectedInbounds] = useState([]);
  const [trafficGb, setTrafficGb] = useState("10");
  const [durationDays, setDurationDays] = useState("30");
  const [limitIp, setLimitIp] = useState("0");
  const [showInbounds, setShowInbounds] = useState(false);
  const [loading, setLoading] = useState(false);
  const [created, setCreated] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([jsonFetch("/api/plans"), jsonFetch("/api/inbounds")])
      .then(([p, i]) => {
        setPlans((p.plans || []).filter((plan) => plan.is_active !== false));
        setInbounds(i.inbounds || []);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!search.trim()) {
      setResults([]);
      return;
    }
    const timer = setTimeout(() => {
      jsonFetch(`/api/users?search=${encodeURIComponent(search)}&limit=8`)
        .then((d) => setResults(d.users || []))
        .catch(() => setResults([]));
    }, 350);
    return () => clearTimeout(timer);
  }, [search]);

  const selectedPlanData = useMemo(
    () => plans.find((plan) => Number(plan.id) === Number(selectedPlan)),
    [plans, selectedPlan]
  );

  const visibleInbounds = useMemo(() => {
    if (mode === "plan" && selectedPlanData?.inbound_ids) {
      const ids = String(selectedPlanData.inbound_ids)
        .split(",")
        .map((value) => Number(value.trim()))
        .filter(Boolean);
      if (ids.length) {
        return inbounds.filter((inb) => ids.includes(Number(inb.id)));
      }
    }
    if (mode === "plan") {
      return inbounds.filter((inb) => inb.enable !== false && inb.enable !== 0);
    }
    return inbounds;
  }, [mode, selectedPlanData, inbounds]);

  const toggleInbound = (id) => {
    setSelectedInbounds((prev) => (
      prev.includes(id) ? prev.filter((value) => value !== id) : [...prev, id]
    ));
  };

  const handleCreate = async () => {
    if (!selectedUser) return setError("Select a user first.");
    if (mode === "plan" && !selectedPlan) return setError("Select a plan first.");
    if (mode !== "plan" && selectedInbounds.length === 0) return setError("Select at least one inbound.");

    setLoading(true);
    setError("");
    setCreated(null);
    try {
      const payload = {
        userId: selectedUser.id,
        telegramId: selectedUser.telegram_id,
        mode,
        planId: selectedPlan,
        trafficGb,
        durationDays,
        limitIp,
        inboundIds: mode === "plan"
          ? (selectedPlanData?.inbound_ids ? String(selectedPlanData.inbound_ids).split(",").map((v) => Number(v.trim())).filter(Boolean) : [])
          : selectedInbounds,
      };
      const data = await jsonFetch("/api/manual-sub", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setCreated(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  };

  const canSubmit = selectedUser && (mode === "plan" ? selectedPlan : selectedInbounds.length > 0);

  return (
    <div className="grid" style={{ gap: 16, maxWidth: 980 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Manual Subscription</h2>
          <div className="muted">Create a subscription for a selected customer or build a custom one.</div>
        </div>
      </div>

      {error && <div className="panel bad">{error}</div>}
      {created && (
        <div className="panel ok">
          Created {created.email} · <a href={created.subLink} target="_blank" rel="noreferrer">Open link</a>
        </div>
      )}

      <section className="section">
        <div className="form-grid">
          <div className="field-full">
            <label>Customer</label>
            <div className="panel" style={{ margin: 0, padding: 12 }}>
              {selectedUser ? (
                <div className="item-head">
                  <div>
                    <h3 className="item-title">{selectedUser.first_name || selectedUser.username || `#${selectedUser.telegram_id}`}</h3>
                    <p className="item-sub">@{selectedUser.username || "no username"} · {selectedUser.telegram_id}</p>
                  </div>
                  <button className="btn secondary" type="button" onClick={() => setSelectedUser(null)}>Change</button>
                </div>
              ) : (
                <>
                  <div style={{ position: "relative" }}>
                    <Search size={16} style={{ position: "absolute", right: 12, top: 12, opacity: .5 }} />
                    <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search name, username, or telegram id..." style={{ paddingRight: 36 }} />
                  </div>
                  {results.length > 0 && (
                    <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
                      {results.map((user) => (
                        <button
                          key={user.id}
                          type="button"
                          className="item"
                          onClick={() => { setSelectedUser(user); setSearch(""); setResults([]); }}
                          style={{ textAlign: "right" }}
                        >
                          <div className="item-head">
                            <div>
                              <h3 className="item-title">{user.first_name || user.username || `#${user.telegram_id}`}</h3>
                              <p className="item-sub">@{user.username || "no username"} · {user.telegram_id}</p>
                            </div>
                            <UserPlus size={16} />
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          <div>
            <label>Mode</label>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="plan">Use plan</option>
              <option value="custom">Custom</option>
            </select>
          </div>

          {mode === "plan" ? (
            <div className="field-full">
              <label>Plan</label>
              <div className="card-list">
                {plans.map((plan) => (
                  <button
                    key={plan.id}
                    type="button"
                    onClick={() => setSelectedPlan(plan.id)}
                    className="item"
                    style={{ textAlign: "right", borderColor: Number(selectedPlan) === Number(plan.id) ? "var(--accent)" : undefined }}
                  >
                    <div className="item-head">
                      <div>
                        <h3 className="item-title">{plan.name}</h3>
                        <p className="item-sub">{plan.duration_days} days · ${Number(plan.price_usdt).toFixed(2)}{Number(plan.traffic_gb) === 0 ? " · unlimited" : ` · ${plan.traffic_gb} GB`}</p>
                      </div>
                      <div className={`pill ${plan.traffic_gb === 0 ? "ok" : "pill"}`}>
                        {plan.traffic_gb === 0 ? <Infinity size={14} /> : <Package size={14} />}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              <div>
                <label>Traffic GB</label>
                <input value={trafficGb} onChange={(e) => setTrafficGb(e.target.value)} type="number" min="0" placeholder="0 = unlimited" />
              </div>
              <div>
                <label>Duration days</label>
                <input value={durationDays} onChange={(e) => setDurationDays(e.target.value)} type="number" min="1" />
              </div>
              <div>
                <label>Limit IP</label>
                <input value={limitIp} onChange={(e) => setLimitIp(e.target.value)} type="number" min="0" />
              </div>
            </>
          )}

          <div className="field-full">
            <button type="button" onClick={() => setShowInbounds((v) => !v)} className="btn secondary" style={{ width: "100%" }}>
              {showInbounds ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
              Allowed inbounds ({mode === "plan" ? (selectedPlanData?.inbound_ids ? "from plan" : "all active") : selectedInbounds.length})
            </button>
            {showInbounds && (
              <div className="card-list" style={{ marginTop: 12 }}>
                {visibleInbounds.map((inbound) => {
                  const active = selectedInbounds.includes(Number(inbound.id));
                  const locked = mode === "plan";
                  return (
                    <button
                      key={inbound.id}
                      type="button"
                      className="item"
                      onClick={() => !locked && toggleInbound(Number(inbound.id))}
                      style={{ textAlign: "right", opacity: locked ? 0.75 : 1 }}
                    >
                      <div className="item-head">
                        <div>
                          <h3 className="item-title">{inbound.remark}</h3>
                          <p className="item-sub">{inbound.protocol?.toUpperCase?.() || inbound.protocol} · port {inbound.port}</p>
                        </div>
                        <div className="pill">{active || locked ? "selected" : "available"}</div>
                      </div>
                    </button>
                  );
                })}
                {visibleInbounds.length === 0 && <div className="muted">No inbounds available.</div>}
              </div>
            )}
          </div>

          <div className="field-full actions">
            <button type="button" onClick={handleCreate} disabled={!canSubmit || loading}>
              {loading ? <Loader2 size={16} /> : <CheckCircle2 size={16} />}
              Create subscription
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
