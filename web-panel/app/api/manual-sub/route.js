import { NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { createClient, buildSubLink, getClientLinks, getPanelConnectionConfig } from "../../../lib/xui";
import { exec, getSetting, setSetting, one } from "../../../lib/db";

async function nextEmail(prefix = "client") {
  const raw = await getSetting("sub_counter_client", "0");
  const n = Number(raw || 0) + 1;
  await setSetting("sub_counter_client", String(n));
  return `${prefix}-${n}`;
}

function toIsoDays(days) {
  const n = Number(days || 0);
  if (!n) return null;
  return new Date(Date.now() + n * 86400000).toISOString();
}

export async function POST(request) {
  const body = await request.json().catch(() => ({}));
  const userId = Number(body.userId || 0);
  const telegramId = Number(body.telegramId || 0);
  const mode = String(body.mode || "plan");

  const user = await one("SELECT * FROM users WHERE id = ? OR telegram_id = ?", [userId, telegramId]);
  if (!user) {
    return NextResponse.json({ ok: false, error: "User not found." }, { status: 404 });
  }

  let plan = null;
  let inboundIds = Array.isArray(body.inboundIds) ? body.inboundIds.map(Number).filter(Boolean) : [];
  let trafficGb = Number(body.trafficGb || 0);
  let durationDays = Number(body.durationDays || 0);
  let limitIp = Number(body.limitIp || 0);

  if (mode === "plan") {
    const planId = Number(body.planId || 0);
    if (!planId) {
      return NextResponse.json({ ok: false, error: "Plan is required." }, { status: 400 });
    }
    plan = await one("SELECT * FROM plans WHERE id = ?", [planId]);
    if (!plan) {
      return NextResponse.json({ ok: false, error: "Plan not found." }, { status: 404 });
    }
    if (!inboundIds.length) {
      inboundIds = String(plan.inbound_ids || "")
        .split(",")
        .map((value) => Number(value.trim()))
        .filter(Boolean);
    }
    trafficGb = Number(plan.traffic_gb || 0);
    durationDays = Number(plan.duration_days || 0);
    limitIp = Number(plan.limit_ip || 0);
  }

  if (!inboundIds.length && mode !== "plan") {
    return NextResponse.json({ ok: false, error: "Select at least one inbound." }, { status: 400 });
  }

  const selectedInbounds = inboundIds.length ? inboundIds : [Number(body.inboundId || 0)].filter(Boolean);
  if (!selectedInbounds.length) {
    return NextResponse.json({ ok: false, error: "No inbound selected." }, { status: 400 });
  }

  const email = String(body.email || await nextEmail("client"));
  const subId = String(body.subId || randomUUID().replace(/-/g, "").slice(0, 16));
  const expiryDate = toIsoDays(durationDays);

  const created = await createClient({
    inboundIds: selectedInbounds,
    email,
    trafficGb,
    expireDays: durationDays,
    subId,
    limitIp,
    tgId: telegramId,
  });

  const clientUuid = String(created?.uuid || created?.id || created?.sub_id || subId);
  const panelCfg = await getPanelConnectionConfig();
  const subLink = buildSubLink(panelCfg.panelUrl || "", subId, Number(panelCfg.subPort || 0));
  const configLinks = await getClientLinks(email).catch(() => []);

  await exec(
    `INSERT INTO subscriptions(
      user_id, email, client_uuid, sub_id, plan_id, inbound_id,
      traffic_limit_gb, used_traffic_bytes, expiry_date, limit_ip,
      warned_7d, warned_3d, warned_1d, status, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 0, 0, 0, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)`,
    [
      user.id,
      email,
      clientUuid,
      subId,
      plan?.id ?? null,
      selectedInbounds[0],
      trafficGb,
      expiryDate,
      limitIp,
    ]
  );

  return NextResponse.json({
    ok: true,
    email,
    subId,
    subLink,
    configLinks,
    clientUuid,
  });
}
