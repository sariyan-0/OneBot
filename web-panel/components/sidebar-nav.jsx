"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  ArrowRightLeft,
  BadgeDollarSign,
  BellRing,
  Blocks,
  CircleDashed,
  HardDriveDownload,
  Gift,
  Image,
  LifeBuoy,
  Logs,
  Megaphone,
  Network,
  Palette,
  Receipt,
  ServerCog,
  Shield,
  Settings,
  Tag,
  Tickets,
  UserPlus,
  Users,
  LayoutDashboard,
} from "lucide-react";

const ICONS = {
  Activity,
  ArrowRightLeft,
  BadgeDollarSign,
  BellRing,
  Blocks,
  CircleDashed,
  HardDriveDownload,
  Gift,
  Image,
  LifeBuoy,
  Logs,
  Megaphone,
  Network,
  Palette,
  Receipt,
  ServerCog,
  Shield,
  Settings,
  Tag,
  Tickets,
  UserPlus,
  Users,
  LayoutDashboard,
};

export default function SidebarNav({ items }) {
  const pathname = usePathname();

  return (
    <nav className="nav">
      {items.map(([href, label, iconName]) => {
        const active = pathname === href || (href !== "/admin" && pathname.startsWith(`${href}/`));
        const Icon = ICONS[iconName] || LayoutDashboard;
        return (
          <Link key={href} href={href} className={active ? "active" : ""}>
            <Icon size={18} />
            <span>{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
