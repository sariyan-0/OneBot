export const dynamic = "force-dynamic";

export default function StatusPage() {
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Status</h2>
          <div className="muted">High-level system state and operational shortcuts.</div>
        </div>
      </div>
      <section className="section">
        <div className="notice">Use the server page for live circular metrics and Xray logs. This route stays as a summary landing area.</div>
      </section>
    </div>
  );
}
