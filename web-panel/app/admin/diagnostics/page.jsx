export const dynamic = "force-dynamic";

export default function DiagnosticsPage() {
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Diagnostics</h2>
          <div className="muted">This page is reserved for future API smoke tests and route checks.</div>
        </div>
      </div>
      <section className="section">
        <div className="notice">Build health, proxy checks, and API validation can be expanded here without changing the layout contract.</div>
      </section>
    </div>
  );
}
