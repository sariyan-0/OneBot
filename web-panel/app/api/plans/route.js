import { NextResponse } from "next/server";
import { exec, many } from "../../../lib/db";
import { redirectSeeOther } from "../../../lib/redirect";

export async function GET() {
  const plans = await many("SELECT * FROM plans ORDER BY sort_order ASC, id ASC");
  return NextResponse.json({ plans });
}

export async function POST(request) {
  const form = await request.formData();
  const action = String(form.get("action") || "create");

  if (action !== "create") {
    return NextResponse.json({ ok: false, error: "Unsupported action" }, { status: 400 });
  }

  const name = String(form.get("name") || "").trim();
  const priceUsdt = Number(form.get("price_usdt") || 0);
  const priceToman = Number(form.get("price_toman") || 0);
  const trafficGb = Number(form.get("traffic_gb") || 0);
  const durationDays = Number(form.get("duration_days") || 30);
  const inboundIds = String(form.get("inbound_ids") || "").trim();

  await exec(
    `INSERT INTO plans(name, traffic_gb, duration_days, price_usdt, price_toman, limit_ip, inbound_ids, is_active, sort_order)
     VALUES (?, ?, ?, ?, ?, 0, ?, 1, COALESCE((SELECT MAX(sort_order) FROM plans), 0) + 1)`,
    [name, trafficGb, durationDays, priceUsdt, priceToman, inboundIds]
  );

  return redirectSeeOther(request, "/admin/plans");
}
