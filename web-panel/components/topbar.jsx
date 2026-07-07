"use client";

import { useEffect, useRef, useState } from "react";
import { Bell, RefreshCw, ServerCog } from "lucide-react";
import { useRouter, usePathname } from "next/navigation";

function getPrefix(pathname) {
  const dashIdx = pathname.indexOf("/admin");
  if (dashIdx > 0) return pathname.slice(0, dashIdx);
  return "";
}

function timeAgo(iso) {
  const d = new Date(iso);
  const diff = Math.floor((Date.now() - d.getTime()) / 1000);
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  return `${Math.floor(diff / 86400)}d`;
}

export default function TopBar({ panelName }) {
  const pathname = usePathname();
  const prefix = getPrefix(pathname);
  const router = useRouter();
  const [notifOpen, setNotifOpen] = useState(false);
  const [notifCount, setNotifCount] = useState(0);
  const [items, setItems] = useState([]);
  const [restarting, setRestarting] = useState(false);
  const notifRef = useRef(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${prefix}/api/notifications`, { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        setNotifCount(data.total || 0);
        setItems(data.items || []);
      } catch {
        // ignore
      }
    };
    load();
    const id = setInterval(load, 60000);
    return () => clearInterval(id);
  }, [prefix]);

  useEffect(() => {
    const onClick = (event) => {
      if (notifRef.current && !notifRef.current.contains(event.target)) {
        setNotifOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const restartXray = async () => {
    setRestarting(true);
    try {
      await fetch(`${prefix}/api/server/restart-xray`, { method: "POST" });
      router.refresh();
    } finally {
      setRestarting(false);
    }
  };

  return (
    <div className="topbar">
      <div>
        <h1>{panelName || "ONEBOT"} Control Plane</h1>
        <p>Node.js admin surface for customers, plans, payments, backups, and server health.</p>
      </div>
      <div className="actions" ref={notifRef}>
        <button type="button" className="btn secondary" onClick={restartXray} disabled={restarting}>
          {restarting ? <RefreshCw className="spin" size={16} /> : <ServerCog size={16} />}
          Restart Xray
        </button>
        <button type="button" className="btn secondary" onClick={() => setNotifOpen((v) => !v)}>
          <Bell size={16} />
          {notifCount > 0 ? `${notifCount}` : "0"}
        </button>
        {notifOpen && (
          <div className="notif-popover">
            <div className="notif-head">
              <strong>Notifications</strong>
              <span className="pill">{notifCount} total</span>
            </div>
            <div className="notif-list">
              {items.length ? items.map((item, index) => (
                <button key={`${item.link}-${index}`} type="button" className="notif-item" onClick={() => router.push(item.link)}>
                  <span className="muted">{item.type === "payment" ? "💳" : "📝"}</span>
                  <div>
                    <strong>{item.label}</strong>
                    <div className="muted">{timeAgo(item.time)} ago</div>
                  </div>
                </button>
              )) : <div className="muted">No notifications.</div>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
