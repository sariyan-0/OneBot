"""
services/web_admin.py - Lightweight aiohttp web admin panel.

The panel intentionally reuses the existing database models and settings store.
It is mounted by services.webhook_server on the same WEBHOOK_PORT.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import io
import json
import os
import shutil
import secrets
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from aiohttp import web
from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from config import settings
from database import AsyncSessionLocal
from database.crud import (
    create_discount_code,
    create_plan,
    delete_discount_code,
    delete_plan,
    get_all_discount_codes,
    get_all_plans,
    get_enabled_inbound_ids,
    get_payments_filtered,
    get_plan,
    get_or_create_user,
    get_setting,
    get_stats,
    set_enabled_inbound_ids,
    set_setting,
    update_payment_status,
    update_subscription_status,
    update_plan,
)
from database.models import Payment, Plan, Subscription, TestSubscriptionRecord, Ticket, User
from services.activity_log import ActivityLoggingBot, get_recent_activity_logs
from services.subscription import create_new_subscription, rotate_subscription_link, delete_subscription_completely
from services.blocked_users import get_blocked_user_ids, set_user_blocked
from services.xui_api import XUIClient, XUIError, build_sub_link_for


ENV_KEYS = [
    "BOT_TOKEN",
    "ADMIN_IDS",
    "ADMIN_SECRET",
    "PANEL_URL",
    "PANEL_USERNAME",
    "PANEL_PASSWORD",
    "PANEL_API_TOKEN",
    "SUB_PORT",
    "DB_URL",
    "WEBHOOK_PORT",
    "NOWPAYMENTS_API_KEY",
    "NOWPAYMENTS_IPN_SECRET",
    "NOWPAYMENTS_IPN_URL",
    "MAXELPAY_API_KEY",
    "MAXELPAY_WEBHOOK_URL",
    "MAXELPAY_WEBHOOK_SECRET",
    "BOT_USERNAME",
    "WEB_ADMIN_ENABLED",
    "WEB_ADMIN_USERNAME",
    "WEB_ADMIN_PASSWORD",
    "WEB_ADMIN_COOKIE_SECRET",
]


def _env_path() -> Path:
    return Path(__file__).resolve().parent.parent / ".env"


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _cookie_secret() -> str:
    return (
        settings.web_admin_cookie_secret
        or settings.admin_secret
        or settings.bot_token
        or "nexora-web-admin-local"
    )


def _sign(value: str) -> str:
    return hmac.new(_cookie_secret().encode(), value.encode(), hashlib.sha256).hexdigest()


def _make_cookie(username: str) -> str:
    value = f"{username}:{int(datetime.now(timezone.utc).timestamp())}"
    return f"{value}:{_sign(value)}"


def _valid_cookie(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        value, sig = raw.rsplit(":", 1)
    except ValueError:
        return False
    return hmac.compare_digest(_sign(value), sig)


def _web_password() -> str:
    return settings.web_admin_password or settings.admin_secret


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _money(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except Exception:
        return "0.00"


def _gb_from_bytes(value: int | None) -> str:
    if not value:
        return "0"
    return f"{value / (1024 ** 3):.2f}"


def _fmt_bytes(value: Any) -> str:
    try:
        num = float(value or 0)
    except Exception:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    idx = 0
    while num >= 1024 and idx < len(units) - 1:
        num /= 1024
        idx += 1
    if idx == 0:
        return f"{int(num)} {units[idx]}"
    return f"{num:.2f} {units[idx]}"


def _fmt_uptime(seconds: Any) -> str:
    try:
        total = int(float(seconds or 0))
    except Exception:
        return "0s"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _usage_percent(part: Any, total: Any) -> float:
    try:
        total_num = float(total or 0)
        if total_num <= 0:
            return 0.0
        return max(0.0, min(100.0, float(part or 0) / total_num * 100.0))
    except Exception:
        return 0.0


def _meter_card(label: str, percent: float, detail: str, tone: str = "ok") -> str:
    percent = max(0.0, min(100.0, percent))
    return f"""
    <div class="meter-card">
      <div class="ring {tone}" style="--value:{percent:.1f}%">
        <span>{percent:.0f}%</span>
      </div>
      <div>
        <strong>{_esc(label)}</strong>
        <small>{_esc(detail)}</small>
      </div>
    </div>
    """


def _kv_card(label: str, value: Any, detail: str = "") -> str:
    detail_html = f"<small>{_esc(detail)}</small>" if detail else ""
    return f"""
    <div class="kv-card">
      <span class="muted">{_esc(label)}</span>
      <strong>{_esc(value)}</strong>
      {detail_html}
    </div>
    """


def _redirect(path: str, **params: str) -> web.Response:
    qs = f"?{urlencode(params)}" if params else ""
    raise web.HTTPFound(f"{path}{qs}")


def _require_auth(request: web.Request) -> None:
    if not _valid_cookie(request.cookies.get("nexora_admin")):
        next_url = quote(str(request.rel_url))
        raise web.HTTPFound(f"/admin/login?next={next_url}")


def _layout(title: str, body: str, request: web.Request | None = None) -> web.Response:
    authed = bool(request and _valid_cookie(request.cookies.get("nexora_admin")))
    app_class = "app-shell" if authed else "app-shell no-sidebar"
    nav = ""
    if authed:
        current_path = request.path if request else "/admin"
        nav_items = [
            ("/admin", "Dashboard"),
            ("/admin/stats", "Stats"),
            ("/admin/customers", "Customers"),
            ("/admin/inbounds", "Inbounds"),
            ("/admin/server", "Server"),
            ("/admin/tickets", "Tickets"),
            ("/admin/plans", "Plans"),
            ("/admin/test-plan", "Test Plan"),
            ("/admin/payments", "Payments"),
            ("/admin/receipts", "Receipts"),
            ("/admin/discounts", "Discounts"),
            ("/admin/content", "Content"),
            ("/admin/broadcast", "Broadcast"),
            ("/admin/security", "Security"),
            ("/admin/settings", "Settings"),
            ("/admin/backups", "Backups"),
        ]
        nav_links = []
        for href, label in nav_items:
            active = current_path == href or (href != "/admin" and current_path.startswith(f"{href}/"))
            nav_links.append(
                f'<a class="{"active" if active else ""}" href="{href}">{label}</a>'
            )
        nav = f"""
        <aside class="sidebar" aria-label="Admin navigation">
          <div class="brand">
            <span class="brand-mark" aria-hidden="true">O</span>
            <div><strong>ONEBOT</strong><small>Bot Operations</small></div>
          </div>
          <nav>{''.join(nav_links)}</nav>
          <a class="logout-link" href="/admin/logout">Logout</a>
        </aside>
        """
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(title)} - ONEBOT Admin</title>
  <style>
    :root {{
      --bg:#0f172a; --bg-soft:#111c33; --panel:#162239; --panel-strong:#1d2a42;
      --text:#f8fafc; --muted:#a9b4c6; --soft:#dbe6f7; --line:rgba(255,255,255,.1);
      --accent:#22c55e; --accent-strong:#16a34a; --accent-soft:rgba(34,197,94,.16);
      --accent2:#334155; --danger:#ef4444; --danger-soft:rgba(239,68,68,.16);
      --warning:#f59e0b; --warning-soft:rgba(245,158,11,.16);
      --shadow:0 18px 50px rgba(0,0,0,.22); --radius:12px;
    }}
    * {{ box-sizing:border-box; }}
    html {{ color-scheme:dark; }}
    body {{
      margin:0; min-height:100dvh; font-family:"Segoe UI", Arial, sans-serif;
      background:
        radial-gradient(circle at top left, rgba(34,197,94,.11), transparent 30rem),
        linear-gradient(145deg, #0b1120 0%, #0f172a 48%, #111827 100%);
      color:var(--text);
    }}
    a {{ color:inherit; }}
    a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {{
      outline:3px solid rgba(34,197,94,.55); outline-offset:2px;
    }}
    .skip-link {{ position:absolute; left:-999px; top:10px; z-index:20; padding:10px 12px; background:var(--accent); color:#052e16; border-radius:8px; }}
    .skip-link:focus {{ left:12px; }}
    .app-shell {{ display:grid; grid-template-columns:248px minmax(0,1fr); min-height:100dvh; }}
    .app-shell.no-sidebar {{ grid-template-columns:minmax(0,1fr); }}
    .sidebar {{
      position:sticky; top:0; height:100dvh; display:flex; flex-direction:column; gap:16px;
      padding:18px 14px; border-right:1px solid var(--line);
      background:rgba(15,23,42,.92); backdrop-filter:blur(18px);
    }}
    .brand {{ display:flex; align-items:center; gap:10px; padding:8px 8px 14px; border-bottom:1px solid var(--line); }}
    .brand-mark {{
      display:grid; place-items:center; width:38px; height:38px; border-radius:10px;
      background:linear-gradient(145deg, var(--accent), #7dd3fc); color:#052e16; font-weight:800;
      box-shadow:0 10px 30px rgba(34,197,94,.2);
    }}
    .brand strong {{ display:block; font-size:15px; letter-spacing:.01em; }}
    .brand small {{ display:block; color:var(--muted); font-size:12px; margin-top:2px; }}
    nav {{ display:grid; gap:4px; overflow:auto; padding-right:2px; }}
    nav a, .logout-link {{
      min-height:44px; display:flex; align-items:center; gap:8px; color:var(--muted);
      text-decoration:none; padding:10px 11px; border-radius:10px; font-size:14px;
      border:1px solid transparent; transition:background .18s ease, color .18s ease, border-color .18s ease, transform .18s ease;
    }}
    nav a:hover, .logout-link:hover {{ background:rgba(255,255,255,.06); color:var(--text); }}
    nav a:active, .logout-link:active {{ transform:translateY(1px); }}
    nav a.active {{
      color:var(--text); background:var(--accent-soft); border-color:rgba(34,197,94,.28);
      box-shadow:inset 3px 0 0 var(--accent);
    }}
    .logout-link {{ margin-top:auto; color:#fecaca; }}
    .content-shell {{ min-width:0; }}
    header {{
      min-height:76px; padding:18px 28px; display:flex; justify-content:space-between; gap:18px; align-items:center;
      border-bottom:1px solid var(--line); background:rgba(15,23,42,.72); backdrop-filter:blur(18px);
    }}
    header h1 {{ margin:0; font-size:20px; line-height:1.2; font-weight:700; }}
    header span {{ color:var(--muted); font-size:14px; }}
    main {{ max-width:1360px; margin:0 auto; padding:24px; }}
    .grid {{ display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); }}
    .stat, .panel {{
      background:linear-gradient(180deg, rgba(255,255,255,.045), rgba(255,255,255,.025)), var(--panel);
      border:1px solid var(--line); border-radius:var(--radius); padding:16px; box-shadow:var(--shadow); overflow-x:auto;
    }}
    .stat {{ min-height:116px; display:flex; flex-direction:column; justify-content:space-between; }}
    .stat b {{ display:block; font-size:30px; line-height:1; margin-top:8px; font-variant-numeric:tabular-nums; letter-spacing:-.02em; }}
    .stat .muted {{ font-size:13px; }}
    h2 {{ font-size:17px; line-height:1.3; margin:0 0 14px; }}
    h3 {{ font-size:15px; line-height:1.35; margin:0 0 12px; }}
    .muted {{ color:var(--muted); }}
    .flash {{ margin:0 0 16px; padding:12px 14px; border-radius:var(--radius); background:var(--accent-soft); border:1px solid rgba(34,197,94,.28); color:#d1fae5; }}
    .error {{ background:var(--danger-soft); border-color:rgba(239,68,68,.36); color:#fecaca; }}
    table {{ width:100%; border-collapse:separate; border-spacing:0; background:rgba(15,23,42,.38); border:1px solid var(--line); border-radius:var(--radius); overflow:hidden; }}
    th, td {{ text-align:left; padding:11px 12px; border-bottom:1px solid var(--line); vertical-align:top; font-size:14px; }}
    th {{ background:rgba(255,255,255,.045); color:var(--soft); font-weight:650; }}
    tr:last-child td {{ border-bottom:0; }}
    tr:hover td {{ background:rgba(255,255,255,.035); }}
    form.inline {{ display:inline; }}
    label {{ display:block; font-size:13px; color:var(--muted); margin:10px 0 5px; }}
    input, select, textarea {{
      width:100%; min-height:44px; padding:10px 11px; border:1px solid rgba(255,255,255,.16);
      border-radius:10px; font:inherit; background:rgba(15,23,42,.72); color:var(--text);
    }}
    input::placeholder, textarea::placeholder {{ color:#778399; }}
    textarea {{ min-height:84px; }}
    button, .button {{
      min-height:44px; display:inline-flex; align-items:center; justify-content:center; gap:8px; border:1px solid rgba(34,197,94,.35);
      background:var(--accent); color:#052e16; padding:10px 13px; border-radius:10px; text-decoration:none; cursor:pointer;
      font:inherit; font-weight:650; transition:background .18s ease, border-color .18s ease, transform .18s ease, filter .18s ease;
    }}
    button:hover, .button:hover {{ filter:brightness(1.04); }}
    button:active, .button:active {{ transform:translateY(1px); }}
    .button.secondary, button.secondary {{ background:#26364f; border-color:rgba(255,255,255,.14); color:var(--text); }}
    .button.danger, button.danger {{ background:var(--danger); border-color:rgba(239,68,68,.45); color:white; }}
    .actions {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; }}
    .two {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:16px; }}
    .badge {{ display:inline-flex; align-items:center; min-height:24px; padding:3px 8px; border-radius:999px; background:rgba(148,163,184,.18); color:#dbe6f7; font-size:12px; border:1px solid rgba(255,255,255,.08); }}
    .badge.ok {{ background:var(--accent-soft); color:#bbf7d0; border-color:rgba(34,197,94,.28); }}
    .badge.wait {{ background:var(--warning-soft); color:#fde68a; border-color:rgba(245,158,11,.28); }}
    .badge.bad {{ background:var(--danger-soft); color:#fecaca; border-color:rgba(239,68,68,.28); }}
    .receipt-img {{ display:block; max-width:280px; max-height:340px; border:1px solid var(--line); border-radius:var(--radius); object-fit:contain; background:#0f172a; }}
    .toolbar {{ display:flex; gap:12px; flex-wrap:wrap; justify-content:space-between; align-items:center; margin:0 0 16px; }}
    .note {{ padding:11px 13px; border:1px solid rgba(34,197,94,.22); background:var(--accent-soft); border-radius:var(--radius); color:#d1fae5; }}
    .activity-list {{ display:grid; gap:8px; }}
    .activity-item {{ display:grid; grid-template-columns:auto 1fr auto; gap:12px; align-items:center; padding:10px 0; border-bottom:1px solid var(--line); }}
    .activity-item:last-child {{ border-bottom:0; }}
    .activity-item a {{ color:var(--text); text-decoration:none; }}
    .activity-item a:hover {{ color:#86efac; text-decoration:underline; }}
    code, pre {{ background:rgba(15,23,42,.72); color:#dbeafe; border:1px solid var(--line); }}
    code {{ padding:2px 5px; border-radius:6px; }}
    pre {{ border-radius:var(--radius); padding:14px; }}
    .meter-grid {{ display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); margin:0 0 16px; }}
    .meter-card {{
      display:flex; align-items:center; gap:14px; min-height:112px; padding:14px;
      border:1px solid var(--line); border-radius:var(--radius); background:rgba(15,23,42,.36);
    }}
    .meter-card strong, .kv-card strong {{ display:block; font-size:16px; line-height:1.3; }}
    .meter-card small, .kv-card small {{ display:block; color:var(--muted); margin-top:4px; line-height:1.4; }}
    .ring {{
      --ring-color:var(--accent); --value:0; flex:0 0 auto; width:74px; height:74px; border-radius:999px;
      display:grid; place-items:center;
      background:conic-gradient(var(--ring-color) var(--value), rgba(255,255,255,.08) 0);
      position:relative; box-shadow:inset 0 0 0 1px rgba(255,255,255,.08);
    }}
    .ring::before {{ content:""; position:absolute; inset:8px; border-radius:inherit; background:#111c33; border:1px solid rgba(255,255,255,.08); }}
    .ring span {{ position:relative; z-index:1; font-weight:800; font-variant-numeric:tabular-nums; }}
    .ring.warn {{ --ring-color:var(--warning); }}
    .ring.bad {{ --ring-color:var(--danger); }}
    .kv-grid {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); }}
    .kv-card {{
      min-height:92px; padding:13px; border:1px solid var(--line); border-radius:var(--radius);
      background:rgba(15,23,42,.36); overflow-wrap:anywhere;
    }}
    .server-shell {{ display:grid; gap:16px; }}
    .server-banner {{
      background:
        linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.02)),
        linear-gradient(145deg, rgba(34,197,94,.08), rgba(96,165,250,.05));
    }}
    .server-banner-top {{
      display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:14px;
    }}
    .server-banner h2 {{ margin:0; font-size:22px; line-height:1.2; }}
    .server-banner .muted {{ max-width:62ch; }}
    .server-strip {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); margin-top:14px; }}
    .server-strip .kv-card {{ min-height:84px; }}
    .server-logbox {{
      max-height:520px; overflow:auto; padding:16px; border-radius:var(--radius);
      background:rgba(7,10,18,.72); border:1px solid var(--line); font-family:Consolas, "SFMono-Regular", monospace;
      font-size:13px; line-height:1.6; white-space:pre-wrap; word-break:break-word;
    }}
    .server-section {{ margin-top:16px; }}
    .settings-hero {{ margin-bottom:16px; }}
    .settings-grid {{
      display:grid;
      gap:16px;
      grid-template-columns:repeat(auto-fit, minmax(320px, 1fr));
      margin-bottom:16px;
    }}
    .settings-stack {{ display:grid; gap:16px; }}
    .settings-card {{ display:grid; gap:14px; }}
    .settings-head {{
      display:flex; justify-content:space-between; align-items:flex-start; gap:12px;
    }}
    .settings-head h2 {{ margin:2px 0 0; font-size:18px; }}
    .field-grid {{
      display:grid; gap:12px; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));
    }}
    .field-grid > div {{ min-width:0; }}
    .field-wide {{ grid-column:1 / -1; }}
    .field-help {{ margin-top:6px; color:var(--muted); font-size:12px; line-height:1.4; }}
    .settings-card form {{ display:grid; gap:12px; }}
    select[multiple] {{
      min-height: 132px;
      width: 100%;
      padding: 10px 12px;
    }}
    .inbound-picker-row {{ display:grid; gap:8px; }}
    .inbound-picker-trigger {{
      width:100%; justify-content:space-between; padding-inline:14px;
    }}
    .inbound-summary {{
      color:var(--muted); font-size:12px; line-height:1.4; margin-top:2px;
      overflow-wrap:anywhere;
    }}
    .inbound-dialog {{
      width:min(760px, calc(100vw - 24px));
      border:1px solid var(--line); border-radius:18px; padding:0;
      background:linear-gradient(180deg, rgba(15,23,42,.98), rgba(15,23,42,.96));
      color:var(--text); box-shadow:var(--shadow);
    }}
    .inbound-dialog::backdrop {{
      background:rgba(2,6,23,.72); backdrop-filter:blur(4px);
    }}
    .inbound-dialog-inner {{ display:grid; gap:14px; padding:18px; }}
    .inbound-dialog-head {{
      display:flex; justify-content:space-between; align-items:flex-start; gap:14px;
      padding-bottom:10px; border-bottom:1px solid var(--line);
    }}
    .inbound-dialog-head h3 {{ margin:0; font-size:18px; }}
    .inbound-dialog-head p {{ margin:6px 0 0; color:var(--muted); font-size:13px; }}
    .inbound-dialog-list {{
      display:grid; gap:10px; max-height:min(52vh, 460px); overflow:auto; padding-right:4px;
    }}
    .inbound-item {{
      display:grid; grid-template-columns:24px minmax(0,1fr); gap:12px; align-items:start;
      padding:12px 13px; border:1px solid var(--line); border-radius:14px;
      background:rgba(255,255,255,.03);
    }}
    .inbound-item:hover {{ background:rgba(255,255,255,.045); }}
    .inbound-item input {{ margin-top:4px; width:18px; height:18px; min-height:18px; }}
    .inbound-item strong {{ display:block; font-size:14px; line-height:1.35; }}
    .inbound-item small {{ display:block; color:var(--muted); margin-top:3px; line-height:1.4; }}
    .inbound-dialog-actions {{
      display:flex; gap:10px; flex-wrap:wrap; justify-content:flex-end; padding-top:6px;
      border-top:1px solid var(--line);
    }}
    @media (max-width: 900px) {{
      .app-shell {{ grid-template-columns:1fr; }}
      .sidebar {{ position:relative; height:auto; padding:12px; border-right:0; border-bottom:1px solid var(--line); }}
      .brand {{ padding-bottom:10px; }}
      nav {{ display:flex; flex-wrap:nowrap; overflow-x:auto; padding-bottom:4px; }}
      nav a {{ white-space:nowrap; }}
      .logout-link {{ margin-top:0; }}
      header {{ padding:16px; align-items:flex-start; flex-direction:column; gap:4px; }}
      main {{ padding:16px; }}
      .activity-item {{ grid-template-columns:1fr; gap:6px; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{ scroll-behavior:auto !important; transition:none !important; animation:none !important; }}
    }}
  </style>
</head>
<body>
  <a class="skip-link" href="#main">Skip to main content</a>
  <div class="{app_class}">
    {nav}
    <div class="content-shell">
      <header><h1>{_esc(title)}</h1><span>ONEBOT Web Admin</span></header>
      <main id="main">{body}</main>
    </div>
  </div>
</body>
</html>"""
    return web.Response(text=html_doc, content_type="text/html")


