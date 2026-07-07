export const dynamic = "force-dynamic";

export default function AlertsPage() {
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Alerts</h2>
          <div className="muted">Future home for warnings, threshold notices, and operator alerts.</div>
        </div>
      </div>
      <section className="section">
        <div className="notice">Threshold-based alerts can be surfaced here without changing the surrounding shell.</div>
      </section>
    </div>
  );
}
