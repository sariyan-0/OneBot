import { NextResponse } from "next/server";

import { one } from "../../../../../lib/db";
import { getTelegramBotToken } from "../../../../../lib/telegram-token";

export async function GET(_request, { params }) {
  const paymentId = Number(params.id);
  if (!paymentId) {
    return NextResponse.json({ ok: false, error: "Invalid payment id" }, { status: 400 });
  }

  const payment = await one(
    "SELECT receipt_file_id, receipt_type FROM payments WHERE id = ?",
    [paymentId]
  );
  if (!payment?.receipt_file_id || payment.receipt_type !== "photo") {
    return NextResponse.json({ ok: false, error: "Receipt image not found" }, { status: 404 });
  }

  const botToken = await getTelegramBotToken();
  if (!botToken) {
    return NextResponse.json({ ok: false, error: "BOT_TOKEN is not configured" }, { status: 500 });
  }

  const metaRes = await fetch(`https://api.telegram.org/bot${botToken}/getFile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_id: payment.receipt_file_id }),
    cache: "no-store",
  });
  const meta = await metaRes.json().catch(() => ({}));
  const filePath = meta?.result?.file_path;
  if (!metaRes.ok || !filePath) {
    return NextResponse.json({ ok: false, error: "Could not resolve Telegram receipt image" }, { status: 404 });
  }

  const fileRes = await fetch(`https://api.telegram.org/file/bot${botToken}/${filePath}`, {
    cache: "no-store",
  });
  if (!fileRes.ok) {
    return NextResponse.json({ ok: false, error: "Could not download Telegram receipt image" }, { status: 404 });
  }

  const bytes = await fileRes.arrayBuffer();
  return new NextResponse(bytes, {
    headers: {
      "Content-Type": fileRes.headers.get("content-type") || "image/jpeg",
      "Cache-Control": "private, no-store, max-age=0",
    },
  });
}