def _flash(request: web.Request) -> str:
    msg = request.query.get("msg", "")
    err = request.query.get("err", "")
    if err:
        return f'<div class="flash error">{_esc(err)}</div>'
    if msg:
        return f'<div class="flash">{_esc(msg)}</div>'
    return ""


def _dt_sort_key(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.min.replace(tzinfo=timezone.utc)


async def _recent_activity_items(limit: int = 20) -> list[dict[str, str]]:
    async with AsyncSessionLocal() as session:
        users = (
            await session.execute(select(User).order_by(User.created_at.desc()).limit(limit))
        ).scalars().all()
        payments = (
            await session.execute(select(Payment).order_by(Payment.updated_at.desc()).limit(limit))
        ).scalars().all()
        subs = (
            await session.execute(select(Subscription).order_by(Subscription.created_at.desc()).limit(limit))
        ).scalars().all()
        tickets = (
            await session.execute(select(Ticket).order_by(Ticket.updated_at.desc()).limit(limit))
        ).scalars().all()
    activity_logs = await get_recent_activity_logs(limit=limit)

    items: list[dict[str, Any]] = []
    for user in users:
        items.append({
            "time": user.created_at,
            "kind": "Customer",
            "text": f"New customer {user.telegram_id} @{user.username or '-'}",
            "href": f"/admin/customers/{user.id}",
        })
    for payment in payments:
        items.append({
            "time": payment.updated_at or payment.created_at,
            "kind": "Payment",
            "text": f"{payment.payment_method} payment {payment.order_id} is {payment.status}",
            "href": f"/admin/payments?q={quote(payment.order_id)}",
        })
    for sub in subs:
        items.append({
            "time": sub.created_at,
            "kind": "Subscription",
            "text": f"Subscription {sub.email} created with status {sub.status}",
            "href": f"/admin/customers/{sub.user_id}",
        })
    for ticket in tickets:
        items.append({
            "time": ticket.updated_at or ticket.created_at,
            "kind": "Ticket",
            "text": f"Ticket #{ticket.id}: {ticket.subject} is {ticket.status}",
            "href": f"/admin/customers/{ticket.user_id}",
        })
    for log in activity_logs:
        direction = "Incoming" if log.direction == "incoming" else "Outgoing"
        who = f" {log.telegram_id}" if log.telegram_id else ""
        username = f" @{log.username}" if log.username else ""
        items.append({
            "time": log.created_at,
            "kind": direction,
            "text": f"{log.event_type}{who}{username}: {log.text}",
            "href": "/admin",
        })

    items.sort(key=lambda item: _dt_sort_key(item["time"]), reverse=True)
    result = []
    for item in items[:limit]:
        t = item["time"]
        result.append({
            "time": t.strftime("%Y-%m-%d %H:%M:%S") if isinstance(t, datetime) else "",
            "kind": str(item["kind"]),
            "text": str(item["text"]),
            "href": str(item["href"]),
        })
    return result


def _activity_html(items: list[dict[str, str]]) -> str:
    if not items:
        return '<p class="muted">No recent activity yet.</p>'
    return "".join(
        f"""<div class="activity-item">
          <span class="badge">{_esc(item["kind"])}</span>
          <a href="{_esc(item["href"])}">{_esc(item["text"])}</a>
          <span class="muted">{_esc(item["time"])}</span>
        </div>"""
        for item in items
    )


async def login_get(request: web.Request) -> web.Response:
    body = f"""
    {_flash(request)}
    <div class="panel" style="max-width:420px;margin:40px auto">
      <h2>Login</h2>
      <form method="post" action="/admin/login">
        <input type="hidden" name="next" value="{_esc(request.query.get('next', '/admin'))}">
        <label>Username</label>
        <input name="username" autocomplete="username" autofocus>
        <label>Password</label>
        <input name="password" type="password" autocomplete="current-password">
        <p><button type="submit">Sign in</button></p>
      </form>
      <p class="muted">Defaults come from WEB_ADMIN_USERNAME and WEB_ADMIN_PASSWORD. If no web password is set, ADMIN_SECRET is used.</p>
    </div>
    """
    return _layout("Login", body)


async def login_post(request: web.Request) -> web.Response:
    form = await request.post()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))
    expected_user = settings.web_admin_username or "admin"
    expected_pass = _web_password()
    if username == expected_user and expected_pass and hmac.compare_digest(password, expected_pass):
        resp = web.HTTPFound(str(form.get("next", "/admin")) or "/admin")
        resp.set_cookie(
            "nexora_admin",
            _make_cookie(username),
            httponly=True,
            samesite="Lax",
            max_age=60 * 60 * 12,
        )
        raise resp
    _redirect("/admin/login", err="Invalid username or password")


async def logout(request: web.Request) -> web.Response:
    resp = web.HTTPFound("/admin/login?msg=Signed+out")
    resp.del_cookie("nexora_admin")
    raise resp


async def dashboard(request: web.Request) -> web.Response:
    _require_auth(request)
    async with AsyncSessionLocal() as session:
        stats = await get_stats(session)
        recent_payments = await get_payments_filtered(session, limit=8)
        users = (
            await session.execute(select(User).order_by(User.created_at.desc()).limit(8))
        ).scalars().all()
    activity_items = await _recent_activity_items(limit=16)

    cards = "".join(
        f'<div class="stat"><span class="muted">{_esc(label)}</span><b>{_esc(value)}</b></div>'
        for label, value in [
            ("Users", stats["total_users"]),
            ("New today", stats["users_today"]),
            ("Active subs", stats["active_subscriptions"]),
            ("Expiring soon", stats["expiring_soon"]),
            ("Revenue Dollars", _money(stats["total_revenue_usdt"])),
            ("Pending payments", stats["payments_pending"]),
        ]
    )
    recent_user_rows = "".join(
        f"<tr><td>{u.id}</td><td>{u.telegram_id}</td><td>@{_esc(u.username or '-')}</td><td>{_esc(u.first_name or '-')}</td><td>{_esc(u.created_at)}</td></tr>"
        for u in users
    )
    recent_payment_rows = "".join(
        f"<tr><td>{_esc(p.order_id)}</td><td>{_money(p.amount_usdt)}</td><td>{_esc(p.payment_method)}</td><td><span class='badge'>{_esc(p.status)}</span></td><td>{_esc(p.created_at)}</td></tr>"
        for p in recent_payments
    )
    body = f"""
    {_flash(request)}
    <div class="grid">{cards}</div>
    <section class="panel" style="margin-top:16px">
      <div class="toolbar">
        <h2 style="margin:0">Live recent actions</h2>
        <span class="muted">Auto-refreshes every 10 seconds</span>
      </div>
      <div id="activity-feed" class="activity-list">{_activity_html(activity_items)}</div>
    </section>
    <div class="two" style="margin-top:16px">
      <section class="panel"><h2>Recent customers</h2><table><tr><th>ID</th><th>Telegram</th><th>Username</th><th>Name</th><th>Created</th></tr>{recent_user_rows}</table></section>
      <section class="panel"><h2>Recent payments</h2><table><tr><th>Order</th><th>Dollars</th><th>Method</th><th>Status</th><th>Created</th></tr>{recent_payment_rows}</table></section>
    </div>
    <script>
      async function refreshActivity() {{
        try {{
          const response = await fetch('/admin/activity', {{cache: 'no-store'}});
          if (!response.ok) return;
          const data = await response.json();
          const feed = document.getElementById('activity-feed');
          if (!feed) return;
          feed.innerHTML = data.items.map((item) => `
            <div class="activity-item">
              <span class="badge">${{escapeHtml(item.kind)}}</span>
              <a href="${{escapeAttr(item.href)}}">${{escapeHtml(item.text)}}</a>
              <span class="muted">${{escapeHtml(item.time)}}</span>
            </div>
          `).join('') || '<p class="muted">No recent activity yet.</p>';
        }} catch (_) {{}}
      }}
      function escapeHtml(value) {{
        return String(value ?? '').replace(/[&<>"']/g, (c) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
      }}
      function escapeAttr(value) {{
        const text = String(value ?? '');
        return text.startsWith('/admin') ? escapeHtml(text) : '#';
      }}
      setInterval(refreshActivity, 10000);
    </script>
    """
    return _layout("Dashboard", body, request)


