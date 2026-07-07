import { NextResponse } from "next/server";
import { exec, many, one } from "../../../lib/db";
import { redirectSeeOther } from "../../../lib/redirect";

function rowToDiscount(row) {
  return {
    id: Number(row.id),
    code: row.code,
    percent: Number(row.percent || 0),
    max_uses: row.max_uses == null ? null : Number(row.max_uses),
    used_count: Number(row.used_count || 0),
    is_active: Number(row.is_active ? 1 : 0),
    expires_at: row.expires_at || null,
    created_at: row.created_at || null,
  };
}

export async function GET() {
  const rows = await many("SELECT * FROM discount_codes ORDER BY created_at DESC, id DESC");
  return NextResponse.json({ discounts: rows.map(rowToDiscount), source: "live" });
}

export async function POST(request) {
  const contentType = request.headers.get("content-type") || "";
  const body = contentType.includes("application/json")
    ? await request.json().catch(() => ({}))
    : Object.fromEntries((await request.formData()).entries());
  const action = String(body.action || "create");

  if (action === "create") {
    const code = String(body.code || "").trim().toUpperCase();
    const percent = Number(body.percent || 0);
    const maxUses = body.max_uses === "" || body.max_uses == null ? null : Number(body.max_uses);
    const expireDays = body.expire_days === "" || body.expire_days == null ? null : Number(body.expire_days);
    const expiresAt = expireDays ? new Date(Date.now() + expireDays * 86400000).toISOString() : null;

    if (!code || !percent || percent < 1 || percent > 100) {
      return NextResponse.json({ ok: false, error: "Code and percent are required." }, { status: 400 });
    }

    await exec(
      `INSERT INTO discount_codes(code, percent, max_uses, used_count, is_active, expires_at, created_at)
       VALUES (?, ?, ?, 0, 1, ?, CURRENT_TIMESTAMP)`,
      [code, percent, maxUses, expiresAt]
    );

    return redirectSeeOther(request, "/admin/discounts");
  }

  if (action === "toggle") {
    const id = Number(body.id || 0);
    const isActive = Number(body.is_active || 0);
    if (!id) {
      return NextResponse.json({ ok: false, error: "Invalid discount id." }, { status: 400 });
    }
    await exec("UPDATE discount_codes SET is_active = ? WHERE id = ?", [isActive, id]);
    return redirectSeeOther(request, "/admin/discounts");
  }

  if (action === "delete") {
    const id = Number(body.id || 0);
    if (!id) {
      return NextResponse.json({ ok: false, error: "Invalid discount id." }, { status: 400 });
    }
    await exec("DELETE FROM discount_codes WHERE id = ?", [id]);
    return redirectSeeOther(request, "/admin/discounts");
  }

  return NextResponse.json({ ok: false, error: "Unsupported action" }, { status: 400 });
}
