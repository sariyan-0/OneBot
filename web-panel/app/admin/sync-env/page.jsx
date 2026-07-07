"use client";

import { useEffect, useState } from "react";
import { ArrowRightLeft } from "lucide-react";

async function jsonFetch(url, init) {
  const res = await fetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export default function SyncEnvPage() {
  const [status, setStatus] = useState(null);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      setStatus(await jsonFetch("/api/sync-env"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load().catch(() => {});
  }, []);

  const sync = async () => {
    setLoading(true);
    setResult(null);
    try {
      const data = await jsonFetch("/api/sync-env", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force: false }),
      });
      setResult(data);
      await load();
    } catch (err) {
      setResult({ ok: false, error: err instanceof Error ? err.message : "Import failed" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid" style={{ gap: 16, maxWidth: 960 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>DB Import</h2>
          <div className="muted">Import runtime environment values into the database once, then keep managing settings from there.</div>
        </div>
      </div>

      <section className="section">
        <div className="panel" style={{ marginBottom: 16 }}>
          <div className="muted">Status</div>
          {status ? (
            <div style={{ marginTop: 8, display: "grid", gap: 8 }}>
              <div className={status.inSync ? "pill ok" : "pill bad"}>
                {status.inSync ? "In sync" : `${status.outOfSync.length} keys can be imported`}
              </div>
              <div className="muted">Database root: {status.rootDir || "unknown"}</div>
            </div>
          ) : (
            <div className="muted">Loading...</div>
          )}
        </div>

        {result && (
          <div className={`panel ${result.ok ? "ok" : "bad"}`} style={{ marginBottom: 16 }}>
            {result.message || result.error}
            {result.synced?.length ? <div className="muted">Imported: {result.synced.join(", ")}</div> : null}
          </div>
        )}

        {status && !status.inSync && (
          <div className="panel" style={{ marginBottom: 16 }}>
            <div className="muted" style={{ marginBottom: 8 }}>Keys available for import</div>
            <div style={{ display: "grid", gap: 6 }}>
              {status.outOfSync.map((key) => (
                <div key={key} className="item" style={{ padding: "10px 12px" }}>
                  {key}
                </div>
              ))}
            </div>
          </div>
        )}

        <button type="button" onClick={sync} disabled={loading || !status}>
          <ArrowRightLeft size={16} />
          Import into DB
        </button>
      </section>
    </div>
  );
}
