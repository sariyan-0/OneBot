import { NextResponse } from "next/server";
import { COOKIE_FALLBACK_NAME, COOKIE_NAME } from "../../../../lib/auth";
import { redirectSeeOther } from "../../../../lib/redirect";

export const runtime = "nodejs";

export async function GET() {
  return NextResponse.json({ ok: false, error: "Method not allowed" }, { status: 405 });
}

export async function POST(request) {
  const response = redirectSeeOther(request, "/login");
  response.cookies.set(COOKIE_NAME, "", { path: "/", maxAge: 0 });
  response.cookies.set(COOKIE_FALLBACK_NAME, "", { path: "/", maxAge: 0 });
  return response;
}
