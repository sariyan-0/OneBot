import { NextResponse } from "next/server";
import { many, one } from "../../../lib/db";

export async function GET(request) {
  const url = new URL(request.url);
  const search = String(url.searchParams.get("search") || "").trim();
  const userId = Number(url.searchParams.get("user_id") || 0);
  const page = Math.max(1, Number(url.searchParams.get("page") || 1));
  const limit = Math.min(50, Math.max(1, Number(url.searchParams.get("limit") || 20)));
  const offset = (page - 1) * limit;

  if (userId) {
    const user = await one("SELECT * FROM users WHERE id = ? OR telegram_id = ?", [userId, userId]);
    const subscriptions = user
      ? await many("SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC", [user.id])
      : [];
    return NextResponse.json({ user, subscriptions });
  }

  const where = search
    ? "WHERE LOWER(CAST(telegram_id AS TEXT)) LIKE LOWER(?) OR LOWER(COALESCE(username, '')) LIKE LOWER(?) OR LOWER(COALESCE(first_name, '')) LIKE LOWER(?)"
    : "";
  const params = search ? [`%${search}%`, `%${search}%`, `%${search}%`] : [];
  const users = await many(
    `SELECT * FROM users ${where} ORDER BY created_at DESC LIMIT ? OFFSET ?`,
    [...params, limit, offset]
  );
  const totalRow = await one(
    `SELECT COUNT(*) AS count FROM users ${where}`,
    params
  );

  return NextResponse.json({
    users,
    total: Number(totalRow?.count || 0),
  });
}