async def dashboard_activity(request: web.Request) -> web.Response:
    _require_auth(request)
    return web.json_response({"items": await _recent_activity_items(limit=16)})


async def stats_page(request: web.Request) -> web.Response:
    _require_auth(request)
    async with AsyncSessionLocal() as session:
        stats = await get_stats(session)
        payment_methods = (
            await session.execute(
                select(Payment.payment_method, func.count(Payment.id), func.sum(Payment.amount_usdt))
                .group_by(Payment.payment_method)
            )
        ).all()
        sub_statuses = (
            await session.execute(
                select(Subscription.status, func.count(Subscription.id))
                .group_by(Subscription.status)
            )
        ).all()
    stat_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>"
        for k, v in stats.items()
    )
    payment_rows = "".join(
        f"<tr><td>{_esc(method)}</td><td>{count}</td><td>{_money(total or 0)}</td></tr>"
        for method, count, total in payment_methods
    )
    sub_rows = "".join(
        f"<tr><td>{_esc(status)}</td><td>{count}</td></tr>"
        for status, count in sub_statuses
    )
    body = f"""
    {_flash(request)}
    <div class="two">
      <section class="panel"><h2>Full stats</h2><table><tr><th>Metric</th><th>Value</th></tr>{stat_rows}</table></section>
      <section class="panel"><h2>Payment methods</h2><table><tr><th>Method</th><th>Count</th><th>Dollars</th></tr>{payment_rows}</table></section>
      <section class="panel"><h2>Subscription statuses</h2><table><tr><th>Status</th><th>Count</th></tr>{sub_rows}</table></section>
    </div>
    """
    return _layout("Stats", body, request)


async def server_page(request: web.Request) -> web.Response:
    _require_auth(request)
    status_html = ""
    logs = ""
    try:
        async with XUIClient(
            panel_url=settings.panel_url,
            username=settings.panel_username,
            password=settings.panel_password,
            api_path=settings.panel_api_path,
            sub_port=settings.sub_port,
        ) as xui:
            status = await xui.get_server_status()
            logs = await xui.get_xray_logs(count=80)

        def tone(percent: float) -> str:
            if percent >= 85:
                return "bad"
            if percent >= 70:
                return "warn"
            return "ok"

        mem = status.get("mem") if isinstance(status.get("mem"), dict) else {}
        swap = status.get("swap") if isinstance(status.get("swap"), dict) else {}
        disk = status.get("disk") if isinstance(status.get("disk"), dict) else {}
        xray = status.get("xray") if isinstance(status.get("xray"), dict) else {}
        public_ip = status.get("publicIP") if isinstance(status.get("publicIP"), dict) else {}
        app_stats = status.get("appStats") if isinstance(status.get("appStats"), dict) else {}
        disk_io = status.get("diskIO") if isinstance(status.get("diskIO"), dict) else {}
        disk_traffic = status.get("diskTraffic") if isinstance(status.get("diskTraffic"), dict) else {}
        net_io = status.get("netIO") if isinstance(status.get("netIO"), dict) else {}
        net_traffic = status.get("netTraffic") if isinstance(status.get("netTraffic"), dict) else {}
        loads = status.get("loads") if isinstance(status.get("loads"), list) else []

        cpu_percent = max(0.0, min(100.0, float(status.get("cpu") or 0)))
        mem_percent = _usage_percent(mem.get("current"), mem.get("total"))
        swap_percent = _usage_percent(swap.get("current"), swap.get("total"))
        disk_percent = _usage_percent(disk.get("current"), disk.get("total"))

        meters = "".join([
            _meter_card(
                "CPU",
                cpu_percent,
                f"{status.get('cpuCores', '-')} core, {status.get('logicalPro', '-')} logical, {status.get('cpuSpeedMhz', '-')} MHz",
                tone(cpu_percent),
            ),
            _meter_card(
                "Memory",
                mem_percent,
                f"{_fmt_bytes(mem.get('current'))} of {_fmt_bytes(mem.get('total'))}",
                tone(mem_percent),
            ),
            _meter_card(
                "Disk",
                disk_percent,
                f"{_fmt_bytes(disk.get('current'))} of {_fmt_bytes(disk.get('total'))}",
                tone(disk_percent),
            ),
            _meter_card(
                "Swap",
                swap_percent,
                f"{_fmt_bytes(swap.get('current'))} of {_fmt_bytes(swap.get('total'))}",
                tone(swap_percent),
            ),
        ])

        runtime_cards = "".join([
            _kv_card("Xray", xray.get("state", "-"), f"Version {xray.get('version', '-')}"),
            _kv_card("Panel version", status.get("panelVersion", "-"), f"GUID {status.get('panelGuid', '-')}"),
            _kv_card("Server uptime", _fmt_uptime(status.get("uptime")), f"{status.get('uptime', 0)} seconds"),
            _kv_card("App process", f"{app_stats.get('threads', '-')} threads", f"{_fmt_bytes(app_stats.get('mem'))}, uptime {_fmt_uptime(app_stats.get('uptime'))}"),
            _kv_card("Load average", ", ".join(str(x) for x in loads) or "-", "1, 5, 15 minute load"),
            _kv_card("Connections", f"TCP {status.get('tcpCount', 0)}", f"UDP {status.get('udpCount', 0)}"),
        ])

        network_cards = "".join([
            _kv_card("Public IPv4", public_ip.get("ipv4", "-")),
            _kv_card("Public IPv6", public_ip.get("ipv6", "-")),
            _kv_card("Network I/O", f"Up {_fmt_bytes(net_io.get('up'))}/s", f"Down {_fmt_bytes(net_io.get('down'))}/s"),
            _kv_card("Network traffic", f"Sent {_fmt_bytes(net_traffic.get('sent'))}", f"Received {_fmt_bytes(net_traffic.get('recv'))}"),
            _kv_card("Disk I/O", f"Read {_fmt_bytes(disk_io.get('read'))}/s", f"Write {_fmt_bytes(disk_io.get('write'))}/s"),
            _kv_card("Disk traffic", f"Read {_fmt_bytes(disk_traffic.get('read'))}", f"Write {_fmt_bytes(disk_traffic.get('write'))}"),
        ])

        xray_error = str(xray.get("errorMsg", "") or "").strip()
        error_html = f'<div class="flash error">Xray error: {_esc(xray_error)}</div>' if xray_error else ""
        status_html = f"""
        {error_html}
        <div class="meter-grid">{meters}</div>
        <div class="server-section">
          <div class="toolbar"><h2 style="margin:0">Runtime</h2><span class="badge {_esc('ok' if xray.get('state') == 'running' else 'bad')}">{_esc(xray.get('state', 'unknown'))}</span></div>
          <div class="kv-grid">{runtime_cards}</div>
        </div>
        <div class="server-section">
          <h2>Network and storage</h2>
          <div class="kv-grid">{network_cards}</div>
        </div>
        """
    except Exception as exc:
        status_html = f'<div class="flash error">Could not read server status: {_esc(exc)}</div>'
    body = f"""
    {_flash(request)}
    <div class="server-shell">
      <section class="panel server-banner">
        <div class="server-banner-top">
          <div>
            <div class="muted">3X-UI server snapshot</div>
            <h2>Server status</h2>
            <p class="muted">Live operational view for CPU, memory, disk, network, and Xray health.</p>
          </div>
          <form method="post" action="/admin/server/restart-xray">
            <button class="danger">Restart Xray</button>
          </form>
        </div>
        <div class="server-strip">
          {_kv_card("Xray", xray.get("state", "-"), f"Version {xray.get('version', '-')}")}
          {_kv_card("Panel", status.get("panelVersion", "-"), f"GUID {status.get('panelGuid', '-')}")}
          {_kv_card("Uptime", _fmt_uptime(status.get("uptime")), f"{status.get('uptime', 0)} seconds")}
          {_kv_card("Public IP", public_ip.get("ipv4", "-"), public_ip.get("ipv6", "-"))}
        </div>
      </section>
      <section class="panel">
        {status_html}
      </section>
      <section class="panel">
        <div class="toolbar"><h2 style="margin:0">Xray logs</h2><span class="muted">Last 80 lines</span></div>
        <div class="server-logbox">{_esc(logs)}</div>
      </section>
    </div>
    """
    return _layout("Server", body, request)


async def server_restart_xray(request: web.Request) -> web.Response:
    _require_auth(request)
    try:
        async with XUIClient(
            panel_url=settings.panel_url,
            username=settings.panel_username,
            password=settings.panel_password,
            api_path=settings.panel_api_path,
            sub_port=settings.sub_port,
        ) as xui:
            await xui.restart_xray()
        _redirect("/admin/server", msg="Xray restarted")
    except Exception as exc:
        _redirect("/admin/server", err=f"Could not restart Xray: {exc}")


async def customers(request: web.Request) -> web.Response:
    _require_auth(request)
    q = request.query.get("q", "").strip()
    async with AsyncSessionLocal() as session:
        stmt = select(User)
        if q:
            like = f"%{q}%"
            conditions = [User.username.ilike(like), User.first_name.ilike(like)]
            if q.lstrip("-").isdigit():
                conditions.append(User.telegram_id == int(q))
                conditions.append(User.id == int(q))
            from sqlalchemy import or_
            stmt = stmt.where(or_(*conditions))
        rows = (await session.execute(stmt.order_by(User.created_at.desc()).limit(100))).scalars().all()
        counts = {}
        for uid, total in (
            await session.execute(
                select(Subscription.user_id, func.count(Subscription.id)).group_by(Subscription.user_id)
            )
        ).all():
            counts[uid] = total
        blocked_ids = await get_blocked_user_ids(session)
    trs = "".join(
        f"""<tr>
          <td><a href="/admin/customers/{u.id}">{u.id}</a></td>
          <td>{u.telegram_id}</td>
          <td><a href="/admin/customers/{u.id}">@{_esc(u.username or "-")}</a></td>
          <td><a href="/admin/customers/{u.id}">{_esc(u.first_name or "-")}</a></td>
          <td>{"<span class='badge bad'>blocked</span>" if u.telegram_id in blocked_ids else "<span class='badge ok'>active</span>"}</td>
          <td>{counts.get(u.id, 0)}</td>
          <td>{_esc(u.created_at)}</td>
          <td><a class="button secondary" href="/admin/customers/{u.id}">Manage</a></td>
        </tr>"""
        for u in rows
    )
    body = f"""
    {_flash(request)}
    <section class="panel">
      <h2>Add customer</h2>
      <form method="post" action="/admin/customers/create" class="two">
        <div><label>Telegram ID</label><input name="telegram_id" type="number" required></div>
        <div><label>Username</label><input name="username" placeholder="without @"></div>
        <div><label>First name</label><input name="first_name"></div>
        <div><label>Admin</label><select name="is_admin"><option value="0">no</option><option value="1">yes</option></select></div>
        <div style="align-self:end"><button>Create customer</button></div>
      </form>
    </section>
    <div class="panel">
      <form method="get" class="actions">
        <input name="q" value="{_esc(q)}" placeholder="Search id, telegram id, username, name" style="max-width:420px">
        <button>Search</button>
        <a class="button secondary" href="/admin/customers">Reset</a>
      </form>
    </div>
    <p class="muted">Showing up to 100 customers.</p>
    <table><tr><th>ID</th><th>Telegram</th><th>Username</th><th>Name</th><th>Access</th><th>Subs</th><th>Created</th><th>Actions</th></tr>{trs}</table>
    """
    return _layout("Customers", body, request)


