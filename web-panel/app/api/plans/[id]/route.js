import { NextResponse } from "next/server";
import { exec, one } from "../../../../lib/db";
import { redirectSeeOther } from "../../../../lib/redirect";

export async function POST(request, { params }) {
  const id = Number(params.id || 0);
  if (!id) {
    return NextResponse.json({ ok: false, error: "Invalid plan id." }, { status: 400 });
  }

  const form = await request.formData();
  const action = String(form.get("action") || "update");

  if (action === "toggle") {
    const current = await one("SELECT is_active FROM plans WHERE id = ?", [id]);
    if (!current) {
      return NextResponse.json({ ok: false, error: "Plan not found." }, { status: 404 });
    }
    await exec("UPDATE plans SET is_active = ? WHERE id = ?", [current.is_active ? 0 : 1, id]);
    return redirectSeeOther(request, "/admin/plans");
  }

  if (action === "delete") {
    await exec("DELETE FROM plans WHERE id = ?", [id]);
    return redirectSeeOther(request, "/admin/plans");
  }

  const name = String(form.get("name") || "").trim();
  const priceUsdt = Number(form.get("price_usdt") || 0);
  const priceToman = Number(form.get("price_toman") || 0);
  const trafficGb = Number(form.get("traffic_gb") || 0);
  const durationDays = Number(form.get("duration_days") || 30);
  const inboundIds = String(form.get("inbound_ids") || "").trim();
  const isActive = String(form.get("is_active") || "1") === "1" ? 1 : 0;
  const limitIp = Number(form.get("limit_ip") || 0);
  const sortOrder = Number(form.get("sort_order") || 0);

  await exec(
    `UPDATE plans
     SET name = ?, traffic_gb = ?, duration_days = ?, price_usdt = ?, price_toman = ?, limit_ip = ?, inbound_ids = ?, is_active = ?, sort_order = ?
     WHERE id = ?`,
    [name, trafficGb, durationDays, priceUsdt, priceToman, limitIp, inboundIds, isActive, sortOrder, id]
  );

  return redirectSeeOther(request, "/admin/plans");
}
