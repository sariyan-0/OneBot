import { setSetting } from "../../../lib/db";
import { redirectSeeOther } from "../../../lib/redirect";

export async function POST(request) {
  const form = await request.formData();
  const username = String(form.get("web_admin_username") || "").trim();
  const password = String(form.get("web_admin_password") || "").trim();
  const secret = String(form.get("web_admin_cookie_secret") || "").trim();

  if (username) {
    await setSetting("WEB_ADMIN_USERNAME", username);
  }
  if (password) {
    await setSetting("WEB_ADMIN_PASSWORD", password);
  }
  if (secret) {
    await setSetting("WEB_ADMIN_COOKIE_SECRET", secret);
  }

  return redirectSeeOther(request, "/admin/security");
}
