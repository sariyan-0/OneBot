"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";

async function fetchInbounds() {
  const res = await fetch("/api/inbounds", { cache: "no-store" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return Array.isArray(data.inbounds) ? data.inbounds : [];
}

export default function InboundPicker({
  name = "inbound_ids",
  initialValue = "",
  label = "Select inbounds",
  emptyLabel = "All active inbounds",
  showSummary = false,
  allowMultiple = true,
}) {
  const [open, setOpen] = useState(false);
  const [inbounds, setInbounds] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(() =>
    String(initialValue || "")
      .split(",")
      .map((value) => Number(value.trim()))
      .filter(Boolean)
  );

  useEffect(() => {
    if (!open || inbounds.length) return;
    let mounted = true;
    setLoading(true);
    fetchInbounds()
      .then((rows) => {
        if (!mounted) return;
        setInbounds(rows);
      })
      .catch((err) => {
        if (!mounted) return;
        setError(err instanceof Error ? err.message : "Failed to load inbounds");
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, [open, inbounds.length]);

  const selectedRows = useMemo(
    () => inbounds.filter((inbound) => selected.includes(Number(inbound.id))),
    [inbounds, selected]
  );

  const visibleRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return inbounds;
    return inbounds.filter((inbound) => {
      const text = `${inbound.remark || ""} ${inbound.protocol || ""} ${inbound.port || ""}`.toLowerCase();
      return text.includes(q);
    });
  }, [inbounds, search]);

  const toggle = (id) => {
    setSelected((prev) => {
      if (allowMultiple) {
        return prev.includes(id) ? prev.filter((value) => value !== id) : [...prev, id];
      }
      if (prev.includes(id)) {
        return [];
      }
      return [id];
    });
  };

  const hiddenValue = selected.join(",");
  const triggerLabel = selected.length
    ? `${selected.length} inbound${selected.length === 1 ? "" : "s"} selected`
    : emptyLabel || label;

  return (
    <div className="inbound-picker">
      <input type="hidden" name={name} value={hiddenValue} />
      <button
        type="button"
        className="btn secondary inbound-picker-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <ChevronDown size={16} />
        {triggerLabel}
      </button>
      {showSummary && selected.length ? (
        <div className="muted" style={{ marginTop: 8 }}>
          {selectedRows.length
            ? selectedRows.map((inbound) => inbound.remark || `#${inbound.id}`).join(" · ")
            : selected.map((id) => `#${id}`).join(" · ")}
        </div>
      ) : null}

      {open ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setOpen(false)}>
          <div className="modal-panel" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
            <div className="toolbar" style={{ marginBottom: 12 }}>
              <div>
                <h3 style={{ margin: 0 }}>Select inbounds</h3>
                <div className="muted">
                  {allowMultiple ? "Pick one or more inbounds, or leave empty to allow all active inbounds." : "Pick one inbound for this setting."}
                </div>
              </div>
              <div className="actions" style={{ justifyContent: "flex-end" }}>
                {allowMultiple ? (
                  <button type="button" className="btn secondary" onClick={() => setSelected([])}>
                    Clear
                  </button>
                ) : null}
                <button type="button" className="btn secondary" onClick={() => setOpen(false)}>
                  Close
                </button>
              </div>
            </div>

            <div className="form-grid" style={{ marginBottom: 12 }}>
              <div className="field-full" style={{ position: "relative" }}>
                <Search size={16} className="inbound-picker-search" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search remark, protocol, port..."
                  style={{ paddingLeft: 36 }}
                />
              </div>
            </div>

            {error ? <div className="notice error" style={{ marginBottom: 12 }}>{error}</div> : null}
            {loading ? <div className="muted">Loading inbounds...</div> : null}

            <div className="card-list inbound-picker-list">
              {visibleRows.map((inbound) => {
                const active = selected.includes(Number(inbound.id));
                return (
                  <button
                    key={inbound.id}
                    type="button"
                    className={`item inbound-picker-item ${active ? "active" : ""}`}
                    onClick={() => toggle(Number(inbound.id))}
                  >
                    <div className="item-head">
                      <div>
                        <h4 className="item-title">{inbound.remark || `Inbound #${inbound.id}`}</h4>
                        <p className="item-sub">
                          {inbound.protocol?.toUpperCase?.() || inbound.protocol || "UNKNOWN"} · port {inbound.port}
                        </p>
                      </div>
                      <span className={`pill ${active ? "ok" : "warn"}`}>
                        {active ? <Check size={14} /> : "Choose"}
                      </span>
                    </div>
                  </button>
                );
              })}
              {!visibleRows.length ? <div className="muted">No inbounds found.</div> : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
