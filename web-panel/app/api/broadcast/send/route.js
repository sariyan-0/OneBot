import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { many } from "../../../../lib/db";
import { isAdminAuth } from "../../../../lib/auth";
import { redirectSeeOther } from "../../../../lib/redirect";
import { getTelegramBotToken } from "../../../../lib/telegram-token";

export const runtime = "nodejs";

const MAX_TELEGRAM_PHOTO_BYTES = 10 * 1024 * 1024;

async function botToken() {
  return getTelegramBotToken();
}

async function telegramRequest(token, method, body) {
  const url = `https://api.telegram.org/bot${token}/${method}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data?.description || `Telegram API error: ${res.status}`);
  }
  return res.json();
}

async function readBroadcastPayload(request) {
  const contentType = String(request.headers.get("content-type") || "");
  if (contentType.includes("application/json")) {
    const payload = await request.json().catch(() => ({}));
    return {
      message: String(payload.message || "").trim(),
      image: null,
    };
  }

  const form = await request.formData();
  return {
    message: String(form.get("message") || "").trim(),
    image: form.get("image"),
  };
}

function errorResponse(request, isClientRequest, error, status = 400) {
  return isClientRequest
    ? NextResponse.json({ ok: false, error }, { status })
    : redirectSeeOther(request, `/admin/broadcast?error=${encodeURIComponent(error)}`);
}

async function sendPhoto(token, chatId, caption, image, buffer) {
  const formData = new FormData();
  formData.append("chat_id", String(chatId));
  formData.append("caption", caption);
  formData.append("photo", new Blob([buffer], { type: image.type || "image/png" }), image.name || "broadcast.png");
  const res = await fetch(`https://api.telegram.org/bot${token}/sendPhoto`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data?.description || `Telegram API error: ${res.status}`);
  }
}

export async function POST(request) {
  const isClientRequest = request.headers.get("x-onebot-client") === "1";
  if (!(await isAdminAuth(cookies()))) {
    return errorResponse(request, isClientRequest, "Unauthorized", 401);
  }

  const { message, image } = await readBroadcastPayload(request);
  const token = await botToken();
  const users = await many("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL ORDER BY id ASC LIMIT 10000");

  if (!message) {
    return errorResponse(request, isClientRequest, "Message is required", 400);
  }
  if (!token) {
    return errorResponse(request, isClientRequest, "BOT_TOKEN is not configured", 400);
  }

  let sent = 0;
  let failed = 0;
  let firstError = "";

  if (image && typeof image.arrayBuffer === "function" && image.size > 0) {
    if (image.type && !String(image.type).startsWith("image/")) {
      return errorResponse(request, isClientRequest, "Broadcast upload must be an image file", 400);
    }
    if (image.size > MAX_TELEGRAM_PHOTO_BYTES) {
      return errorResponse(request, isClientRequest, "Broadcast image is too large. Maximum size is 10 MB", 400);
    }

    const buffer = Buffer.from(await image.arrayBuffer());
    for (const user of users) {
      try {
        await sendPhoto(token, user.telegram_id, message, image, buffer);
        sent += 1;
      } catch (err) {
        failed += 1;
        firstError ||= err.message || "Telegram send failed";
      }
    }
  } else {
    for (const user of users) {
      try {
        await telegramRequest(token, "sendMessage", { chat_id: user.telegram_id, text: message });
        sent += 1;
      } catch (err) {
        failed += 1;
        firstError ||= err.message || "Telegram send failed";
      }
    }
  }

  if (!sent && failed) {
    return errorResponse(request, isClientRequest, firstError || "Telegram send failed", 502);
  }

  return isClientRequest
    ? NextResponse.json({ ok: true, sent, failed, error: firstError || "" })
    : redirectSeeOther(request, "/admin/broadcast");
}
