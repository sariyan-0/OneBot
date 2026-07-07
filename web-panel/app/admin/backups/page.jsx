export const dynamic = "force-dynamic";

import { HardDriveDownload, RotateCcw, FileDown } from "lucide-react";
import { getBackupsSettings } from "../../../lib/admin-data";

export default async function BackupsPage() {
  const settings = await getBackupsSettings();

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Backups</h2>
          <div className="muted">Download archives, restore from zip, and inspect saved settings.</div>
        </div>
      </div>

      <div className="two-col">
        <section className="section">
          <h3>Create backup</h3>
          <p className="muted">Exports the bot database, SQLite WAL files, DB-backed operator settings, root and panel env files, logs, branding uploads, banner previews, the active nginx config, and a best-effort 3X-UI panel DB dump when the panel allows it.</p>
          <form action="/api/backups/create" method="post">
            <button type="submit"><FileDown size={16} /> Download backup zip</button>
          </form>
        </section>

        <section className="section">
          <h3>Restore backup</h3>
          <p className="muted">Upload a previously created backup zip to restore the supported bot env, database settings, DB files, logs, and branding files. If the archive contains a 3X-UI panel DB dump, import that file separately inside 3X-UI.</p>
          <form action="/api/backups/restore" method="post" encType="multipart/form-data" className="grid" style={{ gap: 12 }}>
            <input type="file" name="archive" accept=".zip" required />
            <button type="submit" className="btn secondary"><RotateCcw size={16} /> Restore zip</button>
          </form>
        </section>
      </div>

      <section className="section">
        <h3>Saved operator settings</h3>
        <div className="grid cards">
          {Object.entries(settings).map(([key, value]) => (
            <div key={key} className="stat">
              <span className="muted">{key}</span>
              <strong style={{ fontSize: 18, wordBreak: "break-word" }}>{String(value || "—")}</strong>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
