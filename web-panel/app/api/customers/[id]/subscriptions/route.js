import { NextResponse } from "next/server";
import { exec, one } from "../../../../../lib/db";
import { redirectSeeOther } from "../../../../../lib/redirect";
import {
  createClient,
  deleteClient,
  findClientBySubId,
  findClientByUUID,
  getClientLinks,
  getPanelConnectionConfig,
  buildSubLink,
} from "../../../../../lib/xui";

function redirectBack(request, userId, status = "saved") {
  const url = new URL(request.headers.get("referer") || request.url);
  url.pathname = `/admin/customers/${userId}`;
  url.searchParams.set("status", status);
  return redirectSeeOther(request, url);
}

function parseSubscriptionSource(input) {
  const raw = String(input || "").trim();
  if (!raw) return { value: "" };

  if (/^[0-9a-f-]{36}$/i.test(raw)) {
    return { uuid: raw };
  }

  try {
    const url = new URL(raw);
    const parts = url.pathname.split("/").filter(Boolean);
    if (parts.length) {
      return { subId: parts.at(-1) };
    }
  } catch {
    // not a URL
  }

  if (raw.startsWith("sub/")) {
    return { subId: raw.split("/", 2)[1] };
  }

  return { value: raw };
}

async function upsertSubscriptionFromClient(user, client, planId = null) {
  const panelCfg = await getPanelConnectionConfig();
  const subLink = buildSubLink(panelCfg.panelUrl || "", client.subId || client.sub_id || "", Number(panelCfg.subPort || 0));
  const configLinks = await getClientLinks(client.email).catch(() => []);
  const expiryTime = Number(client.expiryTime || 0);
  const expiryDate = expiryTime > 0 ? new Date(expiryTime).toISOString() : null;
  const trafficUp = Number(client.up ?? client.traffic?.up ?? 0);
  const trafficDown = Number(client.down ?? client.traffic?.down ?? 0);

  const existing = await one("SELECT id FROM subscriptions WHERE email = ? LIMIT 1", [client.email]);
  if (existing) {
    await exec(
      `UPDATE subscriptions SET
        user_id = ?, client_uuid = ?, sub_id = ?, plan_id = ?, inbound_id = COALESCE(inbound_id, ?),
        traffic_limit_gb = ?, used_traffic_bytes = ?, expiry_date = ?, limit_ip = ?, status = ?, updated_at = CURRENT_TIMESTAMP
      WHERE id = ?`,
      [
        user.id,
        client.uuid || client.id || "",
        client.subId || client.sub_id || "",
        planId,
        Number(client.inboundIds?.[0] || client.inbound_id || 0),
        Number(client.totalGB || 0) > 0 ? Math.floor(Number(client.totalGB) / 1024 ** 3) : 0,
        trafficUp + trafficDown,
        expiryDate,
        Number(client.limitIp || 0),
        client.enable === false ? "disabled" : "active",
        existing.id,
      ]
    );
  } else {
    await exec(
      `INSERT INTO subscriptions(
        user_id, email, client_uuid, sub_id, plan_id, inbound_id,
        traffic_limit_gb, used_traffic_bytes, expiry_date, limit_ip,
        warned_7d, warned_3d, warned_1d, status, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)`,
      [
        user.id,
        client.email,
        client.uuid || client.id || "",
        client.subId || client.sub_id || "",
        planId,
        Number(client.inboundIds?.[0] || client.inbound_id || 0),
        Number(client.totalGB || 0) > 0 ? Math.floor(Number(client.totalGB) / 1024 ** 3) : 0,
        trafficUp + trafficDown,
        expiryDate,
        Number(client.limitIp || 0),
        client.enable === false ? "disabled" : "active",
      ]
    );
  }

  return {
    ok: true,
    email: client.email,
    subId: client.subId || client.sub_id || "",
    subLink,
    configLinks,
  };
}

export async function POST(request, { params }) {
  const userId = Number(params.id);
  if (!userId) {
    return NextResponse.json({ ok: false, error: "Invalid customer ID" }, { status: 400 });
  }

  const contentType = String(request.headers.get("content-type") || "");
  const body = contentType.includes("application/json")
    ? await request.json().catch(() => ({}))
    : Object.fromEntries(await request.formData());
  const action = String(body.action || "");
  const user = await one("SELECT * FROM users WHERE id = ?", [userId]);
  if (!user) {
    return NextResponse.json({ ok: false, error: "Customer not found" }, { status: 404 });
  }

  if (action === "add_from_plan") {
    const planId = Number(body.planId || body.plan_id || 0);
    if (!planId) {
      return NextResponse.json({ ok: false, error: "Plan is required" }, { status: 400 });
    }
    const plan = await one("SELECT * FROM plans WHERE id = ?", [planId]);
    if (!plan) {
      return NextResponse.json({ ok: false, error: "Plan not found" }, { status: 404 });
    }
    const inboundIds = String(plan.inbound_ids || "")
      .split(",")
      .map((value) => Number(value.trim()))
      .filter(Boolean);

    const email = String(body.email || `manual-${user.telegram_id}-${Date.now()}`).trim();
    const created = await createClient({
      inboundIds,
      email,
      trafficGb: Number(plan.traffic_gb || 0),
      expireDays: Number(plan.duration_days || 0),
      subId: null,
      limitIp: Number(plan.limit_ip || 0),
      tgId: Number(user.telegram_id || 0),
    });

    const payload = await upsertSubscriptionFromClient(user, created, plan.id);
    return NextResponse.json(payload);
  }

  if (action === "import_link") {
    const source = parseSubscriptionSource(body.source);
    let client = null;
    if (source.uuid) {
      client = await findClientByUUID(source.uuid);
    }
    if (!client && source.subId) {
      client = await findClientBySubId(source.subId);
    }
    if (!client && source.value && /^[0-9a-f-]{36}$/i.test(source.value)) {
      client = await findClientByUUID(source.value);
    }
    if (!client) {
      return NextResponse.json({ ok: false, error: "Subscription not found in panel" }, { status: 404 });
    }

    const payload = await upsertSubscriptionFromClient(user, client, null);
    return NextResponse.json(payload);
  }

  if (action === "remove_subscription") {
    const subId = Number(body.subId || body.sub_id || 0);
    const sub = await one("SELECT * FROM subscriptions WHERE id = ? AND user_id = ?", [subId, userId]);
    if (!sub) {
      return NextResponse.json({ ok: false, error: "Subscription not found" }, { status: 404 });
    }
    if (sub.email) {
      try {
        await deleteClient(sub.email);
      } catch {
        // best-effort remove from panel; DB still gets marked deleted below
      }
    }
    await exec("UPDATE payments SET subscription_id = NULL WHERE subscription_id = ?", [subId]);
    await exec("DELETE FROM subscriptions WHERE id = ?", [subId]);
    return NextResponse.json({ ok: true, subId, status: "removed" });
  }

  return NextResponse.json({ ok: false, error: "Unknown action" }, { status: 400 });
}
