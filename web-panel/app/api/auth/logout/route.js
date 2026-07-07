import { COOKIE_FALLBACK_NAME, COOKIE_NAME } from "../../../../lib/auth";
import { redirectSeeOther } from "../../../../lib/redirect";

export const runtime = "nodejs";

export async function GET(request) {
  const response = redirectSeeOther(request, "/login");
  response.cookies.set(COOKIE_NAME, "", { path: "/", maxAge: 0 });
  response.cookies.set(COOKIE_FALLBACK_NAME, "", { path: "/", maxAge: 0 });
  return response;
}