async def customer_detail(request: web.Request) -> web.Response:
    _require_auth(request)
    user_id = int(request.match_info["user_id"])
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(
                select(User)
                .where(User.id == user_id)
                .options(selectinload(User.subscriptions), selectinload(User.payments))
            )
        ).scalar_one_or_none()
        plans = await get_all_plans(session)
        blocked_ids = await get_blocked_user_ids(session)
    if not user:
        _redirect("/admin/customers", err="Customer not found")
    is_blocked = user.telegram_id in blocked_ids
    plan_options = "".join(
        f'<option value="{p.id}">#{p.id} - {_esc(p.name)} ({_money(p.price_usdt)} dollars)</option>'
        for p in plans
    )
    sub_rows = "".join(
        f'<tr>'
          f'<td>{s.id}</td>'
          f'<td><a href="{_esc(build_sub_link_for(settings.panel_url, s.sub_id, settings.sub_port))}" target="_blank" rel="noopener noreferrer">{_esc(s.email)}</a></td>'
          f'<td>{s.inbound_id}</td>'
          f'<td>{s.traffic_limit_gb}</td>'
          f'<td>{_gb_from_bytes(s.used_traffic_bytes)}</td>'
          f'<td>{_esc(s.expiry_date or "-")}</td>'
          f'<td><span class="badge">{_esc(s.status)}</span></td>'
          f'<td class="actions">'
            f'<a class="button secondary" href="{_esc(build_sub_link_for(settings.panel_url, s.sub_id, settings.sub_port))}" target="_blank" rel="noopener noreferrer">Open</a>'
            f'<form class="inline" method="post" action="/admin/customers/{user.id}/subscriptions/{s.id}/rotate">'
              f'<button class="secondary">Rotate link</button>'
            f'</form>'
            f'<form class="inline" method="post" action="/admin/customers/{user.id}/subscriptions/{s.id}/detach">'
              f'<button class="secondary">Detach</button>'
            f'</form>'
            f'<form class="inline" method="post" action="/admin/customers/{user.id}/subscriptions/{s.id}/remove">'
              f'<button class="danger">Remove from panel</button>'
            f'</form>'
          f'</td>'
        f'</tr>'
        for s in user.subscriptions
    )
    pay_rows = "".join(
        f"<tr><td>{_esc(p.order_id)}</td><td>{_money(p.amount_usdt)}</td><td>{_esc(p.payment_method)}</td><td>{_esc(p.status)}</td><td>{_esc(p.created_at)}</td></tr>"
        for p in user.payments
    )
    body = f"""
    {_flash(request)}
    <p><a href="/admin/customers">Back to customers</a></p>
    <section class="panel">
      <h2>Customer #{user.id}</h2>
      <p>Telegram: <code>{user.telegram_id}</code> | Username: @{_esc(user.username or "-")} | Name: {_esc(user.first_name or "-")}</p>
      <p>Created: {_esc(user.created_at)} | Admin: {_esc(user.is_admin)} | Access: {"<span class='badge bad'>blocked</span>" if is_blocked else "<span class='badge ok'>active</span>"}</p>
      <div class="actions">
        <form class="inline" method="post" action="/admin/customers/{user.id}/toggle-admin">
          <button class="secondary">{"Remove admin" if user.is_admin else "Make admin"}</button>
        </form>
        <form class="inline" method="post" action="/admin/customers/{user.id}/block">
          <input type="hidden" name="blocked" value="{"0" if is_blocked else "1"}">
          <button class="{"secondary" if is_blocked else "danger"}">{"Unblock customer" if is_blocked else "Block customer"}</button>
        </form>
        <form class="inline" method="post" action="/admin/customers/{user.id}/delete">
          <button class="danger">Delete customer</button>
        </form>
      </div>
    </section>
    <section class="panel" style="margin-top:16px">
      <h2>Message user</h2>
      <p class="muted">Sends a direct Telegram message to this customer.</p>
      <form method="post" action="/admin/customers/{user.id}/message">
        <label>Message</label>
        <textarea name="text" placeholder="Write the message to send to the customer" required></textarea>
        <div class="actions" style="margin-top:12px">
          <button>Send message</button>
        </div>
      </form>
    </section>
    <section class="panel" style="margin-top:16px">
      <h2>Attach plan</h2>
      <p class="muted">Creates a new VPN client in 3X-UI and attaches the subscription to this customer.</p>
      <form method="post" action="/admin/customers/{user.id}/attach-plan" class="actions">
        <select name="plan_id" style="max-width:420px">{plan_options}</select>
        <button>Attach selected plan</button>
      </form>
    </section>
    <h2>Subscriptions</h2>
    <table><tr><th>ID</th><th>Email</th><th>Inbound</th><th>Limit GB</th><th>Used GB</th><th>Expiry</th><th>Status</th><th>Actions</th></tr>{sub_rows}</table>
    <h2>Payments</h2>
    <table><tr><th>Order</th><th>Dollars</th><th>Method</th><th>Status</th><th>Created</th></tr>{pay_rows}</table>
    """
    return _layout("Customer", body, request)


async def customer_create(request: web.Request) -> web.Response:
    _require_auth(request)
    form = await request.post()
    try:
        telegram_id = int(form.get("telegram_id", 0) or 0)
        if telegram_id <= 0:
            raise ValueError("Telegram ID is required")
        async with AsyncSessionLocal() as session:
            user, _ = await get_or_create_user(
                session,
                telegram_id=telegram_id,
                username=str(form.get("username", "")).lstrip("@").strip() or None,
                first_name=str(form.get("first_name", "")).strip() or None,
                admin_ids=settings.admin_ids,
            )
            user.is_admin = str(form.get("is_admin", "0")) == "1"
            await session.commit()
        _redirect(f"/admin/customers/{user.id}", msg="Customer saved")
    except Exception as exc:
        _redirect("/admin/customers", err=f"Could not create customer: {exc}")


async def customer_toggle_admin(request: web.Request) -> web.Response:
    _require_auth(request)
    user_id = int(request.match_info["user_id"])
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            _redirect("/admin/customers", err="Customer not found")
        user.is_admin = not user.is_admin
        await session.commit()
    _redirect(f"/admin/customers/{user_id}", msg="Customer admin flag updated")


async def customer_message(request: web.Request) -> web.Response:
    _require_auth(request)
    user_id = int(request.match_info["user_id"])
    form = await request.post()
    text = str(form.get("text", "")).strip()
    if not text:
        _redirect(f"/admin/customers/{user_id}", err="Message cannot be empty")
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            _redirect("/admin/customers", err="Customer not found")
    try:
        bot = ActivityLoggingBot(token=settings.bot_token)
        try:
            await bot.send_message(user.telegram_id, text, parse_mode="HTML")
        finally:
            await bot.session.close()
        _redirect(f"/admin/customers/{user_id}", msg="Message sent to customer")
    except Exception as exc:
        _redirect(f"/admin/customers/{user_id}", err=f"Could not send message: {exc}")


async def customer_block(request: web.Request) -> web.Response:
    _require_auth(request)
    user_id = int(request.match_info["user_id"])
    form = await request.post()
    blocked = str(form.get("blocked", "1")) == "1"
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            _redirect("/admin/customers", err="Customer not found")
        await set_user_blocked(session, user.telegram_id, blocked)
    _redirect(
        f"/admin/customers/{user_id}",
        msg="Customer blocked" if blocked else "Customer unblocked",
    )


async def customer_delete(request: web.Request) -> web.Response:
    _require_auth(request)
    user_id = int(request.match_info["user_id"])
    try:
        async with AsyncSessionLocal() as session:
            user = (
                await session.execute(
                    select(User).where(User.id == user_id).options(selectinload(User.subscriptions))
                )
            ).scalar_one_or_none()
            if not user:
                _redirect("/admin/customers", err="Customer not found")
            for sub in list(user.subscriptions):
                await _detach_subscription_from_panel(sub)
            await session.delete(user)
            await session.commit()
        _redirect("/admin/customers", msg="Customer deleted")
    except Exception as exc:
        _redirect(f"/admin/customers/{user_id}", err=f"Could not delete customer: {exc}")


async def customer_attach_plan(request: web.Request) -> web.Response:
    _require_auth(request)
    user_id = int(request.match_info["user_id"])
    form = await request.post()
    plan_id = int(form.get("plan_id", 0) or 0)
    try:
        async with AsyncSessionLocal() as session:
            user = await session.get(User, user_id)
            plan = await get_plan(session, plan_id)
            if not user:
                _redirect("/admin/customers", err="Customer not found")
            if not plan:
                _redirect(f"/admin/customers/{user_id}", err="Plan not found")
            await create_new_subscription(
                session=session,
                user_id=user.id,
                telegram_id=user.telegram_id,
                inbound_id=0,
                traffic_gb=plan.traffic_gb,
                expire_days=plan.duration_days,
                plan_id=plan.id,
            )
        _redirect(f"/admin/customers/{user_id}", msg=f"Plan attached: {plan.name}")
    except Exception as exc:
        _redirect(f"/admin/customers/{user_id}", err=f"Could not attach plan: {exc}")


async def _delete_subscription_from_panel(sub: Subscription) -> None:
    async with XUIClient(
        panel_url=settings.panel_url,
        username=settings.panel_username,
        password=settings.panel_password,
        api_path=settings.panel_api_path,
        sub_port=settings.sub_port,
    ) as xui:
        await xui.delete_client(sub.email)


def _is_missing_panel_client_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        needle in msg
        for needle in (
            "found",
            "not found",
            "client not found",
            "already removed",
            "no such client",
            "deleted",
        )
    )


async def _detach_subscription_from_panel(sub: Subscription) -> None:
    try:
        await _delete_subscription_from_panel(sub)
    except XUIError as exc:
        if not _is_missing_panel_client_error(exc):
            raise
        # Local state still needs to move forward if the panel is unreachable
        # or the client was already removed there.
        pass


