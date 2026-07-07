import { NextResponse } from "next/server";

export async function GET() {
  return NextResponse.json({
    ok: true,
    service: "onebot-web-panel",
    timestamp: new Date().toISOString(),
  });
}
