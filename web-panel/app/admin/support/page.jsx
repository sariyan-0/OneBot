export const dynamic = "force-dynamic";

export default function SupportPage() {
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Support</h2>
          <div className="muted">Support routing and contact details can live here.</div>
        </div>
      </div>
      <section className="section">
        <div className="notice">This slot is intentionally lightweight for now; the ticket inbox is the main support surface.</div>
      </section>
    </div>
  );
}
