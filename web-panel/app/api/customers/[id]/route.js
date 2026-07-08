import { NextResponse } from "next/server";
import { exec, one, setSetting } from "../../../../lib/db";
import { redirectSeeOther } from "../../../../lib/redirect";
import { getTelegramBotToken } from "../../../../lib/telegram-token";

async function telegramRequest(token, method, body) {
  const res = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data?.description || `Telegram API error: ${res.status}`);
  }
  return res.json().catch(() => ({}));
}

function redirectBack(request, userId, status = "saved") {
  const url = new URL(request.headers.get("referer") || request.url);
  url.pathname = `/admin/customers/${userId}`;
  url.searchParams.set("status", status);
  return redirectSeeOther(request, url);
}

export async function POST(request, { params }) {
  const form = await request.formData();
  const action = String(form.get("action") || "");
  const userId = Number(params.id);

  if (!userId) {
    return NextResponse.json({ ok: false, error: "Invalid customer ID" }, { status: 400 });
  }

  if (action === "wallet_adjust") {
    const amount = Number(form.get("amount") || 0);
    const mode = String(form.get("mode") || "credit");
    if (!Number.isFinite(amount) || amount <= 0) {
      return NextResponse.json({ ok: false, error: "Amount must be positive" }, { status: 400 });
    }
    const currentRow = await one("SELECT wallet_balance_usdt FROM users WHERE id = ?", [userId]);
    const currentBalance = Number(currentRow?.wallet_balance_usdt || 0);
    if (mode === "credit") {
      await exec(
        "UPDATE users SET wallet_balance_usdt = wallet_balance_usdt + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        [amount, userId]
      );
    } else {
      if (currentBalance < amount) {
        return NextResponse.json({ ok: false, error: "Insufficient wallet balance" }, { status: 400 });
      }
      await exec(
        "UPDATE users SET wallet_balance_usdt = wallet_balance_usdt - ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        [amount, userId]
      );
    }
    return redirectBack(request, userId, "wallet_updated");
  }

  if (action === "wallet_adjust_toman") {
    const amount = Number(form.get("amount") || 0);
    const mode = String(form.get("mode") || "credit");
    if (!Number.isFinite(amount) || amount <= 0) {
      return NextResponse.json({ ok: false, error: "Amount must be positive" }, { status: 400 });
    }
    const amountToman = Math.round(amount);
    const currentRow = await one("SELECT wallet_balance_toman FROM users WHERE id = ?", [userId]);
    const currentBalance = Number(currentRow?.wallet_balance_toman || 0);
    if (mode === "credit") {
      await exec(
        "UPDATE users SET wallet_balance_toman = wallet_balance_toman + ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        [amountToman, userId]
      );
    } else {
      if (currentBalance < amountToman) {
        return NextResponse.json({ ok: false, error: "Insufficient wallet balance" }, { status: 400 });
      }
      await exec(
        "UPDATE users SET wallet_balance_toman = wallet_balance_toman - ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        [amountToman, userId]
      );
    }
    return redirectBack(request, userId, "wallet_toman_converted");
  }

  if (action === "direct_message") {
    const message = String(form.get("message") || "").trim();
    if (!message) {
      return NextResponse.json({ ok: false, error: "Message is required" }, { status: 400 });
    }
    const user = await one("SELECT telegram_id, username FROM users WHERE id = ?", [userId]);
    if (!user?.telegram_id) {
      return NextResponse.json({ ok: false, error: "Customer has no Telegram ID" }, { status: 400 });
    }
    const token = await getTelegramBotToken();
    if (!token) {
      return NextResponse.json({ ok: false, error: "BOT_TOKEN is not configured" }, { status: 400 });
    }
    await telegramRequest(token, "sendMessage", { chat_id: user.telegram_id, text: message });
    await exec(
      "INSERT INTO activity_logs(direction, event_type, telegram_id, username, text, created_at) VALUES(?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
      ["outgoing", "direct_message", Number(user.telegram_id), String(user.username || "").replace(/^@/, "") || null, message]
    );
    return redirectBack(request, userId, "message_sent");
  }

  if (action === "toggle_admin") {
    const value = String(form.get("value") || "0") === "1";
    await exec("UPDATE users SET is_admin = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", [value ? 1 : 0, userId]);
    return redirectBack(request, userId, "admin_updated");
  }

  if (action === "toggle_block") {
    const telegramId = Number(form.get("telegram_id") || 0);
    const blockedRaw = await one("SELECT value FROM admin_settings WHERE key = 'blocked_telegram_ids'");
    const current = new Set(
      String(blockedRaw?.value || "")
        .split(",")
        .map((item) => item.trim())
        .filter((item) => item)
    );
    const key = String(telegramId);
    if (current.has(key)) current.delete(key); else current.add(key);
    await setSetting("blocked_telegram_ids", Array.from(current).join(","));
    return redirectBack(request, userId, current.has(key) ? "blocked" : "unblocked");
  }

  if (action === "delete_user") {
    await exec("DELETE FROM users WHERE id = ?", [userId]);
    const url = new URL(request.headers.get("referer") || request.url);
    url.pathname = "/admin/customers";
    url.searchParams.set("status", "deleted");
    return redirectSeeOther(request, url);
  }

  return NextResponse.json({ ok: false, error: "Unknown action" }, { status: 400 });
}
