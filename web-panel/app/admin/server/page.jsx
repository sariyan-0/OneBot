export const dynamic = "force-dynamic";

import { RefreshCw, ShieldCheck } from "lucide-react";
import { getServerStatus, getXrayLogs } from "../../../lib/xui";
import { getSystemSnapshot } from "../../../lib/system";
import { bytes, pct } from "../../../lib/format";

function Ring({ label, value, detail, tone = "ok" }) {
  return (
    <div className="panel ring-card">
      <div className={`ring ${tone}`} style={{ "--value": `${value}%` }}>
        <span>{Math.round(value)}%</span>
      </div>
      <div>
        <strong>{label}</strong>
        <div className="muted">{detail}</div>
      </div>
    </div>
  );
}

export default async function ServerPage() {
  const xui = await getServerStatus().catch(() => ({}));
  const sys = getSystemSnapshot();
  const logs = await getXrayLogs(80);

  const memPercent = pct(sys.memory.used, sys.memory.total);
  const diskPercent = pct(sys.disk.used, sys.disk.total);
  const xuiMem = pct(xui?.mem?.current, xui?.mem?.total);
  const xuiDisk = pct(xui?.disk?.current, xui?.disk?.total);

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Server status</h2>
          <div className="muted">Circular utilization cards, Xray state, and recent logs.</div>
        </div>
        <form action="/api/server/restart-xray" method="post">
          <button type="submit" className="btn secondary">
            <RefreshCw size={16} />
            Restart Xray
          </button>
        </form>
      </div>

      <div className="ring-grid">
        <Ring label="System memory" value={memPercent} detail={`${bytes(sys.memory.used)} / ${bytes(sys.memory.total)}`} />
        <Ring label="System disk" value={diskPercent} detail={`${bytes(sys.disk.used)} / ${bytes(sys.disk.total)}`} tone={diskPercent > 85 ? "bad" : "warn"} />
        <Ring label="Panel RAM" value={pct(sys.process.rss, sys.memory.total)} detail={`${bytes(sys.process.rss)} used by Next.js`} />
        <Ring label="Xray memory" value={xuiMem} detail={`${bytes(xui?.mem?.current)} / ${bytes(xui?.mem?.total)}`} />
        <Ring label="Xray disk" value={xuiDisk} detail={`${bytes(xui?.disk?.current)} / ${bytes(xui?.disk?.total)}`} tone={xuiDisk > 85 ? "bad" : "warn"} />
        <Ring label="Loads" value={Math.min(100, (Number(xui?.loads?.[0] || sys.load[0] || 0) / Math.max(1, sys.cpuCount)) * 100)} detail={`Load avg: ${(xui?.loads || sys.load).map((n) => Number(n).toFixed(2)).join(" · ")}`} />
      </div>

      <div className="two-col">
        <section className="section">
          <h3>3X-UI status</h3>
          <div className="grid cards">
            <div className="stat"><span className="muted">Panel version</span><strong style={{ fontSize: 22 }}>{xui?.panelVersion || "—"}</strong></div>
            <div className="stat"><span className="muted">Panel GUID</span><strong style={{ fontSize: 18, wordBreak: "break-all" }}>{xui?.panelGuid || "—"}</strong></div>
            <div className="stat"><span className="muted">Uptime</span><strong style={{ fontSize: 22 }}>{xui?.uptime || "—"}</strong></div>
            <div className="stat"><span className="muted">Xray</span><strong style={{ fontSize: 22 }}>{xui?.xray?.state || "—"}</strong></div>
          </div>
        </section>

        <section className="section">
          <h3>System snapshot</h3>
          <div className="card-list">
            <div className="item"><strong>Host</strong><div className="muted">{sys.hostname}</div></div>
            <div className="item"><strong>OS</strong><div className="muted">{sys.platform}</div></div>
            <div className="item"><strong>CPU cores</strong><div className="muted">{sys.cpuCount}</div></div>
          </div>
        </section>
      </div>

      <section className="section">
        <div className="toolbar">
          <div>
            <h3 style={{ margin: 0 }}>Xray logs</h3>
            <div className="muted">Pulled directly from the 3X-UI API.</div>
          </div>
        </div>
        <pre className="logbox">{logs}</pre>
      </section>
    </div>
  );
}
