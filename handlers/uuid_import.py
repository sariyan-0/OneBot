"""
handlers/uuid_import.py — افزودن اشتراک قدیمی

کاربری که از خارج از ربات (دستی یا از طریق شخص دیگری) کانفیگ گرفته،
می‌تواند UUID یا لینک اشتراک را وارد کند تا ربات لینک اشتراک و کانفیگ‌هایش را بیاورد.

امنیت:
  • بدون شناسه معتبر دسترسی ممکن نیست
  • اشتراک در دیتابیس ربات ذخیره می‌شود تا در «اشتراک‌های من» نمایش داده شود
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from loguru import logger

from config import settings
from database import AsyncSessionLocal
from database.crud import get_or_create_user, get_subscription_by_email
from database.models import Subscription
from keyboards.main_menu import get_main_menu
from services.xui_api import XUIClient, XUIError

router = Router(name="uuid_import")

# الگوی UUID استاندارد
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class UUIDImportStates(StatesGroup):
    waiting_uuid = State()


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────

def _is_valid_uuid(text: str) -> bool:
    return bool(_UUID_RE.match(text.strip()))


def _extract_sub_id(text: str) -> str:
    raw = text.strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        path_parts = [p for p in parsed.path.split("/") if p]
        if path_parts and path_parts[-2:] and path_parts[-2] == "sub":
            return path_parts[-1].strip()
        if path_parts and path_parts[-1]:
            return path_parts[-1].strip()
    if raw.startswith("sub/"):
        return raw.split("/", 1)[1].strip()
    return raw


def _fmt_bytes(b: int) -> str:
    if b == 0:
        return "نامحدود"
    gb = b / 1024 ** 3
    return f"{gb:.2f} گیگ" if gb >= 1 else f"{b / 1024 ** 2:.1f} مگ"


def _fmt_ts(ts: int) -> str:
    if ts == 0:
        return "نامحدود"
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return dt.strftime("%Y/%m/%d")


def _xui():
    return XUIClient(
        panel_url=settings.panel_url,
        username=settings.panel_username,
        password=settings.panel_password,
        api_path=settings.panel_api_path,
        sub_port=settings.sub_port,
    )


# ──────────────────────────────────────────────
# ورود — دکمه «📥 افزودن اشتراک قدیمی»
# ──────────────────────────────────────────────

@router.message(F.text == "📥 افزودن اشتراک قدیمی")
async def msg_uuid_entry(message: Message, state: FSMContext) -> None:
    await state.set_state(UUIDImportStates.waiting_uuid)
    await message.answer(
        "📥 <b>افزودن اشتراک قدیمی</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "اگر قبلاً برای شما اشتراک ساخته شده، یکی از این‌ها را اینجا بفرستید:\n"
        "• UUID\n"
        "• لینک اشتراک\n"
        "• لینک <code>vless://</code> یا <code>vmess://</code>\n\n"
        "مثال UUID: <code>a1b2c3d4-1234-5678-abcd-ef0123456789</code>\n"
        "مثال لینک: <code>https://example.com/sub/abc123</code>\n\n"
        "برای لغو: /cancel",
        parse_mode="HTML",
    )


@router.message(UUIDImportStates.waiting_uuid, F.text == "/cancel")
async def uuid_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ لغو شد.", reply_markup=get_main_menu())


# ──────────────────────────────────────────────
# دریافت شناسه از کاربر → جستجو در پنل
# ──────────────────────────────────────────────

@router.message(UUIDImportStates.waiting_uuid)
async def msg_receive_uuid(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()

    # اگر کاربر لینک vless/vmess را paste کرده، UUID رو extract کن
    if raw.startswith("vless://") or raw.startswith("vmess://"):
        # vless://UUID@host:port?...
        parts = raw.split("://", 1)
        if len(parts) > 1:
            uuid_candidate = parts[1].split("@")[0].strip()
            if _is_valid_uuid(uuid_candidate):
                raw = uuid_candidate

    sub_id_candidate = _extract_sub_id(raw)
    if not _is_valid_uuid(raw) and not sub_id_candidate:
        await message.answer(
            "⚠️ فرمت UUID صحیح نیست.\n"
            "UUID باید ۳۶ کاراکتر با فرمت زیر باشد:\n"
            "<code>xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx</code>\n\n"
            "یا یک لینک اشتراک معتبر بفرستید.\n\n"
            "دوباره امتحان کنید یا /cancel بزنید.",
            parse_mode="HTML",
        )
        return

    uuid_str = raw.lower()
    await state.clear()

    wait_msg = await message.answer("⏳ در حال جستجو در پنل...")

    try:
        async with _xui() as xui:
            client = None
            if _is_valid_uuid(uuid_str):
                client = await xui.find_client_by_uuid(uuid_str)
            if not client and sub_id_candidate:
                all_clients = await xui.get_all_clients()
                for item in all_clients:
                    if item.sub_id == sub_id_candidate:
                        client = item
                        break

        if not client:
            await wait_msg.edit_text(
                "❌ <b>اشتراک پیدا نشد</b>\n\n"
                "این UUID یا لینک اشتراک در پنل وجود ندارد یا غیرفعال است.\n"
                "مطمئن شوید UUID یا لینک را درست وارد کرده‌اید.",
                parse_mode="HTML",
            )
            return

        # ── UUID پیدا شد ────────────────────────────────────────
        tg_user = message.from_user
        async with AsyncSessionLocal() as session:
            db_user, _ = await get_or_create_user(
                session, tg_user.id, tg_user.username, tg_user.first_name,
                admin_ids=settings.admin_ids,
            )

            # بررسی اینکه این اشتراک قبلاً ثبت شده یا نه
            existing = await get_subscription_by_email(session, client.email)
            already_linked = existing is not None

            # اگر قبلاً ثبت نشده، ذخیره کن
            if not already_linked:
                expiry_dt = None
                if client.expiry_time and client.expiry_time > 0:
                    expiry_dt = datetime.fromtimestamp(
                        client.expiry_time / 1000, tz=timezone.utc
                    )
                traffic_gb = client.total_gb // (1024 ** 3) if client.total_gb else 0
                sub = Subscription(
                    user_id=db_user.id,
                    email=client.email,
                    client_uuid=uuid_str,
                    plan_id=None,
                    sub_id=client.sub_id,
                    inbound_id=client.inbound_id,
                    traffic_limit_gb=traffic_gb,
                    used_traffic_bytes=client.up + client.down,
                    expiry_date=expiry_dt,
                    status="active" if client.enable else "disabled",
                )
                session.add(sub)
                await session.commit()
                logger.info(f"اشتراک با UUID ثبت شد: {client.email} → user {tg_user.id}")

        # دریافت لینک‌های اتصال
        async with _xui() as xui:
            links = await xui.get_sub_links(client.sub_id)
            if not links:
                links = await xui.get_client_links(client.email)
            sub_link = xui.build_sub_link(client.sub_id)

    except XUIError as e:
        await wait_msg.edit_text(f"❌ خطا در اتصال به پنل: <code>{e}</code>", parse_mode="HTML")
        return

    # ── نمایش نتیجه ──────────────────────────────────────────
    traffic_used = _fmt_bytes(client.up + client.down)
    traffic_total = _fmt_bytes(client.total_gb)
    expire_str = _fmt_ts(client.expiry_time)
    status_icon = "✅ فعال" if client.enable else "🚫 غیرفعال"
    link_note = "" if not already_linked else "\n\n♻️ این اشتراک قبلاً در ربات ثبت بود."

    info_text = (
        f"✅ <b>اشتراک پیدا شد!</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📧 شناسه: <code>{client.email}</code>\n"
        f"📦 ترافیک مصرفی: <code>{traffic_used}</code> از <code>{traffic_total}</code>\n"
        f"⏳ انقضا: <code>{expire_str}</code>\n"
        f"وضعیت: {status_icon}"
        f"{link_note}"
    )
    await wait_msg.edit_text(info_text, parse_mode="HTML")

    # لینک اشتراک
    sub_text = (
        f"🔗 <b>لینک اشتراک</b> (همه سرورها):\n"
        f"<code>{sub_link}</code>\n\n"
        f"📲 این لینک را در اپ‌های زیر وارد کنید:\n"
        f"• اندروید: هیدیفای، وی‌تو‌ری‌ان‌جی\n"
        f"• آیفون: استرایزند، شدوراکت\n"
        f"• ویندوز: هیدیفای، وی‌تو‌ری‌ان"
    )
    await message.answer(sub_text, parse_mode="HTML")

    # کانفیگ‌های تکی
    if links:
        await message.answer(
            f"📋 <b>کانفیگ‌های مستقل ({len(links)} سرور):</b>",
            parse_mode="HTML",
        )
        for i, link in enumerate(links, 1):
            proto = link.split("://")[0].upper() if "://" in link else "سرور"
            await message.answer(
                f"<b>سرور {i} — {proto}:</b>\n<code>{link}</code>",
                parse_mode="HTML",
            )

    saved_note = "✅ اشتراک در ربات ذخیره شد — در «اشتراک‌های من» قابل مشاهده است." \
        if not already_linked else "ℹ️ اشتراک از قبل در ربات موجود است."
    await message.answer(saved_note)