async def customer_detach_subscription(request: web.Request) -> web.Response:
    _require_auth(request)
    user_id = int(request.match_info["user_id"])
    sub_id = int(request.match_info["sub_id"])
    async with AsyncSessionLocal() as session:
        sub = (
            await session.execute(
                select(Subscription).where(Subscription.id == sub_id, Subscription.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not sub:
            _redirect(f"/admin/customers/{user_id}", err="Subscription not found")
        await _detach_subscription_from_panel(sub)
        await update_subscription_status(session, sub.id, "deleted")
    _redirect(f"/admin/customers/{user_id}", msg="Subscription detached and marked deleted")


async def customer_remove_subscription(request: web.Request) -> web.Response:
    _require_auth(request)
    user_id = int(request.match_info["user_id"])
    sub_id = int(request.match_info["sub_id"])
    try:
        async with AsyncSessionLocal() as session:
            sub = (
                await session.execute(
                    select(Subscription).where(Subscription.id == sub_id, Subscription.user_id == user_id)
                )
            ).scalar_one_or_none()
            if not sub:
                _redirect(f"/admin/customers/{user_id}", err="Subscription not found")
            await delete_subscription_completely(session, sub)
        _redirect(f"/admin/customers/{user_id}", msg="Subscription removed from panel and bot database")
    except Exception as exc:
        _redirect(f"/admin/customers/{user_id}", err=f"Could not remove subscription: {exc}")


async def customer_rotate_subscription(request: web.Request) -> web.Response:
    _require_auth(request)
    user_id = int(request.match_info["user_id"])
    sub_id = int(request.match_info["sub_id"])
    try:
        async with AsyncSessionLocal() as session:
            sub = (
                await session.execute(
                    select(Subscription).where(Subscription.id == sub_id, Subscription.user_id == user_id)
                )
            ).scalar_one_or_none()
            if not sub:
                _redirect(f"/admin/customers/{user_id}", err="Subscription not found")
            await rotate_subscription_link(session, sub.id)
        _redirect(f"/admin/customers/{user_id}", msg="Subscription link regenerated")
    except Exception as exc:
        _redirect(f"/admin/customers/{user_id}", err=f"Could not rotate subscription link: {exc}")


async def inbounds_get(request: web.Request) -> web.Response:
    _require_auth(request)
    try:
        async with XUIClient(
            panel_url=settings.panel_url,
            username=settings.panel_username,
            password=settings.panel_password,
            api_path=settings.panel_api_path,
            sub_port=settings.sub_port,
        ) as xui:
            inbounds = await xui.get_inbounds()
    except Exception as exc:
        _redirect("/admin", err=f"Could not read panel inbounds: {exc}")

    async with AsyncSessionLocal() as session:
        enabled = set(await get_enabled_inbound_ids(session))

    rows = "".join(
        f"""<tr>
          <td><input type="checkbox" name="inbound_id" value="{ib.id}" {"checked" if ib.id in enabled else ""}></td>
          <td>{ib.id}</td>
          <td>{_esc(ib.remark)}</td>
          <td>{_esc(ib.protocol)}</td>
          <td>{ib.port}</td>
          <td><span class="badge">{'enabled' if ib.enable else 'disabled'}</span></td>
          <td>{_gb_from_bytes(ib.up + ib.down)}</td>
        </tr>"""
        for ib in inbounds
    )
    body = f"""
    {_flash(request)}
    <section class="panel">
      <h2>3X-UI Inbounds</h2>
      <p class="muted">Select which live panel inbounds the bot can use when creating subscriptions. If none are selected, the bot now falls back to the first enabled inbound from the panel.</p>
      <form method="post" action="/admin/inbounds">
        <table><tr><th>Use</th><th>ID</th><th>Remark</th><th>Protocol</th><th>Port</th><th>Status</th><th>Used GB</th></tr>{rows}</table>
        <p><button>Save enabled inbounds</button></p>
      </form>
    </section>
    """
    return _layout("Inbounds", body, request)


async def inbounds_post(request: web.Request) -> web.Response:
    _require_auth(request)
    form = await request.post()
    selected = [int(v) for v in form.getall("inbound_id") if str(v).isdigit()]
    async with AsyncSessionLocal() as session:
        await set_enabled_inbound_ids(session, selected)
    _redirect("/admin/inbounds", msg="Enabled inbounds saved")


async def inbounds_json(request: web.Request) -> web.Response:
    _require_auth(request)
    try:
        async with XUIClient(
            panel_url=settings.panel_url,
            username=settings.panel_username,
            password=settings.panel_password,
            api_path=settings.panel_api_path,
            sub_port=settings.sub_port,
        ) as xui:
            inbounds = await xui.get_inbounds()
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=500)

    payload = [
        {
            "id": ib.id,
            "remark": ib.remark,
            "protocol": ib.protocol,
            "port": ib.port,
            "enable": bool(ib.enable),
            "used_gb": _gb_from_bytes(ib.up + ib.down),
        }
        for ib in inbounds
    ]
    return web.json_response({"ok": True, "items": payload})


async def tickets_page(request: web.Request) -> web.Response:
    _require_auth(request)
    status = request.query.get("status", "open")
    async with AsyncSessionLocal() as session:
        stmt = select(Ticket, User).join(User, User.id == Ticket.user_id).order_by(Ticket.updated_at.desc()).limit(100)
        if status != "all":
            stmt = stmt.where(Ticket.status == status)
        rows = (await session.execute(stmt)).all()
    tr = ""
    for ticket, user in rows:
        actions = f"""
        <div class="actions">
          <form method="post" action="/admin/tickets/{ticket.id}/status"><input type="hidden" name="status" value="closed"><button class="secondary">Close</button></form>
          <form method="post" action="/admin/tickets/{ticket.id}/status"><input type="hidden" name="status" value="open"><button>Reopen</button></form>
        </div>
        """
        tr += f"""<tr>
          <td>#{ticket.id}<br>{_status_badge(ticket.status)}</td>
          <td><a href="/admin/customers/{user.id}">{user.telegram_id}</a><br>@{_esc(user.username or "-")}</td>
          <td>{_esc(ticket.subject)}</td>
          <td>{_esc(ticket.updated_at)}</td>
          <td>{actions}</td>
        </tr>"""
    options = "".join(
        f'<option value="{value}" {"selected" if status == value else ""}>{label}</option>'
        for value, label in [("open", "Open"), ("in_progress", "In progress"), ("closed", "Closed"), ("all", "All")]
    )
    body = f"""
    {_flash(request)}
    <div class="panel"><form method="get" class="actions"><select name="status" style="max-width:220px">{options}</select><button>Filter</button></form></div>
    <table><tr><th>Ticket</th><th>Customer</th><th>Subject</th><th>Updated</th><th>Actions</th></tr>{tr}</table>
    """
    return _layout("Tickets", body, request)


async def ticket_set_status(request: web.Request) -> web.Response:
    _require_auth(request)
    ticket_id = int(request.match_info["ticket_id"])
    form = await request.post()
    status = str(form.get("status", "open"))
    if status not in {"open", "in_progress", "closed"}:
        _redirect("/admin/tickets", err="Invalid ticket status")
    values = {"status": status, "updated_at": datetime.now(timezone.utc)}
    if status == "closed":
        values["closed_at"] = datetime.now(timezone.utc)
    elif status == "open":
        values["closed_at"] = None
    async with AsyncSessionLocal() as session:
        await session.execute(update(Ticket).where(Ticket.id == ticket_id).values(**values))
        await session.commit()
    _redirect("/admin/tickets", msg="Ticket updated")


async def plans_get(request: web.Request) -> web.Response:
    _require_auth(request)
    panel_inbounds: list[Any] = []
    panel_error = ""
    try:
        async with XUIClient(
            panel_url=settings.panel_url,
            username=settings.panel_username,
            password=settings.panel_password,
            api_path=settings.panel_api_path,
            sub_port=settings.sub_port,
        ) as xui:
            panel_inbounds = await xui.get_inbounds()
    except Exception as exc:
        panel_error = str(exc)

    async with AsyncSessionLocal() as session:
        plans = await get_all_plans(session)

    inbound_payload = json.dumps(
        [
            {
                "id": ib.id,
                "remark": ib.remark,
                "protocol": ib.protocol,
                "port": ib.port,
                "enable": bool(ib.enable),
                "used_gb": _gb_from_bytes(ib.up + ib.down),
            }
            for ib in panel_inbounds
        ],
        ensure_ascii=False,
    )

    def inbound_picker(selected_ids: set[int], input_id: str, summary_id: str, label: str = "Allowed inbounds") -> str:
        hidden_value = ",".join(str(i) for i in sorted(selected_ids))
        selected_summary = ", ".join(
            f"#{iid} {inbound_lookup[iid].remark}" if iid in inbound_lookup else f"#{iid}"
            for iid in sorted(selected_ids)
        ) or "Global enabled inbounds"
        return f"""
        <div class="inbound-picker-row">
          <label>{_esc(label)}</label>
          <input type="hidden" name="inbound_ids" id="{_esc(input_id)}" value="{_esc(hidden_value)}">
          <button type="button" class="button secondary inbound-picker-trigger"
                  data-target="{_esc(input_id)}" data-summary="{_esc(summary_id)}">
            ☰ Choose inbounds
          </button>
          <div class="inbound-summary" id="{_esc(summary_id)}">Selected: {_esc(selected_summary)}</div>
        </div>
        """

    rows = ""
    inbound_lookup = {ib.id: ib for ib in panel_inbounds}
    for p in plans:
        selected = set(p.get_inbound_ids())
        selected_summary = ", ".join(
            f"#{iid} {inbound_lookup[iid].remark}" if iid in inbound_lookup else f"#{iid}"
            for iid in selected
        ) or "Global enabled inbounds"
        rows += f"""<tr>
          <form method="post" action="/admin/plans/update">
          <input type="hidden" name="id" value="{p.id}">
          <td>{p.id}</td>
          <td><input name="name" value="{_esc(p.name)}"></td>
          <td><input name="traffic_gb" value="{p.traffic_gb}" type="number"></td>
          <td><input name="duration_days" value="{p.duration_days}" type="number"></td>
          <td><input name="price_usdt" value="{_money(p.price_usdt)}" step="0.01" type="number"></td>
          <td><input name="price_toman" value="{getattr(p, 'price_toman', 0) or 0}" type="number"></td>
          <td><input name="limit_ip" value="{p.limit_ip}" type="number"></td>
          <td>
            {inbound_picker(selected, input_id=f"plan-inbounds-{p.id}", summary_id=f"plan-inbounds-summary-{p.id}")}
          </td>
          <td><select name="is_active"><option value="1" {"selected" if p.is_active else ""}>active</option><option value="0" {"" if p.is_active else "selected"}>inactive</option></select></td>
          <td><input name="sort_order" value="{p.sort_order}" type="number"></td>
          <td class="actions"><button>Save</button></form><form class="inline" method="post" action="/admin/plans/delete"><input type="hidden" name="id" value="{p.id}"><button class="danger">Delete</button></form></td>
        </tr>"""
    body = f"""
    {_flash(request)}
    <section class="panel"><h2>Add plan</h2>
      <form method="post" action="/admin/plans/create" class="two">
        <div><label>Name</label><input name="name" required></div>
        <div><label>Price dollars</label><input name="price_usdt" type="number" step="0.01" value="5"></div>
        <div><label>Price toman (0 = auto)</label><input name="price_toman" type="number" value="0"></div>
        <div><label>Traffic GB (0 unlimited)</label><input name="traffic_gb" type="number" value="10"></div>
        <div><label>Duration days</label><input name="duration_days" type="number" value="30"></div>
        <div><label>Limit IP (0 unlimited)</label><input name="limit_ip" type="number" value="0"></div>
        <div>{inbound_picker(set(), input_id="plan-inbounds-new", summary_id="plan-inbounds-summary-new")}</div>
        <div><label>Sort order</label><input name="sort_order" type="number" value="0"></div>
        <div style="align-self:end"><button>Create plan</button></div>
      </form>
    </section>
    {"<div class='panel'><p class='muted'>Panel inbounds could not be loaded: " + _esc(panel_error) + "</p></div>" if panel_error else ""}
    <h2>Plans</h2>
    <table><tr><th>ID</th><th>Name</th><th>GB</th><th>Days</th><th>Dollars</th><th>Toman</th><th>IP</th><th>Inbounds</th><th>Status</th><th>Sort</th><th>Actions</th></tr>{rows}</table>
    <dialog id="inbound-picker-dialog" class="inbound-dialog">
      <div class="inbound-dialog-inner">
        <div class="inbound-dialog-head">
          <div>
            <h3 id="inbound-picker-title">Allowed inbounds</h3>
            <p>Pick one or more inbounds for this plan. Use sync if the panel changed and you need a fresh list.</p>
          </div>
          <button type="button" class="button secondary" id="inbound-picker-close">Close</button>
        </div>
        <div class="inbound-dialog-list" id="inbound-picker-list"></div>
        <div class="inbound-dialog-actions">
          <button type="button" class="button secondary" id="inbound-picker-sync">Sync from panel</button>
          <button type="button" id="inbound-picker-apply">Apply selection</button>
        </div>
      </div>
    </dialog>
    <script>
      (() => {{
        const inboundDataUrl = '/admin/inbounds/list-json';
        let inboundItems = {inbound_payload};
        const dialog = document.getElementById('inbound-picker-dialog');
        const list = document.getElementById('inbound-picker-list');
        const title = document.getElementById('inbound-picker-title');
        const closeBtn = document.getElementById('inbound-picker-close');
        const syncBtn = document.getElementById('inbound-picker-sync');
        const applyBtn = document.getElementById('inbound-picker-apply');
        let activeInput = null;
        let activeSummary = null;
        let activeLabel = 'Allowed inbounds';

        function parseSelected() {{
          if (!activeInput) return new Set();
          return new Set((activeInput.value || '')
            .split(',')
            .map(v => parseInt(v.trim(), 10))
            .filter(Number.isFinite));
        }}

        function renderList() {{
          const selected = parseSelected();
          if (!inboundItems.length) {{
            list.innerHTML = '<div class="note">No panel inbounds were loaded.</div>';
            return;
          }}
          list.innerHTML = inboundItems.map(item => {{
            const checked = selected.has(item.id) ? 'checked' : '';
            const status = item.enable ? '<span class="badge ok">enabled</span>' : '<span class="badge bad">disabled</span>';
            return `
              <label class="inbound-item">
                <input type="checkbox" value="${{item.id}}" ${{checked}}>
                <div>
                  <strong>#${{item.id}} — ${{item.remark || 'Inbound'}}</strong>
                  <small>${{item.protocol || 'unknown'}} • port ${{item.port || '-'}} • used ${{item.used_gb || '0 GB'}} • ${{status}}</small>
                </div>
              </label>
            `;
          }}).join('');
        }}

        function selectedIdsFromList() {{
          return [...list.querySelectorAll('input[type="checkbox"]:checked')].map(cb => parseInt(cb.value, 10)).filter(Number.isFinite);
        }}

        function updateSourceField() {{
          if (!activeInput) return;
          const ids = selectedIdsFromList();
          activeInput.value = ids.join(',');
          if (activeSummary) {{
            activeSummary.textContent = ids.length
              ? `Selected: ${{ids.length}} inbound${{ids.length === 1 ? '' : 's'}}`
              : 'Selected: Global enabled inbounds';
          }}
        }}

        document.querySelectorAll('.inbound-picker-trigger').forEach(btn => {{
          btn.addEventListener('click', () => {{
            activeInput = document.getElementById(btn.dataset.target);
            activeSummary = document.getElementById(btn.dataset.summary);
            activeLabel = btn.dataset.label || 'Allowed inbounds';
            title.textContent = activeLabel;
            renderList();
            dialog.showModal();
          }});
        }});

        list.addEventListener('change', updateSourceField);

        closeBtn.addEventListener('click', () => dialog.close());
        applyBtn.addEventListener('click', () => {{
          updateSourceField();
          dialog.close();
        }});

        syncBtn.addEventListener('click', async () => {{
          syncBtn.disabled = true;
          const oldText = syncBtn.textContent;
          syncBtn.textContent = 'Syncing...';
          try {{
            const res = await fetch(inboundDataUrl, {{ cache: 'no-store' }});
            const data = await res.json();
            if (!res.ok || !data.ok) throw new Error(data.error || 'Failed to sync inbounds');
            inboundItems = data.items || [];
            renderList();
          }} catch (err) {{
            alert(err.message || String(err));
          }} finally {{
            syncBtn.textContent = oldText;
            syncBtn.disabled = false;
          }}
        }});

        dialog.addEventListener('click', (event) => {{
          const rect = dialog.getBoundingClientRect();
          const clickedOutside = (
            event.clientX < rect.left ||
            event.clientX > rect.right ||
            event.clientY < rect.top ||
            event.clientY > rect.bottom
          );
          if (clickedOutside) dialog.close();
        }});
      }})();
    </script>
    """
    return _layout("Plans", body, request)


async def plans_create(request: web.Request) -> web.Response:
    _require_auth(request)
    form = await request.post()
    inbound_ids = [str(v).strip() for v in form.getall("inbound_ids") if str(v).strip().isdigit()]
    async with AsyncSessionLocal() as session:
        await create_plan(
            session,
            name=str(form.get("name", "Plan")).strip(),
            traffic_gb=int(form.get("traffic_gb", 0) or 0),
            duration_days=int(form.get("duration_days", 30) or 30),
            price_usdt=float(form.get("price_usdt", 0) or 0),
            price_toman=int(form.get("price_toman", 0) or 0),
            limit_ip=int(form.get("limit_ip", 0) or 0),
            inbound_ids=",".join(inbound_ids) if inbound_ids else str(form.get("inbound_ids", "")).strip(),
            sort_order=int(form.get("sort_order", 0) or 0),
        )
    _redirect("/admin/plans", msg="Plan created")


async def plans_update(request: web.Request) -> web.Response:
    _require_auth(request)
    form = await request.post()
    plan_id = int(form.get("id", 0) or 0)
    inbound_ids = [str(v).strip() for v in form.getall("inbound_ids") if str(v).strip().isdigit()]
    async with AsyncSessionLocal() as session:
        await update_plan(
            session,
            plan_id,
            name=str(form.get("name", "")).strip(),
            traffic_gb=int(form.get("traffic_gb", 0) or 0),
            duration_days=int(form.get("duration_days", 30) or 30),
            price_usdt=float(form.get("price_usdt", 0) or 0),
            price_toman=int(form.get("price_toman", 0) or 0),
            limit_ip=int(form.get("limit_ip", 0) or 0),
            inbound_ids=",".join(inbound_ids) if inbound_ids else str(form.get("inbound_ids", "")).strip(),
            is_active=str(form.get("is_active", "0")) == "1",
            sort_order=int(form.get("sort_order", 0) or 0),
        )
    _redirect("/admin/plans", msg="Plan updated")


async def plans_delete(request: web.Request) -> web.Response:
    _require_auth(request)
    form = await request.post()
    async with AsyncSessionLocal() as session:
        await delete_plan(session, int(form.get("id", 0) or 0))
    _redirect("/admin/plans", msg="Plan deleted")


async def test_plan_page(request: web.Request) -> web.Response:
    _require_auth(request)
    async with AsyncSessionLocal() as session:
        enabled = await get_setting(
            session,
            "test_sub_enabled",
            str(settings.test_subscription_enabled).lower(),
        )
        traffic = await get_setting(
            session,
            "test_sub_traffic_gb",
            str(settings.test_traffic_gb),
        )
        days = await get_setting(
            session,
            "test_sub_duration_days",
            str(settings.test_duration_days),
        )
        used_count = (
            await session.execute(select(func.count()).select_from(TestSubscriptionRecord))
        ).scalar() or 0

    is_enabled = enabled.lower() == "true"
    body = f"""
    {_flash(request)}
    <div class="grid">
      <div class="stat"><span class="muted">Status</span><b>{"Enabled" if is_enabled else "Disabled"}</b></div>
      <div class="stat"><span class="muted">Traffic</span><b>{_esc(traffic)} GB</b></div>
      <div class="stat"><span class="muted">Duration</span><b>{_esc(days)} days</b></div>
      <div class="stat"><span class="muted">Already used</span><b>{used_count}</b></div>
    </div>
    <section class="panel" style="margin-top:16px">
      <h2>Free test subscription</h2>
      <p class="muted">Users can claim this once. Disable it if you do not want the bot to issue free test configs.</p>
      <form method="post" action="/admin/test-plan" class="two">
        <div>
          <label>Test subscription</label>
          <select name="test_sub_enabled">
            <option value="true" {"selected" if is_enabled else ""}>Enabled</option>
            <option value="false" {"" if is_enabled else "selected"}>Disabled</option>
          </select>
        </div>
        <div>
          <label>Traffic GB</label>
          <input name="test_sub_traffic_gb" type="number" min="0" value="{_esc(traffic)}">
        </div>
        <div>
          <label>Duration days</label>
          <input name="test_sub_duration_days" type="number" min="1" value="{_esc(days)}">
        </div>
        <div style="align-self:end"><button>Save test plan</button></div>
      </form>
    </section>
    """
    return _layout("Test Plan", body, request)


async def test_plan_save(request: web.Request) -> web.Response:
    _require_auth(request)
    form = await request.post()
    enabled = str(form.get("test_sub_enabled", "false")).lower()
    traffic = str(form.get("test_sub_traffic_gb", "1")).strip()
    days = str(form.get("test_sub_duration_days", "1")).strip()

    if enabled not in {"true", "false"}:
        _redirect("/admin/test-plan", err="Invalid enabled value")
    if not traffic.isdigit() or int(traffic) < 0:
        _redirect("/admin/test-plan", err="Traffic must be 0 or more")
    if not days.isdigit() or int(days) < 1:
        _redirect("/admin/test-plan", err="Duration must be at least 1 day")

    async with AsyncSessionLocal() as session:
        await set_setting(session, "test_sub_enabled", enabled)
        await set_setting(session, "test_sub_traffic_gb", traffic)
        await set_setting(session, "test_sub_duration_days", days)
    _redirect("/admin/test-plan", msg="Test plan settings saved")


async def payments(request: web.Request) -> web.Response:
    _require_auth(request)
    status = request.query.get("status") or None
    search = request.query.get("q") or None
    async with AsyncSessionLocal() as session:
        rows = await get_payments_filtered(session, status_filter=status, limit=100, order_id_search=search)
    tr = "".join(
        f"<tr><td>{p.id}</td><td>{_esc(p.order_id)}</td><td>{p.user_id}</td><td>{_money(p.amount_usdt)}</td><td>{_esc(p.payment_method)}</td><td>{_esc(p.status)}</td><td>{_esc(p.created_at)}</td></tr>"
        for p in rows
    )
    options = ["", "pending", "pending_card", "pending_crypto", "confirmed", "failed"]
    selects = "".join(f'<option value="{o}" {"selected" if o == (status or "") else ""}>{o or "all"}</option>' for o in options)
    body = f"""
    <div class="panel"><form method="get" class="actions"><input name="q" value="{_esc(search or '')}" placeholder="Order id search" style="max-width:360px"><select name="status" style="max-width:220px">{selects}</select><button>Filter</button><a class="button secondary" href="/admin/payments">Reset</a></form></div>
    <table><tr><th>ID</th><th>Order</th><th>User ID</th><th>Dollars</th><th>Method</th><th>Status</th><th>Created</th></tr>{tr}</table>
    """
    return _layout("Payments", body, request)


def _status_badge(status: str) -> str:
    cls = "ok" if status in {"confirmed", "finished"} else "bad" if status in {"failed", "expired", "partially_paid"} else "wait"
    return f'<span class="badge {cls}">{_esc(status)}</span>'


async def receipts_get(request: web.Request) -> web.Response:
    _require_auth(request)
    status = request.query.get("status", "pending")
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Payment, User)
            .join(User, User.id == Payment.user_id)
            .where(Payment.payment_method == "card")
            .order_by(Payment.created_at.desc())
            .limit(100)
        )
        if status == "pending":
            stmt = stmt.where(Payment.status == "awaiting_review")
        elif status == "confirmed":
            stmt = stmt.where(Payment.status.in_(["confirmed", "finished"]))
        elif status == "failed":
            stmt = stmt.where(Payment.status == "failed")
        rows = (await session.execute(stmt)).all()

    tr = ""
    for payment, user in rows:
        if payment.receipt_type == "photo" and payment.receipt_file_id:
            receipt = f'<a href="/admin/receipts/{payment.id}/photo" target="_blank"><img class="receipt-img" src="/admin/receipts/{payment.id}/photo" alt="receipt"></a>'
        elif payment.receipt_file_id:
            receipt = f'<code>{_esc(payment.receipt_file_id)}</code>'
        else:
            receipt = '<span class="muted">No receipt attached</span>'

        actions = ""
        if payment.status == "awaiting_review":
            actions = f"""
            <div class="actions">
              <form method="post" action="/admin/receipts/{payment.id}/approve"><button>Approve</button></form>
              <form method="post" action="/admin/receipts/{payment.id}/reject"><button class="danger">Reject</button></form>
            </div>
            """
        tr += f"""<tr>
          <td>{payment.id}<br><span class="muted">{_esc(payment.created_at)}</span></td>
          <td><a href="/admin/customers/{user.id}">{user.telegram_id}</a><br>@{_esc(user.username or "-")}</td>
          <td><code>{_esc(payment.order_id)}</code><br>{_money(payment.amount_usdt)} dollars<br>{_esc(payment.amount_rial or "-")} rial</td>
          <td>{receipt}</td>
          <td>{_status_badge(payment.status)}</td>
          <td>{actions}</td>
        </tr>"""

    status_options = "".join(
        f'<option value="{value}" {"selected" if status == value else ""}>{label}</option>'
        for value, label in [
            ("pending", "Pending review"),
            ("confirmed", "Confirmed"),
            ("failed", "Failed"),
            ("all", "All card receipts"),
        ]
    )
    body = f"""
    {_flash(request)}
    <div class="toolbar">
      <form method="get" class="actions">
        <select name="status" style="max-width:220px">{status_options}</select>
        <button>Filter</button>
      </form>
      <div class="note">Approving a receipt creates the customer's VPN subscription using the plan stored on that payment.</div>
    </div>
    <table><tr><th>Payment</th><th>Customer</th><th>Order</th><th>Receipt</th><th>Status</th><th>Actions</th></tr>{tr}</table>
    """
    return _layout("Receipts", body, request)


