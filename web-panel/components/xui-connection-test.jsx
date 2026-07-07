"use client";

import { useState } from "react";
import { PlugZap, RefreshCw } from "lucide-react";

export default function XuiConnectionTest() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const test = async () => {
    setLoading(true);
    setResult(null);
    try {
      const res = await fetch("/api/settings/test-xui", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        throw new Error(data.error || data.message || `Request failed (${res.status})`);
      }
      setResult({ ok: true, message: data.message || "Connection OK" });
    } catch (err) {
      setResult({ ok: false, message: err instanceof Error ? err.message : "Connection test failed" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="panel" style={{ margin: 0 }}>
      <div className="toolbar" style={{ marginBottom: 10 }}>
        <div>
          <strong>Connection test</strong>
          <div className="muted">Checks the configured 3X-UI API endpoint and bearer token.</div>
        </div>
        <button type="button" className="btn secondary" onClick={test} disabled={loading}>
          {loading ? <RefreshCw size={16} className="spin" /> : <PlugZap size={16} />}
          {loading ? "Testing..." : "Test connection"}
        </button>
      </div>
      {result ? (
        <div className={`notice ${result.ok ? "" : "error"}`} style={{ marginTop: 8 }}>
          {result.message}
        </div>
      ) : (
        <div className="muted">Run a quick check after saving to confirm the panel can talk to 3X-UI.</div>
      )}
    </div>
  );
}
