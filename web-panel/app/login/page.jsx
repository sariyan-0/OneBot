export const dynamic = "force-dynamic";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import Image from "next/image";

import { isAdminAuth } from "../../lib/auth";
import { getSetting } from "../../lib/db";
import { ShieldCheck, LockKeyhole } from "lucide-react";

export default async function LoginPage({ searchParams }) {
  const store = cookies();
  if (await isAdminAuth(store)) {
    redirect("/admin");
  }

  const error = searchParams?.error;
  const [panelName, logoUrl] = await Promise.all([
    getSetting("panel_name", "ONEBOT"),
    getSetting("brand_logo_url", ""),
  ]);

  return (
    <main className="main" style={{ minHeight: "100dvh", display: "grid", placeItems: "center" }}>
      <div className="panel" style={{ width: "min(520px, 100%)", padding: 24 }}>
        <div className="brand" style={{ borderBottom: "1px solid rgba(255,255,255,.08)", marginBottom: 18 }}>
          <div className="brand-mark">
            {logoUrl ? <Image src={logoUrl} alt={panelName} width={28} height={28} style={{ objectFit: "cover", borderRadius: 10 }} unoptimized /> : "O"}
          </div>
          <div>
            <strong>{panelName || "ONEBOT"}</strong>
            <small>Web Admin</small>
          </div>
        </div>
        <h1 style={{ margin: "0 0 6px", fontSize: 28 }}>Operator login</h1>
        <p className="muted" style={{ marginTop: 0 }}>
          Sign in with the admin credentials stored in the database.
        </p>
        {error ? <div className="notice error" style={{ marginBottom: 14 }}>{decodeURIComponent(error)}</div> : null}
        <form action="/api/auth/login" method="post" className="grid" style={{ gap: 12 }}>
          <div>
            <label htmlFor="username">Username</label>
            <input id="username" name="username" autoComplete="username" placeholder="admin" required />
          </div>
          <div>
            <label htmlFor="password">Password</label>
            <input id="password" name="password" type="password" autoComplete="current-password" placeholder="••••••••" required />
          </div>
          <button type="submit" style={{ width: "100%" }}>
            <ShieldCheck size={18} />
            Sign in
          </button>
        </form>
        <div className="muted" style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "center" }}>
          <LockKeyhole size={16} />
          <span>WEB_ADMIN_USERNAME / WEB_ADMIN_PASSWORD</span>
        </div>
      </div>
    </main>
  );
}
