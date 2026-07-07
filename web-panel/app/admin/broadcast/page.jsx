export const dynamic = "force-dynamic";

import BroadcastForm from "../../../components/broadcast-form";

export default function BroadcastPage() {
  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Broadcast</h2>
          <div className="muted">Send text or a photo with caption to active users.</div>
        </div>
      </div>

      <section className="section">
        <BroadcastForm />
      </section>
    </div>
  );
}
