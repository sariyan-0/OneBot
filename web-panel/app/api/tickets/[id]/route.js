import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

import { exec, many, one, getSetting } from "../../../../lib/db";
import { redirectSeeOther } from "../../../../lib/redirect";

async function getAdminSenderId() {
  const row = await one("SELECT id FROM users WHERE is_admin = 1 ORDER BY id ASC LIMIT 1");
  return row?.id || null;
}

async function sendTelegramMessage(chatId, text) {
  const token = String((await getSetting("BOT_TOKEN", "")) || process.env.BOT_TOKEN || "").trim();
  if (!token || !chatId) return;
  await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text,
      parse_mode: "HTML",
    }),
  });
}

async function sendTelegramTicketReply(chatId, ticketId, subject, body) {
  const token = String((await getSetting("BOT_TOKEN", "")) || process.env.BOT_TOKEN || "").trim();
  if (!token || !chatId) return;
  const activeSessions = String(await getSetting("active_ticket_session_ids", "") || "")
    .split(",")
    .map((part) => Number(part.trim()))
    .filter((id) => Number.isInteger(id) && id > 0);
  const hasActiveSession = activeSessions.includes(Number(chatId));
  const replyMarkup = hasActiveSession ? null : {
    inline_keyboard: [
      [
        { text: "✍️ پاسخ", callback_data: `ticket_reply:${ticketId}` },
        { text: "🔒 بستن تیکت", callback_data: `ticket_close:${ticketId}` },
      ],
      [
        { text: "🚪 خروج از گفتگو", callback_data: "ticket_exit" },
      ],
    ],
  };
  return fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      parse_mode: "HTML",
      text: [
        `📨 <b>پاسخ پشتیبانی برای تیکت</b>`,
        `📌 موضوع: ${escapeHtml(subject)}`,
        "",
        `💬 پاسخ ادمین:`,
        escapeHtml(body),
      ].join("\n"),
      ...(replyMarkup ? { reply_markup: replyMarkup } : {}),
    }),
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function getTicketBundle(ticketId) {
  const ticket = await one(
    `SELECT t.*, u.telegram_id, u.username, u.first_name
     FROM tickets t
     LEFT JOIN users u ON u.id = t.user_id
     WHERE t.id = ?`,
    [ticketId]
  );
  const messages = await many(
    `SELECT tm.*, u.telegram_id, u.username, u.first_name
     FROM ticket_messages tm
     LEFT JOIN users u ON u.id = tm.sender_id
     WHERE tm.ticket_id = ?
     ORDER BY tm.created_at ASC`,
    [ticketId]
  );
  return { ticket, messages };
}

function wantsJson(request) {
  const accept = request.headers.get("accept") || "";
  return accept.includes("application/json") || request.headers.get("x-onebot-client") === "ticket-thread";
}

function redirectToTicket(request, ticketId, status = "ok") {
  const url = new URL(request.headers.get("referer") || request.url);
  url.pathname = `/admin/tickets/${ticketId}`;
  url.searchParams.set("status", status);
  return redirectSeeOther(request, url);
}

export async function GET(request, { params }) {
  const ticketId = Number(params.id);
  if (!Number.isFinite(ticketId)) {
    return NextResponse.json({ ok: false, error: "Invalid ticket id" }, { status: 400 });
  }

  const bundle = await getTicketBundle(ticketId);
  if (!bundle.ticket) {
    return NextResponse.json({ ok: false, error: "Ticket not found" }, { status: 404 });
  }

  return NextResponse.json({
    ok: true,
    ticket: bundle.ticket,
    messages: bundle.messages,
  }, {
    headers: {
      "Cache-Control": "no-store, max-age=0",
    },
  });
}

export async function POST(request, { params }) {
  const ticketId = Number(params.id);
  if (!Number.isFinite(ticketId)) {
    return NextResponse.json({ ok: false, error: "Invalid ticket id" }, { status: 400 });
  }

  const form = await request.formData();
  const action = String(form.get("action") || "");
  const body = String(form.get("body") || "").trim();

  const ticket = await one("SELECT * FROM tickets WHERE id = ?", [ticketId]);
  if (!ticket) {
    return NextResponse.json({ ok: false, error: "Ticket not found" }, { status: 404 });
  }
  const jsonMode = wantsJson(request);

  if (action === "reply") {
    if (!body) {
      return jsonMode
        ? NextResponse.json({ ok: false, error: "empty_reply" }, { status: 400 })
        : redirectSeeOther(request, `/admin/tickets/${ticketId}?error=empty_reply`);
    }
    const senderId = await getAdminSenderId();
    if (!senderId) {
      return NextResponse.json({ ok: false, error: "No admin user exists in the database" }, { status: 409 });
    }

    await exec(
      "INSERT INTO ticket_messages(ticket_id, sender_id, body, is_admin_reply, created_at) VALUES(?, ?, ?, 1, CURRENT_TIMESTAMP)",
      [ticketId, senderId, body]
    );
    await exec(
      "UPDATE tickets SET status = 'in_progress', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
      [ticketId]
    );

    const bundle = await getTicketBundle(ticketId);
    if (bundle.ticket?.telegram_id) {
      try {
        await sendTelegramTicketReply(bundle.ticket.telegram_id, ticketId, bundle.ticket.subject, body);
      } catch {
        // Keep admin flow alive even when Telegram is unreachable.
      }
    }
    const createdMessage = await one(
      `SELECT tm.*, u.telegram_id, u.username, u.first_name
       FROM ticket_messages tm
       LEFT JOIN users u ON u.id = tm.sender_id
       WHERE tm.ticket_id = ?
       ORDER BY tm.id DESC
       LIMIT 1`,
      [ticketId]
    );
    return jsonMode
      ? NextResponse.json({
          ok: true,
          ticketId,
          status: "in_progress",
          message: createdMessage || null,
        })
      : redirectToTicket(request, ticketId, "replied");
  }

  if (action === "reopen") {
    await exec(
      "UPDATE tickets SET status = 'open', closed_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
      [ticketId]
    );
    return jsonMode
      ? NextResponse.json({ ok: true, ticketId, status: "open" })
      : redirectToTicket(request, ticketId, "reopened");
  }

  if (action === "close") {
    await exec(
      "UPDATE tickets SET status = 'closed', closed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
      [ticketId]
    );
    return jsonMode
      ? NextResponse.json({ ok: true, ticketId, status: "closed" })
      : redirectToTicket(request, ticketId, "closed");
  }

  return NextResponse.json({ ok: false, error: "Unsupported action" }, { status: 400 });
}
