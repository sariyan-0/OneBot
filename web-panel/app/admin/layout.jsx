export const dynamic = "force-dynamic";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { LogOut } from "lucide-react";

import { isAdminAuth } from "../../lib/auth";
import { safeImageSrc } from "../../lib/asset-url";
import { getSetting } from "../../lib/db";
import SidebarNav from "../../components/sidebar-nav";
import TopBar from "../../components/topbar";

const NAV = [
  ["/admin", "Dashboard", "LayoutDashboard"],
  ["/admin/customers", "Customers", "Users"],
  ["/admin/plans", "Plans", "Blocks"],
  ["/admin/discounts", "Discounts", "Tag"],
  ["/admin/test-sub", "Test Sub", "Gift"],
  ["/admin/manual-sub", "Manual Sub", "UserPlus"],
  ["/admin/inbounds", "Allowed Inbounds", "Network"],
  ["/admin/payments", "Payments", "BadgeDollarSign"],
  ["/admin/referrals", "Referrals", "Share2"],
  ["/admin/banners", "Banners", "Image"],
  ["/admin/tickets", "Tickets", "Tickets"],
  ["/admin/broadcast", "Broadcast", "Megaphone"],
  ["/admin/server", "Server", "ServerCog"],
  ["/admin/backups", "Backups", "HardDriveDownload"],
  ["/admin/security", "Security", "Shield"],
  ["/admin/settings", "Settings", "Settings"],
  ["/admin/brand", "Brand", "Palette"],
  ["/admin/sync-env", "DB Import", "ArrowRightLeft"],
  ["/admin/activity", "Live Activity", "Activity"],
  ["/admin/diagnostics", "Diagnostics", "Logs"],
  ["/admin/alerts", "Alerts", "BellRing"],
  ["/admin/status", "Status", "CircleDashed"],
  ["/admin/support", "Support", "LifeBuoy"],
];

export default async function AdminLayout({ children }) {
  const store = cookies();
  if (!(await isAdminAuth(store))) {
    redirect("/login");
  }
  const [panelName, logoUrl] = await Promise.all([
    getSetting("panel_name", "ONEBOT"),
    getSetting("brand_logo_url", ""),
  ]);
  const safeLogoUrl = safeImageSrc(logoUrl);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            {safeLogoUrl ? (
              <img src={safeLogoUrl} alt="ONEBOT" width={28} height={28} style={{ objectFit: "cover", borderRadius: 10 }} />
            ) : (
              "O"
            )}
          </div>
          <div>
            <strong>{panelName || "ONEBOT"}</strong>
            <small>Operator panel</small>
          </div>
        </div>
        <SidebarNav items={NAV} />
        <form action="/api/auth/logout" method="post" className="logout-form">
          <button type="submit" className="logout-link logout-button">
          <LogOut size={18} />
          <span>Logout</span>
          </button>
        </form>
      </aside>
      <div className="content">
        <TopBar panelName={panelName || "ONEBOT"} />
        <main className="main">{children}</main>
      </div>
    </div>
  );
}