async def receipt_photo(request: web.Request) -> web.Response:
    _require_auth(request)
    payment_id = int(request.match_info["payment_id"])
    async with AsyncSessionLocal() as session:
        payment = await session.get(Payment, payment_id)
    if not payment or payment.receipt_type != "photo" or not payment.receipt_file_id:
        raise web.HTTPNotFound(text="Receipt photo not found")

    api = f"https://api.telegram.org/bot{settings.bot_token}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        meta = await client.get(f"{api}/getFile", params={"file_id": payment.receipt_file_id})
        meta.raise_for_status()
        payload = meta.json()
        if not payload.get("ok"):
            raise web.HTTPBadGateway(text="Telegram did not return file metadata")
        file_path = payload["result"]["file_path"]
        file_resp = await client.get(f"https://api.telegram.org/file/bot{settings.bot_token}/{file_path}")
        file_resp.raise_for_status()
        content_type = file_resp.headers.get("content-type", "image/jpeg")
        return web.Response(body=file_resp.content, content_type=content_type)


async def _get_payment_user(session, payment_id: int) -> tuple[Payment | None, User | None]:
    row = (
        await session.execute(
            select(Payment, User)
            .join(User, User.id == Payment.user_id)
            .where(Payment.id == payment_id)
        )
    ).first()
    if not row:
        return None, None
    return row[0], row[1]


async def receipt_approve(request: web.Request) -> web.Response:
    _require_auth(request)
    payment_id = int(request.match_info["payment_id"])
    try:
        async with AsyncSessionLocal() as session:
            payment, user = await _get_payment_user(session, payment_id)
            if not payment or not user:
                _redirect("/admin/receipts", err="Payment not found")
            if payment.status in ("confirmed", "finished"):
                _redirect("/admin/receipts", msg="Receipt was already approved")
            if payment.payment_method != "card":
                _redirect("/admin/receipts", err="This payment is not a card receipt")

            result = await create_new_subscription(
                session=session,
                user_id=user.id,
                telegram_id=user.telegram_id,
                inbound_id=0,
                plan_id=getattr(payment, "inbound_id", 0) or 0,
            )
            await update_payment_status(session, payment.id, "confirmed", result.subscription.id)

        await _notify_customer_receipt_result(
            user.telegram_id,
            approved=True,
            order_id=payment.order_id,
            sub_link=result.sub_link,
        )
        _redirect("/admin/receipts", msg="Receipt approved and subscription created")
    except Exception as exc:
        _redirect("/admin/receipts", err=f"Could not approve receipt: {exc}")


async def receipt_reject(request: web.Request) -> web.Response:
    _require_auth(request)
    payment_id = int(request.match_info["payment_id"])
    async with AsyncSessionLocal() as session:
        payment, user = await _get_payment_user(session, payment_id)
        if not payment or not user:
            _redirect("/admin/receipts", err="Payment not found")
        if payment.status in ("confirmed", "finished"):
            _redirect("/admin/receipts", err="Confirmed payments cannot be rejected")
        await update_payment_status(session, payment.id, "failed")
    await _notify_customer_receipt_result(
        user.telegram_id,
        approved=False,
        order_id=payment.order_id,
        sub_link="",
    )
    _redirect("/admin/receipts", msg="Receipt rejected")


async def _notify_customer_receipt_result(
    telegram_id: int,
    approved: bool,
    order_id: str,
    sub_link: str,
) -> None:
    bot = ActivityLoggingBot(token=settings.bot_token)
    try:
        if approved:
            text = (
                f"🎉 <b>پرداخت کارت به کارت شما تأیید شد!</b>\n"
                f"🔖 سفارش: <code>{_esc(order_id)}</code>\n\n"
                f"🔗 <b>لینک اشتراک:</b>\n<code>{_esc(sub_link)}</code>"
            )
        else:
            text = (
                f"❌ <b>پرداخت کارت به کارت شما رد شد.</b>\n"
                f"🔖 سفارش: <code>{_esc(order_id)}</code>\n\n"
                "در صورت وجود مشکل با پشتیبانی تماس بگیرید."
            )
        await bot.send_message(telegram_id, text, parse_mode="HTML")
    except Exception:
        pass
    finally:
        await bot.session.close()


async def discounts_get(request: web.Request) -> web.Response:
    _require_auth(request)
    async with AsyncSessionLocal() as session:
        codes = await get_all_discount_codes(session)
    rows = "".join(
        f"""<tr><td>{c.id}</td><td>{_esc(c.code)}</td><td>{c.percent}%</td><td>{c.used_count}/{_esc(c.max_uses or "unlimited")}</td><td>{_esc(c.is_active)}</td><td>{_esc(c.expires_at or "-")}</td>
        <td><form method="post" action="/admin/discounts/delete"><input type="hidden" name="id" value="{c.id}"><button class="danger">Delete</button></form></td></tr>"""
        for c in codes
    )
    body = f"""
    {_flash(request)}
    <section class="panel"><h2>Add discount</h2>
      <form method="post" action="/admin/discounts/create" class="two">
        <div><label>Code</label><input name="code" required></div>
        <div><label>Percent</label><input name="percent" type="number" value="10"></div>
        <div><label>Max uses (blank unlimited)</label><input name="max_uses" type="number"></div>
        <div style="align-self:end"><button>Create discount</button></div>
      </form>
    </section>
    <table><tr><th>ID</th><th>Code</th><th>Percent</th><th>Used</th><th>Active</th><th>Expires</th><th>Actions</th></tr>{rows}</table>
    """
    return _layout("Discounts", body, request)


async def discounts_create(request: web.Request) -> web.Response:
    _require_auth(request)
    form = await request.post()
    max_uses_raw = str(form.get("max_uses", "")).strip()
    async with AsyncSessionLocal() as session:
        await create_discount_code(
            session,
            code=str(form.get("code", "")).strip(),
            percent=int(form.get("percent", 0) or 0),
            max_uses=int(max_uses_raw) if max_uses_raw else None,
        )
    _redirect("/admin/discounts", msg="Discount created")


async def discounts_delete(request: web.Request) -> web.Response:
    _require_auth(request)
    form = await request.post()
    async with AsyncSessionLocal() as session:
        await delete_discount_code(session, int(form.get("id", 0) or 0))
    _redirect("/admin/discounts", msg="Discount deleted")


async def content_page(request: web.Request) -> web.Response:
    _require_auth(request)
    async with AsyncSessionLocal() as session:
        vals = {
            "banner_file_id": await get_setting(session, "banner_file_id", ""),
            "welcome_banner_file_id": await get_setting(session, "welcome_banner_file_id", ""),
            "welcome_banner_caption": await get_setting(session, "welcome_banner_caption", ""),
            "join_channel_id": await get_setting(session, "join_channel_id", ""),
            "join_channel_link": await get_setting(session, "join_channel_link", ""),
            "join_channel_title": await get_setting(session, "join_channel_title", "کانال ما"),
        }
    body = f"""
    {_flash(request)}
    <div class="two">
      <section class="panel"><h2>Bot banner</h2>
        <form method="post" action="/admin/content" enctype="multipart/form-data">
          <label>Upload general banner photo</label><input name="banner_upload" type="file" accept="image/*">
          <p class="muted">The panel uploads this image to Telegram and stores the returned file_id automatically.</p>
          <label>General banner Telegram file_id</label><input name="banner_file_id" value="{_esc(vals["banner_file_id"])}">
          <p class="muted">Current file_id is kept unless you upload a new photo or change this value.</p>
          <h2>Welcome banner</h2>
          <label>Upload welcome banner photo</label><input name="welcome_banner_upload" type="file" accept="image/*">
          <label>Welcome banner file_id</label><input name="welcome_banner_file_id" value="{_esc(vals["welcome_banner_file_id"])}">
          <label>Welcome caption</label><textarea name="welcome_banner_caption">{_esc(vals["welcome_banner_caption"])}</textarea>
          <p><button>Save banners</button></p>
        </form>
      </section>
      <section class="panel"><h2>Forced join channel</h2>
        <form method="post" action="/admin/content">
          <label>Channel ID (@channel or -100...)</label><input name="join_channel_id" value="{_esc(vals["join_channel_id"])}">
          <label>Invite/link URL</label><input name="join_channel_link" value="{_esc(vals["join_channel_link"])}">
          <label>Display title</label><input name="join_channel_title" value="{_esc(vals["join_channel_title"])}">
          <p><button>Save channel</button></p>
        </form>
      </section>
    </div>
    """
    return _layout("Content", body, request)


async def _telegram_upload_photo_file_id(file_field: Any, label: str) -> str | None:
    filename = str(getattr(file_field, "filename", "") or "").strip()
    file_obj = getattr(file_field, "file", None)
    if not filename or file_obj is None:
        return None
    content_type = str(getattr(file_field, "content_type", "") or "")
    if content_type and not content_type.startswith("image/"):
        raise ValueError(f"{label} must be an image file")

    data = file_obj.read()
    if not data:
        return None
    max_bytes = 10 * 1024 * 1024
    if len(data) > max_bytes:
        raise ValueError(f"{label} image is too large. Maximum size is 10 MB")
    if not settings.admin_ids:
        raise ValueError("ADMIN_IDS must contain at least one Telegram admin ID for banner uploads")

    from aiogram.types import BufferedInputFile

    safe_name = Path(filename).name or "banner.jpg"
    bot = ActivityLoggingBot(token=settings.bot_token)
    try:
        message = await bot.send_photo(
            chat_id=settings.admin_ids[0],
            photo=BufferedInputFile(data, filename=safe_name),
            caption=f"ONEBOT admin upload: {label}",
        )
        if not message.photo:
            raise ValueError(f"Telegram did not return a photo file_id for {label}")
        return message.photo[-1].file_id
    finally:
        await bot.session.close()


