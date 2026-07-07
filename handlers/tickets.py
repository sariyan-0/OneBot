"""
handlers/tickets.py — هندلرهای سیستم تیکت پشتیبانی
FSM: TicketForm (subject → message)
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from loguru import logger

from config import settings
from database import AsyncSessionLocal
from database.crud import get_user_by_telegram_id, get_or_create_user, get_setting, set_setting
from keyboards.main_menu import get_main_menu


async def _is_admin(uid: int) -> bool:
    """بررسی ادمین بودن از هر دو منبع: .env و DB."""
    if settings.is_admin(uid):
        return True
    async with AsyncSessionLocal() as s:
        u = await get_user_by_telegram_id(s, uid)
        return bool(u and u.is_admin)
from keyboards.tickets import (
    get_admin_ticket_keyboard,
    get_cancel_keyboard,
    get_ticket_detail_keyboard,
    get_ticket_list_keyboard,
    get_ticket_mode_keyboard,
)
from services.tickets import (
    close_user_ticket,
    fetch_open_tickets_for_admin,
    fetch_ticket_detail,
    fetch_user_tickets,
    open_new_ticket,
    reopen_ticket,
    reply_to_ticket,
)

router = Router(name="tickets")
_ACTIVE_TICKET_SESSION_KEY = "active_ticket_session_ids"
_ACTIVE_TICKET_MAP_KEY = "active_ticket_session_map"
_TICKET_CONTROL_TEXTS = {"✍️ پاسخ", "🔒 بستن تیکت", "🔓 باز کردن مجدد", "🔓 باز کردن تیکت", "🚪 خروج از گفتگو"}
_MAIN_MENU_TEXTS = {
    "🛒 خرید کانفیگ",
    "🎁 اشتراک تست",
    "📊 اشتراک‌های من",
    "📥 افزودن اشتراک قدیمی",
    "👤 پروفایل",
    "👥 دعوت دوستان",
    "❓ پشتیبانی",
    "⚙️ پنل مدیریت",
}
_TICKET_MEDIA_LABELS = {
    "photo": "📷 [عکس]",
    "video": "🎥 [ویدیو]",
    "voice": "🎤 [ویس]",
    "audio": "🎵 [صدا]",
    "document": "📎 [فایل]",
    "sticker": "🙂 [استیکر]",
    "animation": "✨ [گیف]",
    "video_note": "🎬 [ویدیو نوت]",
}


def _parse_session_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.replace("\n", ",").split(","):
        item = part.strip()
        if item.isdigit():
            ids.add(int(item))
    return ids


def _parse_active_ticket_map(raw: str) -> dict[int, int]:
    result: dict[int, int] = {}
    for part in raw.replace("\n", ",").split(","):
        item = part.strip()
        if ":" not in item:
            continue
        user_id, ticket_id = item.split(":", 1)
        if user_id.isdigit() and ticket_id.isdigit():
            result[int(user_id)] = int(ticket_id)
    return result


async def _set_ticket_session_state(telegram_id: int, active: bool) -> None:
    async with AsyncSessionLocal() as session:
        raw = await get_setting(session, _ACTIVE_TICKET_SESSION_KEY, "")
        ids = _parse_session_ids(raw)
        if active:
            ids.add(telegram_id)
        else:
            ids.discard(telegram_id)
        await set_setting(session, _ACTIVE_TICKET_SESSION_KEY, ",".join(str(x) for x in sorted(ids)))


async def _ticket_session_active(telegram_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        raw = await get_setting(session, _ACTIVE_TICKET_SESSION_KEY, "")
    return telegram_id in _parse_session_ids(raw)


async def _set_active_ticket_id(telegram_id: int, ticket_id: int | None) -> None:
    async with AsyncSessionLocal() as session:
        raw = await get_setting(session, _ACTIVE_TICKET_MAP_KEY, "")
        mapping = _parse_active_ticket_map(raw)
        if ticket_id is None:
            mapping.pop(telegram_id, None)
        else:
            mapping[telegram_id] = ticket_id
        serialized = ",".join(f"{uid}:{tid}" for uid, tid in sorted(mapping.items()))
        await set_setting(session, _ACTIVE_TICKET_MAP_KEY, serialized)


async def _get_active_ticket_id(telegram_id: int) -> int | None:
    async with AsyncSessionLocal() as session:
        raw = await get_setting(session, _ACTIVE_TICKET_MAP_KEY, "")
    return _parse_active_ticket_map(raw).get(telegram_id)


# ──────────────────────────────────────────────
# FSM States
# ──────────────────────────────────────────────

class TicketForm(StatesGroup):
    waiting_subject = State()
    waiting_message = State()
    waiting_reply   = State()
    viewing_ticket = State()


class AdminTicketForm(StatesGroup):
    waiting_reply = State()


# ──────────────────────────────────────────────
# Helper: متن تیکت
# ──────────────────────────────────────────────

def _ticket_status_label(status: str) -> str:
    return {"open": "🔴 باز", "in_progress": "🟡 در حال بررسی", "closed": "✅ بسته"}.get(status, status)


def _format_ticket(ticket, show_messages: bool = False) -> str:
    lines = [
        f"🎫 *تیکت #{ticket.id}*",
        f"📌 موضوع: {ticket.subject}",
        f"📊 وضعیت: {_ticket_status_label(ticket.status)}",
        f"📅 تاریخ: {ticket.created_at.strftime('%Y-%m-%d %H:%M')} UTC",
    ]
    if show_messages and hasattr(ticket, "messages"):
        lines.append("\n─── پیام‌ها ───")
        for msg in ticket.messages:
            who = "👨‍💼 ادمین" if msg.is_admin_reply else "👤 کاربر"
            lines.append(f"\n{who} ({msg.created_at.strftime('%H:%M')}):\n{msg.body}")
    return "\n".join(lines)


def _compose_ticket_body(message: Message) -> str | None:
    text = (message.text or message.caption or "").strip()
    if text:
        return text

    content_type = str(getattr(message, "content_type", "") or "")
    label = _TICKET_MEDIA_LABELS.get(content_type)
    if label:
        return label
    if content_type:
        return f"📎 [{content_type}]"
    return None


# ──────────────────────────────────────────────
# پشتیبانی — منوی اصلی تیکت‌ها
# ──────────────────────────────────────────────

@router.message(F.text == "❓ پشتیبانی")
@router.callback_query(F.data == "support_list")
async def menu_support(event: Message | CallbackQuery) -> None:
    """نمایش لیست تیکت‌های کاربر یا دکمه ایجاد تیکت جدید."""
    if isinstance(event, CallbackQuery):
        await event.answer()
        tg_user = event.from_user
        send = event.message.answer  # type: ignore[union-attr]
    else:
        tg_user = event.from_user
        send = event.answer

    if not tg_user:
        return

    async with AsyncSessionLocal() as session:
        db_user = await get_user_by_telegram_id(session, tg_user.id)
        if not db_user:
            await send("❌ ابتدا /start بزنید.")
            return
        tickets = await fetch_user_tickets(session, db_user.id)

    if not tickets:
        text = (
            "❓ *پشتیبانی*\n\n"
            "هنوز تیکتی ندارید.\n"
            "برای ثبت تیکت جدید از دکمه زیر استفاده کنید:"
        )
    else:
        text = f"❓ *تیکت‌های شما* ({len(tickets)} تیکت):\n\nیک تیکت را انتخاب کنید:"

    await send(text, parse_mode="Markdown", reply_markup=get_ticket_list_keyboard(tickets))


# ──────────────────────────────────────────────
# FSM: ایجاد تیکت جدید
# ──────────────────────────────────────────────

@router.callback_query(F.data == "ticket_new")
async def cb_ticket_new(callback: CallbackQuery, state: FSMContext) -> None:
    """شروع فرآیند ایجاد تیکت — مرحله ۱: موضوع."""
    await callback.answer()
    await state.set_state(TicketForm.waiting_subject)
    await callback.message.answer(  # type: ignore[union-attr]
        "✏️ *تیکت جدید — مرحله ۱/۲*\n\nلطفاً *موضوع* تیکت را بنویسید:",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(StateFilter(TicketForm.waiting_subject))
async def fsm_ticket_subject(message: Message, state: FSMContext) -> None:
    """دریافت موضوع تیکت — مرحله ۲: پیام."""
    subject = message.text or ""
    if len(subject) < 5:
        await message.answer("⚠️ موضوع باید حداقل ۵ کاراکتر باشد.", reply_markup=get_cancel_keyboard())
        return
    if len(subject) > 200:
        await message.answer("⚠️ موضوع نباید بیشتر از ۲۰۰ کاراکتر باشد.", reply_markup=get_cancel_keyboard())
        return

    await state.update_data(subject=subject)
    await state.set_state(TicketForm.waiting_message)
    await message.answer(
        f"✅ موضوع: _{subject}_\n\n✏️ *مرحله ۲/۲*\n\nحالا *متن کامل* مشکل خود را بنویسید:",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(StateFilter(TicketForm.waiting_message))
async def fsm_ticket_message(message: Message, state: FSMContext) -> None:
    """دریافت متن تیکت — ذخیره و اطلاع‌رسانی به ادمین."""
    body = message.text or ""
    if len(body) < 10:
        await message.answer("⚠️ پیام باید حداقل ۱۰ کاراکتر باشد.", reply_markup=get_cancel_keyboard())
        return

    data = await state.get_data()
    subject = data.get("subject", "بدون موضوع")
    tg_user = message.from_user
    if not tg_user:
        await state.clear()
        return

    await state.clear()

    async with AsyncSessionLocal() as session:
        db_user, _ = await get_or_create_user(
            session=session,
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            admin_ids=settings.admin_ids,
        )
        ticket = await open_new_ticket(
            session=session,
            user_id=db_user.id,
            subject=subject,
            message=body,
        )

    await message.answer(
        f"✅ *تیکت #{ticket.id} با موفقیت ثبت شد!*\n\n"
        f"📌 موضوع: {subject}\n\n"
        "ادمین در اسرع وقت پاسخ خواهد داد.",
        parse_mode="Markdown",
    )

    # اطلاع‌رسانی به ادمین‌ها
    await _notify_admins_new_ticket(message.bot, ticket.id, tg_user, subject, body)


# ──────────────────────────────────────────────
# نمایش جزئیات تیکت
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("ticket_view:"))
async def cb_ticket_view(callback: CallbackQuery, state: FSMContext) -> None:
    """نمایش پیام‌های یک تیکت."""
    await callback.answer()
    ticket_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    tg_user = callback.from_user

    async with AsyncSessionLocal() as session:
        db_user = await get_user_by_telegram_id(session, tg_user.id)  # type: ignore[union-attr]
        if not db_user:
            return
        try:
            ticket = await fetch_ticket_detail(
                session, ticket_id, db_user.id, is_admin=db_user.is_admin
            )
        except (ValueError, PermissionError) as e:
            await callback.answer(str(e), show_alert=True)
            return

    text = _format_ticket(ticket, show_messages=True)
    await callback.message.answer(  # type: ignore[union-attr]
        text,
        parse_mode="Markdown",
        reply_markup=get_ticket_detail_keyboard(ticket_id, is_closed=(ticket.status == "closed")),
    )
    await state.set_state(TicketForm.viewing_ticket)
    await state.update_data(ticket_id=ticket_id, is_closed=(ticket.status == "closed"))
    if tg_user:
        await _set_ticket_session_state(tg_user.id, ticket.status != "closed")
        await _set_active_ticket_id(tg_user.id, ticket_id)
    await callback.message.answer(  # type: ignore[union-attr]
        "\u2063",
        reply_markup=get_ticket_mode_keyboard(is_closed=(ticket.status == "closed")),
    )


# ──────────────────────────────────────────────
# پاسخ کاربر به تیکت
# ──────────────────────────────────────────────

@router.message(StateFilter(TicketForm.viewing_ticket), F.text == "✍️ پاسخ")
async def fsm_ticket_reply_start(message: Message, state: FSMContext) -> None:
    """در حالت گفتگوی تیکت، دکمه پاسخ فقط کیبورد را نگه می‌دارد."""
    data = await state.get_data()
    is_closed = bool(data.get("is_closed"))
    await message.answer(
        "\u2063",
        reply_markup=get_ticket_mode_keyboard(is_closed=is_closed),
    )


@router.message(StateFilter(TicketForm.viewing_ticket), CommandStart())
async def fsm_ticket_start_over(message: Message, state: FSMContext) -> None:
    """/start داخل حالت تیکت، گفتگوی تیکت را می‌بندد و منوی اصلی را برمی‌گرداند."""
    await state.clear()
    if message.from_user:
        await _set_ticket_session_state(message.from_user.id, False)
        await _set_active_ticket_id(message.from_user.id, None)
        is_admin = await _is_admin(message.from_user.id)
    else:
        is_admin = False
    await message.answer(
        "🏠 منوی اصلی:",
        reply_markup=get_main_menu(is_admin=is_admin),
    )


@router.callback_query(F.data.startswith("ticket_reply:"))
async def cb_ticket_reply_start(callback: CallbackQuery, state: FSMContext) -> None:
    """سازگاری با پیام‌های قدیمی inline."""
    await callback.answer()
    ticket_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    await state.set_state(TicketForm.viewing_ticket)
    await state.update_data(ticket_id=ticket_id)
    if callback.from_user:
        await _set_ticket_session_state(callback.from_user.id, True)
        await _set_active_ticket_id(callback.from_user.id, ticket_id)
    await callback.message.answer(  # type: ignore[union-attr]
        "\u2063",
        reply_markup=get_ticket_mode_keyboard(is_closed=False),
    )


# ──────────────────────────────────────────────
# بستن تیکت توسط کاربر
# ──────────────────────────────────────────────

@router.message(StateFilter(TicketForm.viewing_ticket), F.text == "🔒 بستن تیکت")
async def fsm_ticket_close(message: Message, state: FSMContext) -> None:
    """بستن تیکت توسط کاربر از روی کیبورد پایین صفحه."""
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        await message.answer("❌ تیکت فعالی پیدا نشد.", reply_markup=get_main_menu())
        return
    async with AsyncSessionLocal() as session:
        await close_user_ticket(session, ticket_id)
    await state.clear()
    if message.from_user:
        await _set_ticket_session_state(message.from_user.id, False)
        await _set_active_ticket_id(message.from_user.id, None)
        is_admin = await _is_admin(message.from_user.id)
    else:
        is_admin = False
    await message.answer(
        f"✅ تیکت #{ticket_id} بسته شد.\n🏠 برگشتیم به منوی اصلی.",
        reply_markup=get_main_menu(is_admin=is_admin),
    )


@router.callback_query(F.data.startswith("ticket_close:"))
async def cb_ticket_close(callback: CallbackQuery) -> None:
    """سازگاری با پیام‌های قدیمی inline."""
    await callback.answer()
    ticket_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    async with AsyncSessionLocal() as session:
        await close_user_ticket(session, ticket_id)
    if callback.from_user:
        await _set_ticket_session_state(callback.from_user.id, False)
        await _set_active_ticket_id(callback.from_user.id, None)
        is_admin = await _is_admin(callback.from_user.id)
    else:
        is_admin = False
    await callback.message.answer(  # type: ignore[union-attr]
        f"✅ تیکت #{ticket_id} بسته شد.\n"
        "🏠 برگشتیم به منوی اصلی.",
        reply_markup=get_main_menu(is_admin=is_admin),
    )


# ──────────────────────────────────────────────
# بازگشایی تیکت بسته (کاربر)
# ──────────────────────────────────────────────

@router.message(StateFilter(TicketForm.viewing_ticket), F.text == "🔓 باز کردن مجدد")
@router.message(StateFilter(TicketForm.viewing_ticket), F.text == "🔓 باز کردن تیکت")
async def fsm_ticket_reopen(message: Message, state: FSMContext) -> None:
    """کاربر تیکت بسته را دوباره باز می‌کند."""
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        await message.answer("❌ تیکت فعالی پیدا نشد.", reply_markup=get_main_menu())
        return
    async with AsyncSessionLocal() as session:
        await reopen_ticket(session, ticket_id)
    await state.update_data(is_closed=False)
    if message.from_user:
        await _set_ticket_session_state(message.from_user.id, True)
        await _set_active_ticket_id(message.from_user.id, ticket_id)
    await message.answer("\u2063", reply_markup=get_ticket_mode_keyboard(is_closed=False))
    # اطلاع به ادمین
    tg_user = message.from_user
    if tg_user:
        for admin_id in settings.admin_ids:
            try:
                await message.bot.send_message(
                    admin_id,
                    f"🔓 *تیکت #{ticket_id} دوباره باز شد*\n"
                    f"👤 توسط: {tg_user.first_name or ''} `{tg_user.id}`",
                    parse_mode="Markdown",
                    reply_markup=get_admin_ticket_keyboard(ticket_id),
                )
            except Exception:
                pass


@router.callback_query(F.data.startswith("ticket_reopen:"))
async def cb_ticket_reopen(callback: CallbackQuery) -> None:
    """سازگاری با پیام‌های قدیمی inline."""
    await callback.answer()
    ticket_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    async with AsyncSessionLocal() as session:
        await reopen_ticket(session, ticket_id)
    if callback.from_user:
        await _set_ticket_session_state(callback.from_user.id, True)
        await _set_active_ticket_id(callback.from_user.id, ticket_id)
    await callback.message.answer(  # type: ignore[union-attr]
        "\u2063",
        reply_markup=get_ticket_mode_keyboard(is_closed=False),
    )


# ──────────────────────────────────────────────
# لغو FSM
# ──────────────────────────────────────────────

@router.callback_query(F.data == "ticket_cancel")
async def cb_ticket_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """لغو عملیات جاری FSM."""
    await state.clear()
    await callback.answer("❌ لغو شد.")
    if callback.from_user and await _ticket_session_active(callback.from_user.id):
        await _set_ticket_session_state(callback.from_user.id, False)
        await _set_active_ticket_id(callback.from_user.id, None)
        is_admin = await _is_admin(callback.from_user.id)
        await callback.message.answer(  # type: ignore[union-attr]
            "عملیات لغو شد. می‌توانید از منو ادامه دهید.",
            reply_markup=get_main_menu(is_admin=is_admin),
        )
        return
    await callback.message.answer("عملیات لغو شد. می‌توانید از منو ادامه دهید.")  # type: ignore[union-attr]


@router.message(StateFilter(TicketForm.viewing_ticket), F.text == "🚪 خروج از گفتگو")
async def fsm_ticket_exit(message: Message, state: FSMContext) -> None:
    """خروج از گفتگوی تیکت و برگشت به منوی اصلی."""
    await state.clear()
    if message.from_user:
        await _set_ticket_session_state(message.from_user.id, False)
        await _set_active_ticket_id(message.from_user.id, None)
    async with AsyncSessionLocal() as session:
        db_user = await get_user_by_telegram_id(session, message.from_user.id)  # type: ignore[union-attr]
    is_admin = db_user.is_admin if db_user else False
    await message.answer("🏠 منوی اصلی:", reply_markup=get_main_menu(is_admin=is_admin))


@router.callback_query(F.data == "ticket_exit")
async def cb_ticket_exit(callback: CallbackQuery, state: FSMContext) -> None:
    """سازگاری با خروج inline در پیام‌های قدیمی."""
    await callback.answer()
    await state.clear()
    if callback.from_user:
        await _set_ticket_session_state(callback.from_user.id, False)
        await _set_active_ticket_id(callback.from_user.id, None)
    async with AsyncSessionLocal() as session:
        db_user = await get_user_by_telegram_id(session, callback.from_user.id)  # type: ignore[union-attr]
    is_admin = db_user.is_admin if db_user else False
    await callback.message.answer(  # type: ignore[union-attr]
        "🏠 منوی اصلی:",
        reply_markup=get_main_menu(is_admin=is_admin),
    )


@router.message(StateFilter(TicketForm.viewing_ticket))
async def fsm_ticket_live_forward(message: Message, state: FSMContext) -> None:
    """هر پیام غیرکنترلی در حالت تیکت مستقیماً به همان تیکت ارسال می‌شود."""
    tg_user = message.from_user
    if not tg_user:
        return
    if not await _ticket_session_active(tg_user.id):
        return
    if message.text and message.text.startswith("/"):
        return
    if message.text in _MAIN_MENU_TEXTS:
        await state.clear()
        await _set_ticket_session_state(tg_user.id, False)
        await _set_active_ticket_id(tg_user.id, None)

        if message.text == "🛒 خرید کانفیگ":
            from handlers.shop import msg_buy
            await msg_buy(message)
            return
        if message.text == "🎁 اشتراک تست":
            from handlers.shop import msg_test_sub
            await msg_test_sub(message)
            return
        if message.text == "📊 اشتراک‌های من":
            from handlers.user import menu_my_subscriptions
            await menu_my_subscriptions(message)
            return
        if message.text == "📥 افزودن اشتراک قدیمی":
            from handlers.uuid_import import msg_uuid_entry
            await msg_uuid_entry(message, state)
            return
        if message.text == "👤 پروفایل":
            from handlers.user import menu_profile
            await menu_profile(message)
            return
        if message.text == "👥 دعوت دوستان":
            from handlers.referral import menu_referral
            await menu_referral(message)
            return
        if message.text == "❓ پشتیبانی":
            await menu_support(message)
            return
        if message.text == "⚙️ پنل مدیریت":
            from handlers.admin import msg_admin_panel
            await msg_admin_panel(message)
            return
    if message.text in _TICKET_CONTROL_TEXTS:
        return

    body = _compose_ticket_body(message)
    if not body:
        return

    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        ticket_id = await _get_active_ticket_id(tg_user.id)
        if not ticket_id:
            await message.answer("❌ تیکت فعالی پیدا نشد.", reply_markup=get_main_menu())
            return
        await state.update_data(ticket_id=ticket_id, is_closed=False)

    async with AsyncSessionLocal() as session:
        db_user = await get_user_by_telegram_id(session, tg_user.id)  # type: ignore[union-attr]
        if not db_user:
            return
        try:
            await reply_to_ticket(
                session,
                ticket_id,
                db_user.id,
                body,
                is_admin=False,
            )
        except ValueError as e:
            await message.answer(f"❌ {e}")
            return

    await _notify_admins_user_reply(message.bot, ticket_id, tg_user, body)


# ──────────────────────────────────────────────
# پاسخ ادمین به تیکت
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_ticket_reply:"))
async def cb_admin_reply_start(callback: CallbackQuery, state: FSMContext) -> None:
    """شروع FSM پاسخ ادمین."""
    if not await _is_admin(callback.from_user.id):  # type: ignore[union-attr]
        await callback.answer("🚫 دسترسی ندارید.", show_alert=True)
        return
    await callback.answer()
    ticket_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    await state.set_state(AdminTicketForm.waiting_reply)
    await state.update_data(ticket_id=ticket_id)
    await callback.message.answer(  # type: ignore[union-attr]
        f"✍️ *پاسخ ادمین به تیکت #{ticket_id}*\n\nپیام پاسخ را بنویسید:",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(StateFilter(AdminTicketForm.waiting_reply))
async def fsm_admin_reply(message: Message, state: FSMContext) -> None:
    """ذخیره پاسخ ادمین و اطلاع‌رسانی به کاربر."""
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    tg_user = message.from_user
    await state.clear()

    async with AsyncSessionLocal() as session:
        db_user = await get_user_by_telegram_id(session, tg_user.id)  # type: ignore[union-attr]
        if not db_user:
            return
        try:
            await reply_to_ticket(
                session, ticket_id, db_user.id, message.text or "", is_admin=True
            )
            ticket = await fetch_ticket_detail(session, ticket_id, db_user.id, is_admin=True)
        except ValueError as e:
            await message.answer(f"❌ {e}")
            return

    await message.answer(f"✅ پاسخ به تیکت #{ticket_id} ارسال شد.")
    # اطلاع‌رسانی به کاربر صاحب تیکت
    await _notify_user_admin_replied(message.bot, ticket, message.text or "")


@router.callback_query(F.data.startswith("admin_ticket_view:"))
async def cb_admin_ticket_view(callback: CallbackQuery) -> None:
    """ادمین جزئیات کامل تیکت + تاریخچه پیام‌ها را می‌بیند."""
    if not await _is_admin(callback.from_user.id):  # type: ignore[union-attr]
        await callback.answer("🚫 دسترسی ندارید.", show_alert=True)
        return
    await callback.answer()
    ticket_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    async with AsyncSessionLocal() as session:
        try:
            ticket = await fetch_ticket_detail(session, ticket_id, 0, is_admin=True)
        except ValueError as e:
            await callback.answer(str(e), show_alert=True)
            return
        # دریافت اطلاعات کاربر
        from database.crud import get_user_by_telegram_id
        from database.models import User
        from sqlalchemy import select
        result = await session.execute(select(User).where(User.id == ticket.user_id))
        owner = result.scalar_one_or_none()

    # اطلاعات کاربر
    if owner:
        uname = f"@{owner.username}" if owner.username else "بدون یوزرنیم"
        user_line = f"👤 {owner.first_name or ''} {uname} — `{owner.telegram_id}`"
    else:
        user_line = "👤 کاربر نامشخص"

    status_label = {"open": "🔴 باز", "in_progress": "🟡 در بررسی", "closed": "✅ بسته"}.get(
        ticket.status, ticket.status
    )
    lines = [
        f"🎫 *تیکت #{ticket.id}*",
        f"{user_line}",
        f"📌 موضوع: *{ticket.subject}*",
        f"📊 وضعیت: {status_label}",
        f"📅 ایجاد: {ticket.created_at.strftime('%Y-%m-%d %H:%M')} UTC",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    if hasattr(ticket, "messages") and ticket.messages:
        for msg in ticket.messages:
            who = "👨‍💼 ادمین" if msg.is_admin_reply else "👤 کاربر"
            time_str = msg.created_at.strftime("%m-%d %H:%M")
            lines.append(f"\n{who} [{time_str}]:\n{msg.body}")

    text = "\n".join(lines)
    # تقسیم اگر طولانی بود
    if len(text) > 4000:
        text = text[:3900] + "\n\n... (ادامه)"

    is_closed = ticket.status == "closed"
    await callback.message.answer(
        text, parse_mode="Markdown",
        reply_markup=get_admin_ticket_keyboard(ticket_id, is_closed=is_closed),
    )


@router.callback_query(F.data.startswith("admin_ticket_reopen:"))
async def cb_admin_ticket_reopen(callback: CallbackQuery) -> None:
    """ادمین تیکت بسته را دوباره باز می‌کند."""
    if not await _is_admin(callback.from_user.id):  # type: ignore[union-attr]
        await callback.answer("🚫 دسترسی ندارید.", show_alert=True)
        return
    await callback.answer()
    ticket_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    async with AsyncSessionLocal() as session:
        await reopen_ticket(session, ticket_id)
        ticket = await fetch_ticket_detail(session, ticket_id, 0, is_admin=True)
        # اطلاع به کاربر
        await _notify_user_ticket_reopened(callback.bot, ticket)  # type: ignore[union-attr]
    await callback.message.answer(
        f"🔓 تیکت #{ticket_id} دوباره باز شد.",
        reply_markup=get_admin_ticket_keyboard(ticket_id, is_closed=False),
    )


@router.callback_query(F.data.startswith("admin_ticket_close:"))
async def cb_admin_ticket_close(callback: CallbackQuery) -> None:
    """بستن تیکت توسط ادمین."""
    if not await _is_admin(callback.from_user.id):  # type: ignore[union-attr]
        await callback.answer("🚫 دسترسی ندارید.", show_alert=True)
        return
    await callback.answer()
    ticket_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    async with AsyncSessionLocal() as session:
        await close_user_ticket(session, ticket_id)
        ticket = await fetch_ticket_detail(session, ticket_id, 0, is_admin=True)
        await _notify_user_ticket_closed(callback.bot, ticket)  # type: ignore[union-attr]
    await callback.message.answer(  # type: ignore[union-attr]
        f"✅ تیکت #{ticket_id} توسط ادمین بسته شد."
    )


# ──────────────────────────────────────────────
# نوتیفیکیشن‌های داخلی
# ──────────────────────────────────────────────

async def _notify_admins_new_ticket(bot, ticket_id: int, tg_user, subject: str, body: str) -> None:
    """
    اطلاع‌رسانی کامل به ادمین‌ها هنگام تیکت جدید.
    شامل: اطلاعات کاربر، وضعیت اشتراک، موضوع و متن پیام.
    """
    # دریافت وضعیت اشتراک کاربر
    sub_info = "فاقد اشتراک فعال"
    try:
        async with AsyncSessionLocal() as session:
            from database.crud import get_user_by_telegram_id, get_user_subscriptions
            db_user = await get_user_by_telegram_id(session, tg_user.id)
            if db_user:
                subs = await get_user_subscriptions(session, db_user.id, active_only=True)
                if subs:
                    sub_info = f"{len(subs)} اشتراک فعال"
    except Exception:
        pass

    name = tg_user.first_name or ""
    uname = f"@{tg_user.username}" if tg_user.username else "بدون یوزرنیم"
    text = (
        f"🆕 *تیکت جدید #{ticket_id}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 کاربر: {name} {uname}\n"
        f"🆔 آیدی: `{tg_user.id}`\n"
        f"📦 اشتراک: {sub_info}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 موضوع: *{subject}*\n\n"
        f"💬 پیام کاربر:\n{body[:500]}"
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                admin_id, text, parse_mode="Markdown",
                reply_markup=get_admin_ticket_keyboard(ticket_id),
            )
        except Exception as e:
            logger.warning(f"ارسال نوتیف به ادمین {admin_id} ناموفق: {e}")


async def _notify_admins_user_reply(bot, ticket_id: int, tg_user, reply_text: str = "") -> None:
    """اطلاع‌رسانی هنگام پاسخ کاربر — شامل متن پاسخ."""
    name = tg_user.first_name or ""
    uname = f"@{tg_user.username}" if tg_user.username else ""
    text = (
        f"💬 *پاسخ جدید در تیکت #{ticket_id}*\n"
        f"👤 {name} {uname} — `{tg_user.id}`\n\n"
        f"📝 پیام:\n{reply_text[:400]}"
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                admin_id, text, parse_mode="Markdown",
                reply_markup=get_admin_ticket_keyboard(ticket_id),
            )
        except Exception as e:
            logger.warning(f"ارسال نوتیف به ادمین {admin_id} ناموفق: {e}")


async def _notify_user_admin_replied(bot, ticket, reply_text: str) -> None:
    """اطلاع‌رسانی به کاربر هنگام پاسخ ادمین."""
    from database.models import User
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        # از User مستقیم استفاده می‌کنیم — ticket ممکن است detached باشد
        result = await session.execute(select(User).where(User.id == ticket.user_id))
        user = result.scalar_one_or_none()
        if not user:
            return
        telegram_id = user.telegram_id

    last_user_message = ""
    if hasattr(ticket, "messages") and ticket.messages:
        for msg in reversed(ticket.messages):
            if not msg.is_admin_reply:
                last_user_message = msg.body
                break

    text = (
        f"📨 *پاسخ پشتیبانی برای تیکت #{ticket.id}*\n\n"
        f"📌 موضوع: {ticket.subject}\n\n"
        + (f"👤 پیام شما:\n{last_user_message[:400]}\n\n" if last_user_message else "")
        + f"💬 پاسخ ادمین:\n{reply_text[:500]}"
    )
    try:
        await _set_ticket_session_state(telegram_id, True)
        await _set_active_ticket_id(telegram_id, ticket.id)
        kwargs = {
            "parse_mode": "Markdown",
            "reply_markup": get_ticket_mode_keyboard(is_closed=False),
        }
        await bot.send_message(telegram_id, text, **kwargs)
    except Exception as e:
        logger.warning(f"ارسال نوتیف به کاربر {telegram_id} ناموفق: {e}")


async def _get_ticket_owner_tgid(ticket) -> int | None:
    """دریافت telegram_id مالک تیکت."""
    async with AsyncSessionLocal() as session:
        from database.models import User
        from sqlalchemy import select
        result = await session.execute(select(User).where(User.id == ticket.user_id))
        user = result.scalar_one_or_none()
        return user.telegram_id if user else None


async def _notify_user_ticket_closed(bot, ticket) -> None:
    """اطلاع‌رسانی به کاربر هنگام بسته شدن تیکت."""
    tg_id = await _get_ticket_owner_tgid(ticket)
    if not tg_id:
        return
    try:
        await _set_ticket_session_state(tg_id, False)
        await _set_active_ticket_id(tg_id, None)
        is_admin = await _is_admin(tg_id)
        await bot.send_message(
            tg_id,
            f"✅ *تیکت #{ticket.id} بسته شد*\n"
            f"📌 موضوع: {ticket.subject}\n\n"
            "گفتگو بسته شد و منوی اصلی برگردانده شد.",
            parse_mode="Markdown",
            reply_markup=get_main_menu(is_admin=is_admin),
        )
    except Exception as e:
        logger.warning(f"ارسال نوتیف بستن تیکت ناموفق: {e}")


async def _notify_user_ticket_reopened(bot, ticket) -> None:
    """اطلاع‌رسانی به کاربر هنگام بازگشایی تیکت توسط ادمین."""
    tg_id = await _get_ticket_owner_tgid(ticket)
    if not tg_id:
        return
    try:
        await _set_ticket_session_state(tg_id, True)
        await bot.send_message(
            tg_id,
            f"🔓 *تیکت #{ticket.id} توسط ادمین دوباره باز شد*\n"
            f"📌 موضوع: {ticket.subject}\n\n"
            "می‌توانید ادامه مکالمه را دنبال کنید.",
            parse_mode="Markdown",
            reply_markup=get_ticket_mode_keyboard(is_closed=False),
        )
    except Exception as e:
        logger.warning(f"ارسال نوتیف بازگشایی تیکت ناموفق: {e}")
