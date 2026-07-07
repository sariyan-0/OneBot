import { COOKIE_FALLBACK_NAME, COOKIE_NAME, getAuthConfig, makeCookie } from "../../../../lib/auth";
import { isSecureRequest, redirectSeeOther } from "../../../../lib/redirect";

export async function POST(request) {
  const form = await request.formData();
  const username = String(form.get("username") || "").trim();
  const password = String(form.get("password") || "").trim();

  const { username: expectedUser, password: expectedPass } = await getAuthConfig();

  if (username !== expectedUser || password !== expectedPass) {
    return redirectSeeOther(request, `/login?error=${encodeURIComponent("Invalid username or password")}`);
  }

  const response = redirectSeeOther(request, "/admin");
  const cookieValue = await makeCookie(username);
  const cookieOptions = {
    httpOnly: true,
    sameSite: "lax",
    secure: isSecureRequest(request),
    path: "/",
    maxAge: 60 * 60 * 24 * 7,
  };
  response.cookies.set(COOKIE_NAME, cookieValue, cookieOptions);
  response.cookies.set(COOKIE_FALLBACK_NAME, cookieValue, {
    ...cookieOptions,
    httpOnly: false,
  });
  return response;
}