async def content_save(request: web.Request) -> web.Response:
    _require_auth(request)
    try:
        form = await request.post()
        uploaded_banner = await _telegram_upload_photo_file_id(
            form.get("banner_upload"),
            "general banner",
        )
        uploaded_welcome = await _telegram_upload_photo_file_id(
            form.get("welcome_banner_upload"),
            "welcome banner",
        )
        keys = [
            "banner_file_id",
            "welcome_banner_file_id",
            "welcome_banner_caption",
            "join_channel_id",
            "join_channel_link",
            "join_channel_title",
        ]
        async with AsyncSessionLocal() as session:
            for key in keys:
                if key in form:
                    await set_setting(session, key, str(form.get(key, "")))
            if uploaded_banner:
                await set_setting(session, "banner_file_id", uploaded_banner)
            if uploaded_welcome:
                await set_setting(session, "welcome_banner_file_id", uploaded_welcome)
        _redirect("/admin/content", msg="Content settings saved")
    except Exception as exc:
        _redirect("/admin/content", err=f"Could not save content settings: {exc}")


async def broadcast_page(request: web.Request) -> web.Response:
    _require_auth(request)
    body = f"""
    {_flash(request)}
    <section class="panel">
      <h2>Broadcast to all customers</h2>
      <form method="post" action="/admin/broadcast" enctype="multipart/form-data">
        <label>Broadcast image</label>
        <input name="photo" type="file" accept="image/*">
        <p class="muted">Optional. If you upload an image, the message text is sent as the caption below it.</p>
        <label>Message text or image caption</label>
        <textarea name="text" required></textarea>
        <label>Confirmation</label>
        <input name="confirm" placeholder="Type SEND">
        <p><button class="danger">Send broadcast</button></p>
      </form>
    </section>
    """
    return _layout("Broadcast", body, request)


async def broadcast_send(request: web.Request) -> web.Response:
    _require_auth(request)
    form = await request.post()
    text = str(form.get("text", "")).strip()
    confirm = str(form.get("confirm", "")).strip()
    if confirm != "SEND" or not text:
        _redirect("/admin/broadcast", err="Type SEND and provide message text")
    from aiogram.types import BufferedInputFile

    photo_field = form.get("photo")
    photo_bytes: bytes | None = None
    photo_filename = "broadcast.jpg"
    if photo_field is not None and getattr(photo_field, "filename", ""):
        content_type = str(getattr(photo_field, "content_type", "") or "")
        if content_type and not content_type.startswith("image/"):
            _redirect("/admin/broadcast", err="Broadcast upload must be an image file")
        file_obj = getattr(photo_field, "file", None)
        if file_obj is not None:
            photo_bytes = file_obj.read()
            if photo_bytes and len(photo_bytes) > 10 * 1024 * 1024:
                _redirect("/admin/broadcast", err="Broadcast image is too large. Maximum size is 10 MB")
            photo_filename = Path(str(photo_field.filename)).name or photo_filename

    bot = ActivityLoggingBot(token=settings.bot_token)
    sent = 0
    failed = 0
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(User.telegram_id))).scalars().all()
    try:
        for telegram_id in users:
            try:
                if photo_bytes:
                    await bot.send_photo(
                        telegram_id,
                        photo=BufferedInputFile(photo_bytes, filename=photo_filename),
                        caption=text,
                    )
                else:
                    await bot.send_message(telegram_id, text)
                sent += 1
            except Exception:
                failed += 1
    finally:
        await bot.session.close()
    _redirect("/admin/broadcast", msg=f"Broadcast complete: {sent} sent, {failed} failed")


async def security_page(request: web.Request) -> web.Response:
    _require_auth(request)
    async with AsyncSessionLocal() as session:
        admin_cmd = await get_setting(session, "admin_login_command", "admin_secret")
        blocked = await get_blocked_user_ids(session)
    body = f"""
    {_flash(request)}
    <div class="two">
      <section class="panel"><h2>Bot admin security</h2>
        <form method="post" action="/admin/security">
          <label>Telegram admin login command</label><input name="admin_login_command" value="{_esc(admin_cmd)}">
          <p><button>Save bot access</button></p>
        </form>
      </section>
      <section class="panel"><h2>Web panel access</h2>
        <p class="muted">Change the login used for the web admin panel. Leave password blank to keep the current one.</p>
        <form method="post" action="/admin/security">
          <label>Panel username</label><input name="web_admin_username" value="{_esc(settings.web_admin_username or "admin")}">
          <label>New panel password</label><input name="web_admin_password" type="password" placeholder="Leave blank to keep current password">
          <p><button>Save panel login</button></p>
        </form>
      </section>
      <section class="panel"><h2>Blocked customers</h2>
        <p class="muted">{len(blocked)} blocked Telegram IDs.</p>
        <textarea readonly>{_esc(",".join(str(x) for x in sorted(blocked)))}</textarea>
      </section>
    </div>
    """
    return _layout("Security", body, request)


async def security_save(request: web.Request) -> web.Response:
    _require_auth(request)
    form = await request.post()
    cmd = str(form.get("admin_login_command", "")).strip().lstrip("/")
    web_username = str(form.get("web_admin_username", "")).strip()
    web_password = str(form.get("web_admin_password", "")).strip()
    if not cmd and not web_username and not web_password:
        _redirect("/admin/security", err="Nothing to update")
    async with AsyncSessionLocal() as session:
        if cmd:
            await set_setting(session, "admin_login_command", cmd)
        if web_username:
            settings.web_admin_username = web_username
            _write_env_values({"WEB_ADMIN_USERNAME": web_username})
        if web_password:
            settings.web_admin_password = web_password
            _write_env_values({"WEB_ADMIN_PASSWORD": web_password})
    _redirect("/admin/security", msg="Security settings saved")


def _read_env_values() -> dict[str, str]:
    values = dict(os.environ)
    path = _env_path()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _write_env_values(updates: dict[str, str]) -> None:
    path = _env_path()
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in existing:
        if "=" not in line or line.lstrip().startswith("#"):
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key in ENV_KEYS:
        if key in updates and key not in seen:
            out.append(f"{key}={updates[key]}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


async def settings_get(request: web.Request) -> web.Response:
    _require_auth(request)
    env_values = _read_env_values()
    if not env_values.get("NOWPAYMENTS_IPN_URL", "").strip():
        env_values["NOWPAYMENTS_IPN_URL"] = settings.nowpayments_ipn_callback_url()
    if not env_values.get("MAXELPAY_WEBHOOK_URL", "").strip():
        env_values["MAXELPAY_WEBHOOK_URL"] = settings.maxelpay_webhook_callback_url()
    async with AsyncSessionLocal() as session:
        runtime = {
            "payment_crypto_enabled": await get_setting(session, "payment_crypto_enabled", "1"),
            "payment_card_enabled": await get_setting(session, "payment_card_enabled", "0"),
            "payment_crypto_invoice": await get_setting(session, "payment_crypto_invoice", "0"),
            "crypto_gateway": await get_setting(session, "crypto_gateway", "nowpayments"),
            "card_number": await get_setting(session, "card_number", ""),
            "card_holder": await get_setting(session, "card_holder", ""),
            "usdt_to_toman_rate": await get_setting(session, "usdt_to_toman_rate", "0"),
            "notification_expiry_enabled": await get_setting(session, "notification_expiry_enabled", "1"),
            "notification_traffic_enabled": await get_setting(session, "notification_traffic_enabled", "1"),
        }

    def env_input(key: str, label: str, helper: str = "", secret: bool = False, placeholder: str = "") -> str:
        value = _esc(env_values.get(key, ""))
        input_type = "password" if secret else "text"
        helper_html = f'<div class="field-help">{_esc(helper)}</div>' if helper else ""
        placeholder_attr = f' placeholder="{_esc(placeholder)}"' if placeholder else ""
        return f"""
        <label>{_esc(label)}</label>
        <input name="{_esc(key)}" value="{value}" type="{input_type}"{placeholder_attr}>
        {helper_html}
        """

    payments_fields = f"""
      <div class="field-grid">
        <div>
          <label>Crypto payments</label>
          <select name="payment_crypto_enabled"><option value="1" {"selected" if runtime["payment_crypto_enabled"] == "1" else ""}>enabled</option><option value="0" {"" if runtime["payment_crypto_enabled"] == "1" else "selected"}>disabled</option></select>
        </div>
        <div>
          <label>Card payments</label>
          <select name="payment_card_enabled"><option value="1" {"selected" if runtime["payment_card_enabled"] == "1" else ""}>enabled</option><option value="0" {"" if runtime["payment_card_enabled"] == "1" else "selected"}>disabled</option></select>
        </div>
        <div>
          <label>NOWPayments invoice page</label>
          <select name="payment_crypto_invoice"><option value="1" {"selected" if runtime["payment_crypto_invoice"] == "1" else ""}>enabled</option><option value="0" {"" if runtime["payment_crypto_invoice"] == "1" else "selected"}>disabled</option></select>
        </div>
      </div>
    """
    routing_fields = f"""
      <div class="field-grid">
        <div>
          <label>Crypto gateway</label>
          <select name="crypto_gateway"><option value="nowpayments" {"selected" if runtime["crypto_gateway"] == "nowpayments" else ""}>NOWPayments</option><option value="maxelpay" {"selected" if runtime["crypto_gateway"] == "maxelpay" else ""}>MaxelPay</option></select>
        </div>
        <div>
          <label>Dollar to toman rate</label>
          <input name="usdt_to_toman_rate" value="{_esc(runtime["usdt_to_toman_rate"])}" type="number">
        </div>
        <div>
          <label>Card number</label>
          <input name="card_number" value="{_esc(runtime["card_number"])}" inputmode="numeric">
        </div>
        <div class="field-wide">
          <label>Card holder</label>
          <input name="card_holder" value="{_esc(runtime["card_holder"])}">
        </div>
      </div>
    """
    notification_fields = f"""
      <div class="field-grid">
        <div>
          <label>Expiry warnings</label>
          <select name="notification_expiry_enabled">
            <option value="1" {"selected" if runtime["notification_expiry_enabled"] == "1" else ""}>enabled</option>
            <option value="0" {"" if runtime["notification_expiry_enabled"] == "1" else "selected"}>disabled</option>
          </select>
        </div>
        <div>
          <label>Traffic warnings</label>
          <select name="notification_traffic_enabled">
            <option value="1" {"selected" if runtime["notification_traffic_enabled"] == "1" else ""}>enabled</option>
            <option value="0" {"" if runtime["notification_traffic_enabled"] == "1" else "selected"}>disabled</option>
          </select>
        </div>
      </div>
    """
    env_cards = f"""
    <section class="settings-grid">
      <section class="panel settings-card">
        <div class="settings-head">
          <div>
            <div class="muted">Immediate effect</div>
            <h2>Payment switches</h2>
          </div>
          <span class="badge ok">runtime</span>
        </div>
        <form method="post" action="/admin/settings/runtime">
          {payments_fields}
          <p><button>Save switches</button></p>
        </form>
      </section>
      <section class="panel settings-card">
        <div class="settings-head">
          <div>
            <div class="muted">Immediate effect</div>
            <h2>Payment routing and payout</h2>
          </div>
          <span class="badge wait">runtime</span>
        </div>
        <form method="post" action="/admin/settings/runtime">
          {routing_fields}
          <p><button>Save routing</button></p>
        </form>
      </section>
      <section class="panel settings-card">
        <div class="settings-head">
          <div>
            <div class="muted">Notification messages</div>
            <h2>Subscription warnings</h2>
          </div>
          <span class="badge">runtime</span>
        </div>
        <form method="post" action="/admin/settings/runtime">
          {notification_fields}
          <p><button>Save notification messages</button></p>
        </form>
      </section>
    </section>
    """

    env_groups = f"""
      <section class="panel settings-card">
        <div class="settings-head">
          <div>
            <div class="muted">Connection and auth</div>
            <h2>Telegram and access</h2>
          </div>
          <span class="badge">.env</span>
        </div>
        <div class="field-grid">
          <div>{env_input("BOT_TOKEN", "BOT_TOKEN", "Bot token from BotFather.", secret=True)}</div>
          <div>{env_input("BOT_USERNAME", "BOT_USERNAME", "Bot username without @.")}</div>
          <div>{env_input("ADMIN_IDS", "ADMIN_IDS", "Comma-separated Telegram admin IDs.")}</div>
          <div>{env_input("ADMIN_SECRET", "ADMIN_SECRET", "Secret command password.", secret=True)}</div>
          <div>{env_input("WEB_ADMIN_ENABLED", "WEB_ADMIN_ENABLED", "Enable or disable the web panel.")}</div>
          <div>{env_input("WEB_ADMIN_USERNAME", "WEB_ADMIN_USERNAME", "Web panel login username.")}</div>
          <div>{env_input("WEB_ADMIN_PASSWORD", "WEB_ADMIN_PASSWORD", "Web panel login password.", secret=True)}</div>
          <div>{env_input("WEB_ADMIN_COOKIE_SECRET", "WEB_ADMIN_COOKIE_SECRET", "Cookie signing secret.", secret=True)}</div>
        </div>
      </section>
      <section class="panel settings-card">
        <div class="settings-head">
          <div>
            <div class="muted">Panel connection</div>
            <h2>3X-UI and database</h2>
          </div>
          <span class="badge">.env</span>
        </div>
        <div class="field-grid">
          <div>{env_input("PANEL_URL", "PANEL_URL", "Full 3X-UI panel URL.")}</div>
          <div>{env_input("PANEL_USERNAME", "PANEL_USERNAME", "Panel username.", secret=True)}</div>
          <div>{env_input("PANEL_PASSWORD", "PANEL_PASSWORD", "Panel password.", secret=True)}</div>
          <div>{env_input("PANEL_API_TOKEN", "PANEL_API_TOKEN", "Bearer token for panel API.", secret=True)}</div>
          <div>{env_input("SUB_PORT", "SUB_PORT", "Subscription base port.")}</div>
          <div>{env_input("DB_URL", "DB_URL", "Database URL.")}</div>
          <div>{env_input("WEBHOOK_PORT", "WEBHOOK_PORT", "Webhook server port.")}</div>
        </div>
      </section>
      <section class="panel settings-card">
        <div class="settings-head">
          <div>
            <div class="muted">Payments and webhooks</div>
            <h2>External services</h2>
          </div>
          <span class="badge bad">sensitive</span>
        </div>
        <div class="field-grid">
          <div>{env_input("NOWPAYMENTS_API_KEY", "NOWPAYMENTS_API_KEY", "NOWPayments API key.", secret=True)}</div>
          <div>{env_input("NOWPAYMENTS_IPN_SECRET", "NOWPAYMENTS_IPN_SECRET", "IPN signature secret.", secret=True)}</div>
          <div>{env_input("NOWPAYMENTS_IPN_URL", "NOWPAYMENTS_IPN_URL", "NOWPayments IPN callback URL.", placeholder=settings.nowpayments_ipn_callback_url())}</div>
          <div>{env_input("MAXELPAY_API_KEY", "MAXELPAY_API_KEY", "MaxelPay API key.", secret=True)}</div>
          <div>{env_input("MAXELPAY_WEBHOOK_SECRET", "MAXELPAY_WEBHOOK_SECRET", "MaxelPay webhook secret.", secret=True)}</div>
          <div>{env_input("MAXELPAY_WEBHOOK_URL", "MAXELPAY_WEBHOOK_URL", "MaxelPay webhook URL.", placeholder=settings.maxelpay_webhook_callback_url())}</div>
        </div>
      </section>
    """
    body = f"""
    {_flash(request)}
    <section class="panel settings-hero">
      <div class="settings-head">
        <div>
          <div class="muted">Operator controls</div>
          <h2>Settings</h2>
          <p class="muted">Grouped by what changes now and what needs a restart. Payment controls, panel access, and service secrets are split into separate boxes so the page scans quickly.</p>
        </div>
      </div>
    </section>
    {env_cards}
    <div class="settings-stack">
      {env_groups}
    </div>
    """
    return _layout("Settings", body, request)


async def settings_runtime_post(request: web.Request) -> web.Response:
    _require_auth(request)
    form = await request.post()
    keys = [
        "payment_crypto_enabled",
        "payment_card_enabled",
        "payment_crypto_invoice",
        "crypto_gateway",
        "card_number",
        "card_holder",
        "usdt_to_toman_rate",
        "notification_expiry_enabled",
        "notification_traffic_enabled",
    ]
    async with AsyncSessionLocal() as session:
        existing = {key: await get_setting(session, key, "") for key in keys}
        for key in keys:
            if key in form:
                await set_setting(session, key, str(form.get(key, "")))
            elif existing.get(key, "") != "":
                await set_setting(session, key, existing[key])
    _redirect("/admin/settings", msg="Runtime settings saved")


async def settings_env_post(request: web.Request) -> web.Response:
    _require_auth(request)
    form = await request.post()
    _write_env_values({key: str(form.get(key, "")) for key in ENV_KEYS})
    _redirect("/admin/settings", msg=".env saved. Restart the bot for env changes to take effect.")


def _sqlite_db_path() -> Path:
    db_url = settings.db_url
    path_str = db_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    path = Path(path_str)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    return path


def _log_path() -> Path:
    path = Path(settings.log_file)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    return path


async def backups_get(request: web.Request) -> web.Response:
    _require_auth(request)
    body = f"""
    {_flash(request)}
    <div class="grid">
      <section class="panel">
        <h2>Bot database</h2>
        <p class="muted">Downloads a zip copy of the local bot SQLite database.</p>
        <a class="button" href="/admin/backups/bot">Download bot backup</a>
      </section>
      <section class="panel">
        <h2>3X-UI panel database</h2>
        <p class="muted">Uses the configured panel API to download its database backup.</p>
        <a class="button secondary" href="/admin/backups/panel">Download panel backup</a>
      </section>
      <section class="panel">
        <h2>Logs</h2>
        <p class="muted">Downloads the bot log file and rotated log archives from the configured logs directory.</p>
        <div class="actions">
          <a class="button secondary" href="/admin/backups/logs">Download logs zip</a>
          <a class="button secondary" href="/admin/backups/log-current">Download current log</a>
        </div>
      </section>
    </div>
    <div class="two" style="margin-top:16px">
      <section class="panel">
        <h2>Restore bot database</h2>
        <p class="muted">Upload a bot SQLite backup as <code>.zip</code> or <code>.db</code>. The current DB is copied first, then the uploaded DB replaces it. Restart the bot after restore.</p>
        <form method="post" action="/admin/backups/restore-bot" enctype="multipart/form-data">
          <label>Bot database backup file</label>
          <input type="file" name="backup" accept=".zip,.db,.sqlite,.sqlite3" required>
          <label>Confirmation</label>
          <input name="confirm" placeholder="Type RESTORE">
          <p><button class="danger">Restore bot DB</button></p>
        </form>
      </section>
    </div>
    """
    return _layout("Backups", body, request)


async def backup_bot_download(request: web.Request) -> web.Response:
    _require_auth(request)
    db_path = _sqlite_db_path()
    if not db_path.exists():
        _redirect("/admin/backups", err=f"Database file not found: {db_path}")
    import sqlite3
    tmp_path = db_path.parent / f"_web_backup_{_now_ts()}.db"
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(tmp_path))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    data = tmp_path.read_bytes()
    tmp_path.unlink(missing_ok=True)
    buf = io.BytesIO()
    filename = f"onebot_bot_backup_{_now_ts()}.zip"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bot_data.db", data)
    return web.Response(
        body=buf.getvalue(),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        content_type="application/zip",
    )


