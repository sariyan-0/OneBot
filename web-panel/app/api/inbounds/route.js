import { NextResponse } from "next/server";
import { setSetting, getSetting } from "../../../lib/db";
import { redirectSeeOther } from "../../../lib/redirect";
import { getInbounds } from "../../../lib/xui";

const CACHE_MS = 30_000;
let cachedInbounds = null;
let cachedAt = 0;

export async function GET() {
  const now = Date.now();
  let source = "live";
  let inbounds = cachedInbounds;
  if (!cachedInbounds || now - cachedAt > CACHE_MS) {
    try {
      inbounds = await getInbounds();
      cachedInbounds = inbounds;
      cachedAt = now;
    } catch {
      inbounds = cachedInbounds || [];
      source = cachedInbounds ? "cache" : "unavailable";
    }
  } else {
    source = "cache";
  }

  const selected = String(await getSetting("enabled_inbound_ids", "") || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  return NextResponse.json({ inbounds, selected, source });
}

export async function POST(request) {
  const form = await request.formData();
  const ids = form.getAll("inbound_ids").map((value) => String(value)).filter(Boolean);
  await setSetting("enabled_inbound_ids", ids.join(","));
  return redirectSeeOther(request, "/admin/inbounds");
}
