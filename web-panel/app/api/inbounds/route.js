import { NextResponse } from "next/server";
import { setSetting, getSetting } from "../../../lib/db";
import { redirectSeeOther } from "../../../lib/redirect";
import { getInbounds } from "../../../lib/xui";

export async function GET() {
  const inbounds = await getInbounds().catch(() => []);
  const selected = String(await getSetting("enabled_inbound_ids", "") || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  return NextResponse.json({ inbounds, selected, source: "live" });
}

export async function POST(request) {
  const form = await request.formData();
  const ids = form.getAll("inbound_ids").map((value) => String(value)).filter(Boolean);
  await setSetting("enabled_inbound_ids", ids.join(","));
  return redirectSeeOther(request, "/admin/inbounds");
}