async def backup_panel_download(request: web.Request) -> web.Response:
    _require_auth(request)
    try:
        async with XUIClient(
            panel_url=settings.panel_url,
            username=settings.panel_username,
            password=settings.panel_password,
            api_path=settings.panel_api_path,
            sub_port=settings.sub_port,
        ) as xui:
            data = await xui.download_panel_db()
    except Exception as exc:
        _redirect("/admin/backups", err=f"Panel backup failed: {exc}")
    buf = io.BytesIO()
    filename = f"onebot_xui_backup_{_now_ts()}.zip"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("x-ui.db", data)
    return web.Response(
        body=buf.getvalue(),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        content_type="application/zip",
    )


async def backup_logs_download(request: web.Request) -> web.Response:
    _require_auth(request)
    log_path = _log_path()
    log_dir = log_path.parent
    if not log_dir.exists():
        _redirect("/admin/backups", err=f"Log directory not found: {log_dir}")

    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(log_dir.glob("*")):
            if path.is_file():
                zf.write(path, arcname=path.name)
                added += 1
    if not added:
        _redirect("/admin/backups", err="No log files found")

    filename = f"onebot_logs_{_now_ts()}.zip"
    return web.Response(
        body=buf.getvalue(),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        content_type="application/zip",
    )


async def backup_current_log_download(request: web.Request) -> web.Response:
    _require_auth(request)
    log_path = _log_path()
    if not log_path.exists():
        _redirect("/admin/backups", err=f"Current log file not found: {log_path}")
    return web.Response(
        body=log_path.read_bytes(),
        headers={"Content-Disposition": f'attachment; filename="{log_path.name}"'},
        content_type="text/plain",
    )


def _extract_uploaded_sqlite(raw: bytes, filename: str) -> bytes:
    name = filename.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
            candidates = [
                n for n in zf.namelist()
                if n.lower().endswith((".db", ".sqlite", ".sqlite3"))
                and not n.endswith("/")
            ]
            if not candidates:
                raise ValueError("Zip file does not contain a .db/.sqlite file")
            return zf.read(candidates[0])
    return raw


def _validate_sqlite_bytes(raw: bytes) -> None:
    if not raw.startswith(b"SQLite format 3\x00"):
        raise ValueError("Uploaded file is not a SQLite database")


async def _current_bot_identity_settings() -> dict[str, str]:
    values: dict[str, str] = {}
    async with AsyncSessionLocal() as session:
        for key, fallback in (
            ("BOT_TOKEN", settings.bot_token),
            ("BOT_TOKEN_SOURCE", ""),
            ("BOT_USERNAME", settings.bot_username),
        ):
            value = (await get_setting(session, key, fallback or "")).strip()
            if value:
                values[key] = value
    return values


def _write_sqlite_settings(db_path: Path, values: dict[str, str]) -> None:
    if not values:
        return
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS admin_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')"
        )
        for key, value in values.items():
            conn.execute(
                "INSERT INTO admin_settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()


async def restore_bot_backup(request: web.Request) -> web.Response:
    _require_auth(request)
    if "sqlite" not in settings.db_url:
        _redirect("/admin/backups", err="Web restore currently supports SQLite DB_URL only")

    reader = await request.multipart()
    uploaded = None
    confirm = ""

    async for part in reader:
        if part.name == "backup":
            filename = part.filename or "backup.db"
            raw = await part.read(decode=False)
            uploaded = (filename, raw)
        elif part.name == "confirm":
            confirm = (await part.text()).strip()

    if confirm != "RESTORE":
        _redirect("/admin/backups", err="Restore confirmation failed. Type RESTORE.")
    if not uploaded:
        _redirect("/admin/backups", err="No backup file uploaded")

    preserved_identity = await _current_bot_identity_settings()
    filename, raw = uploaded
    try:
        db_bytes = _extract_uploaded_sqlite(raw, filename)
        _validate_sqlite_bytes(db_bytes)
    except Exception as exc:
        _redirect("/admin/backups", err=f"Invalid backup: {exc}")

    db_path = _sqlite_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        pre_restore = db_path.with_name(f"{db_path.stem}.pre_restore_{_now_ts()}{db_path.suffix}")
        shutil.copy2(db_path, pre_restore)

    tmp_path = db_path.with_name(f"{db_path.stem}.restore_tmp{db_path.suffix}")
    tmp_path.write_bytes(db_bytes)

    # Open once to verify integrity before replacing the live file.
    import sqlite3
    conn = sqlite3.connect(str(tmp_path))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise ValueError(f"SQLite integrity check failed: {result[0] if result else 'unknown'}")
    finally:
        conn.close()

    os.replace(tmp_path, db_path)
    _write_sqlite_settings(db_path, preserved_identity)
    (Path(__file__).resolve().parent.parent / ".onebot-restart").write_text(str(datetime.now().timestamp()), encoding="utf-8")
    _redirect("/admin/backups", msg="Bot database restored. Bot restart requested.")


def setup_web_admin(app: web.Application) -> None:
    """Mount web admin routes on an aiohttp application."""
    if not settings.web_admin_enabled:
        return
    app.router.add_get("/", lambda request: web.HTTPFound("/admin/login"))
    app.router.add_get("/favicon.ico", lambda request: web.Response(status=204))
    app.router.add_get("/admin/login", login_get)
    app.router.add_post("/admin/login", login_post)
    app.router.add_get("/admin/logout", logout)
    app.router.add_get("/admin", dashboard)
    app.router.add_get("/admin/activity", dashboard_activity)
    app.router.add_get("/admin/stats", stats_page)
    app.router.add_get("/admin/customers", customers)
    app.router.add_post("/admin/customers/create", customer_create)
    app.router.add_get("/admin/customers/{user_id:\\d+}", customer_detail)
    app.router.add_post("/admin/customers/{user_id:\\d+}/toggle-admin", customer_toggle_admin)
    app.router.add_post("/admin/customers/{user_id:\\d+}/message", customer_message)
    app.router.add_post("/admin/customers/{user_id:\\d+}/block", customer_block)
    app.router.add_post("/admin/customers/{user_id:\\d+}/delete", customer_delete)
    app.router.add_post("/admin/customers/{user_id:\\d+}/attach-plan", customer_attach_plan)
    app.router.add_post("/admin/customers/{user_id:\\d+}/subscriptions/{sub_id:\\d+}/detach", customer_detach_subscription)
    app.router.add_post("/admin/customers/{user_id:\\d+}/subscriptions/{sub_id:\\d+}/remove", customer_remove_subscription)
    app.router.add_post("/admin/customers/{user_id:\\d+}/subscriptions/{sub_id:\\d+}/rotate", customer_rotate_subscription)
    app.router.add_get("/admin/inbounds", inbounds_get)
    app.router.add_post("/admin/inbounds", inbounds_post)
    app.router.add_get("/admin/inbounds/list-json", inbounds_json)
    app.router.add_get("/admin/server", server_page)
    app.router.add_post("/admin/server/restart-xray", server_restart_xray)
    app.router.add_get("/admin/tickets", tickets_page)
    app.router.add_post("/admin/tickets/{ticket_id:\\d+}/status", ticket_set_status)
    app.router.add_get("/admin/plans", plans_get)
    app.router.add_post("/admin/plans/create", plans_create)
    app.router.add_post("/admin/plans/update", plans_update)
    app.router.add_post("/admin/plans/delete", plans_delete)
    app.router.add_get("/admin/test-plan", test_plan_page)
    app.router.add_post("/admin/test-plan", test_plan_save)
    app.router.add_get("/admin/payments", payments)
    app.router.add_get("/admin/receipts", receipts_get)
    app.router.add_get("/admin/receipts/{payment_id:\\d+}/photo", receipt_photo)
    app.router.add_post("/admin/receipts/{payment_id:\\d+}/approve", receipt_approve)
    app.router.add_post("/admin/receipts/{payment_id:\\d+}/reject", receipt_reject)
    app.router.add_get("/admin/discounts", discounts_get)
    app.router.add_post("/admin/discounts/create", discounts_create)
    app.router.add_post("/admin/discounts/delete", discounts_delete)
    app.router.add_get("/admin/content", content_page)
    app.router.add_post("/admin/content", content_save)
    app.router.add_get("/admin/broadcast", broadcast_page)
    app.router.add_post("/admin/broadcast", broadcast_send)
    app.router.add_get("/admin/security", security_page)
    app.router.add_post("/admin/security", security_save)
    app.router.add_get("/admin/settings", settings_get)
    app.router.add_post("/admin/settings/runtime", settings_runtime_post)
    app.router.add_post("/admin/settings/env", settings_env_post)
    app.router.add_get("/admin/backups", backups_get)
    app.router.add_get("/admin/backups/bot", backup_bot_download)
    app.router.add_get("/admin/backups/panel", backup_panel_download)
    app.router.add_get("/admin/backups/logs", backup_logs_download)
    app.router.add_get("/admin/backups/log-current", backup_current_log_download)
    app.router.add_post("/admin/backups/restore-bot", restore_bot_backup)
