import { NextResponse } from "next/server";
import fs from "fs/promises";
import path from "path";
import { getSetting, setSetting } from "../../../lib/db";
import { redirectSeeOther } from "../../../lib/redirect";
import { getTelegramBotToken } from "../../../lib/telegram-token";

export const runtime = "nodejs";

const UPLOAD_DIR = path.join(process.cwd(), "public", "banners");

function adminChatId() {
  const raw = process.env.ADMIN_IDS || process.env.ADMIN_ID || "";
  const id = String(raw)
    .split(",")
    .map((part) => part.trim())
    .find((part) => /^\d+$/.test(part));
  return id || "";
}

async function ensureUploadDir() {
  await fs.mkdir(UPLOAD_DIR, { recursive: true });
}

async function savePreview(file, prefix) {
  await ensureUploadDir();
  const arrayBuffer = await file.arrayBuffer();
  const ext = path.extname(file.name || "") || ".png";
  const fileName = `${prefix}-${Date.now()}${ext}`;
  const absPath = path.join(UPLOAD_DIR, fileName);
  await fs.writeFile(absPath, Buffer.from(arrayBuffer));
  return `/banners/${fileName}`;
}

async function telegramRequest(method, body) {
  const token = await getTelegramBotToken();
  if (!token) throw new Error("BOT_TOKEN is not configured");
  const res = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) {
    throw new Error(data?.description || `Telegram API error: ${res.status}`);
  }
  return data.result;
}

async function uploadBannerFile(file, prefix) {
  const token = await getTelegramBotToken();
  const chatId = adminChatId();
  if (!chatId) {
    throw new Error("ADMIN_IDS is required to generate Telegram file_id values");
  }
  const buffer = Buffer.from(await file.arrayBuffer());
  const formData = new FormData();
  formData.append("chat_id", chatId);
  formData.append("photo", new Blob([buffer], { type: file.type || "image/png" }), file.name || `${prefix}.png`);
  if (!token) throw new Error("BOT_TOKEN is not configured");
  const res = await fetch(`https://api.telegram.org/bot${token}/sendPhoto`, {
    method: "POST",
    body: formData,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) {
    throw new Error(data?.description || `Telegram upload failed: ${res.status}`);
  }
  const photo = data?.result?.photo || [];
  const fileId = photo.length ? photo[photo.length - 1].file_id : "";
  if (!fileId) throw new Error("Telegram did not return a file_id");
  if (data?.result?.message_id) {
    try {
      await telegramRequest("deleteMessage", { chat_id: chatId, message_id: data.result.message_id });
    } catch {
      // best effort cleanup
    }
  }
  return fileId;
}

export async function GET() {
  const [
    bannerFileId,
    bannerPreviewUrl,
    welcomeBannerFileId,
    welcomeBannerPreviewUrl,
    welcomeBannerCaption,
  ] = await Promise.all([
    getSetting("banner_file_id", ""),
    getSetting("banner_preview_url", ""),
    getSetting("welcome_banner_file_id", ""),
    getSetting("welcome_banner_preview_url", ""),
    getSetting("welcome_banner_caption", ""),
  ]);

  return NextResponse.json({
    bannerFileId,
    bannerPreviewUrl,
    welcomeBannerFileId,
    welcomeBannerPreviewUrl,
    welcomeBannerCaption,
  });
}

export async function POST(request) {
  const form = await request.formData();
  const bannerUpload = form.get("banner_upload");
  const welcomeUpload = form.get("welcome_banner_upload");
  const clearBanner = form.get("clear_banner") === "1";
  const clearWelcome = form.get("clear_welcome_banner") === "1";
  const welcomeCaption = String(form.get("welcome_banner_caption") || "").trim();

  if (clearBanner) {
    await setSetting("banner_file_id", "");
    await setSetting("banner_preview_url", "");
  }
  if (clearWelcome) {
    await setSetting("welcome_banner_file_id", "");
    await setSetting("welcome_banner_preview_url", "");
    await setSetting("welcome_banner_caption", "");
  }

  if (bannerUpload && typeof bannerUpload.arrayBuffer === "function" && bannerUpload.size > 0) {
    const preview = await savePreview(bannerUpload, "banner");
    const fileId = await uploadBannerFile(bannerUpload, "banner");
    await setSetting("banner_file_id", fileId);
    await setSetting("banner_preview_url", preview);
  }

  if (welcomeUpload && typeof welcomeUpload.arrayBuffer === "function" && welcomeUpload.size > 0) {
    const preview = await savePreview(welcomeUpload, "welcome-banner");
    const fileId = await uploadBannerFile(welcomeUpload, "welcome-banner");
    await setSetting("welcome_banner_file_id", fileId);
    await setSetting("welcome_banner_preview_url", preview);
  }

  if (welcomeCaption !== "") {
    await setSetting("welcome_banner_caption", welcomeCaption);
  } else if (form.has("welcome_banner_caption")) {
    await setSetting("welcome_banner_caption", "");
  }

  return redirectSeeOther(request, "/admin/banners");
}
