import { NextResponse } from "next/server";
import fs from "fs";
import path from "path";
import { many, getSetting } from "../../../../lib/db";
import { redirectSeeOther } from "../../../../lib/redirect";

export const runtime = "nodejs";

async function botToken() {
  return String((await getSetting("BOT_TOKEN", "")) || process.env.BOT_TOKEN || "").trim();
}

async function telegramRequest(token, method, body) {
  const url = `https://api.telegram.org/bot${token}/${method}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`Telegram API error: ${res.status}`);
  }
  return res.json();
}

export async function POST(request) {
  const form = await request.formData();
  const message = String(form.get("message") || "").trim();
  const image = form.get("image");
  const token = await botToken();
  const users = await many("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL ORDER BY id ASC LIMIT 10000");
  const isClientRequest = request.headers.get("x-onebot-client") === "1";

  if (!message) {
    return NextResponse.json({ ok: false, error: "Message is required" }, { status: 400 });
  }
  if (!token) {
    const error = "BOT_TOKEN is not configured";
    return isClientRequest
      ? NextResponse.json({ ok: false, error }, { status: 400 })
      : redirectSeeOther(request, `/admin/broadcast?error=${encodeURIComponent(error)}`);
  }

  if (image && typeof image.arrayBuffer === "function" && image.size > 0) {
    const buffer = Buffer.from(await image.arrayBuffer());
    for (const user of users) {
      const formData = new FormData();
      formData.append("chat_id", String(user.telegram_id));
      formData.append("caption", message);
      formData.append("photo", new Blob([buffer], { type: image.type || "image/png" }), image.name || "broadcast.png");
      const res = await fetch(`https://api.telegram.org/bot${token}/sendPhoto`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const error = data?.description || `Telegram API error: ${res.status}`;
        return isClientRequest
          ? NextResponse.json({ ok: false, error }, { status: 502 })
          : redirectSeeOther(request, `/admin/broadcast?error=${encodeURIComponent(error)}`);
      }
    }
  } else {
    for (const user of users) {
      try {
        await telegramRequest(token, "sendMessage", { chat_id: user.telegram_id, text: message });
      } catch (err) {
        const error = err.message || "Telegram send failed";
        return isClientRequest
          ? NextResponse.json({ ok: false, error }, { status: 502 })
          : redirectSeeOther(request, `/admin/broadcast?error=${encodeURIComponent(error)}`);
      }
    }
  }

  return isClientRequest
    ? NextResponse.json({ ok: true })
    : redirectSeeOther(request, "/admin/broadcast");
}
