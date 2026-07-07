export const dynamic = "force-dynamic";

import { getSetting } from "../../../lib/db";

export default async function SecurityPage() {
  const [username, secret] = await Promise.all([
    getSetting("WEB_ADMIN_USERNAME", "admin"),
    getSetting("WEB_ADMIN_COOKIE_SECRET", ""),
  ]);

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="toolbar">
        <div>
          <h2 style={{ margin: 0 }}>Security</h2>
          <div className="muted">Panel login credentials live in the database now, so the panel stays portable.</div>
        </div>
      </div>

      <section className="section">
        <form action="/api/security" method="post" className="grid" style={{ gap: 12 }}>
          <div className="form-grid">
            <div>
              <label>Panel username</label>
              <input name="web_admin_username" defaultValue={username || "admin"} />
            </div>
            <div>
              <label>New password</label>
              <input name="web_admin_password" type="password" placeholder="Leave blank to keep current" />
            </div>
            <div className="field-full">
              <label>Cookie secret</label>
              <input name="web_admin_cookie_secret" defaultValue={secret || ""} />
            </div>
          </div>
          <button type="submit">Save security settings</button>
        </form>
      </section>
    </div>
  );
}
