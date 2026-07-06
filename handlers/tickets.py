"""
handlers/tickets.py — هندلرهای سیستم تیکت پشتیبانی
FSM: TicketForm (subject → message)
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from loguru import logger

from config import settings
from database import AsyncSessionLocal
from database.crud import get_user_by_telegram_id, get_or_create_user


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


# ──────────────────────────────────────────────
# FSM States
# ──────────────────────────────────────────────

class TicketForm(StatesGroup):
    waiting_subject = State()
    waiting_message = State()
    waiting_reply   = State()


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
            "هیچ تیکتی ندارید.\n"
            "روی دکمه زیر کلیک کنید تا تیکت جدید باز کنید:"
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
async def cb_ticket_view(callback: CallbackQuery) -> None:
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


# ──────────────────────────────────────────────
# پاسخ کاربر به تیکت
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("ticket_reply:"))
async def cb_ticket_reply_start(callback: CallbackQuery, state: FSMContext) -> None:
    """شروع FSM پاسخ کاربر."""
    await callback.answer()
    ticket_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    await state.set_state(TicketForm.waiting_reply)
    await state.update_data(ticket_id=ticket_id)
    await callback.message.answer(  # type: ignore[union-attr]
        f"✍️ *پاسخ به تیکت #{ticket_id}*\n\nپیام خود را بنویسید:",
        parse_mode="Markdown",
        reply_markup=get_cancel_keyboard(),
    )


@router.message(StateFilter(TicketForm.waiting_reply))
async def fsm_user_reply(message: Message, state: FSMContext) -> None:
    """ذخیره پاسخ کاربر."""
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
                session, ticket_id, db_user.id, message.text or "", is_admin=False
            )
        except ValueError as e:
            await message.answer(f"❌ {e}")
            return

    await message.answer(f"✅ پاسخ شما به تیکت #{ticket_id} ارسال شد.")
    await _notify_admins_user_reply(message.bot, ticket_id, tg_user, message.text or "")


# ──────────────────────────────────────────────
# بستن تیکت توسط کاربر
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("ticket_close:"))
async def cb_ticket_close(callback: CallbackQuery) -> None:
    """بستن تیکت توسط کاربر."""
    await callback.answer()
    ticket_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    async with AsyncSessionLocal() as session:
        await close_user_ticket(session, ticket_id)
    await callback.message.answer(  # type: ignore[union-attr]
        f"✅ تیکت #{ticket_id} بسته شد.\n"
        "اگر مشکل مجدداً پیش آمد می‌توانید تیکت را دوباره باز کنید.",
        reply_markup=get_ticket_detail_keyboard(ticket_id, is_closed=True),
    )


# ──────────────────────────────────────────────
# بازگشایی تیکت بسته (کاربر)
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("ticket_reopen:"))
async def cb_ticket_reopen(callback: CallbackQuery) -> None:
    """کاربر تیکت بسته را دوباره باز می‌کند."""
    await callback.answer()
    ticket_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]
    async with AsyncSessionLocal() as session:
        await reopen_ticket(session, ticket_id)
    await callback.message.answer(  # type: ignore[union-attr]
        f"🔓 تیکت #{ticket_id} دوباره باز شد.\n"
        "می‌توانید پیام جدید ارسال کنید:",
        reply_markup=get_ticket_detail_keyboard(ticket_id, is_closed=False),
    )
    # اطلاع به ادمین
    tg_user = callback.from_user
    if tg_user:
        for admin_id in settings.admin_ids:
            try:
                await callback.bot.send_message(  # type: ignore[union-attr]
                    admin_id,
                    f"🔓 *تیکت #{ticket_id} دوباره باز شد*\n"
                    f"👤 توسط: {tg_user.first_name or ''} `{tg_user.id}`",
                    parse_mode="Markdown",
                    reply_markup=get_admin_ticket_keyboard(ticket_id),
                )
            except Exception:
                pass


# ──────────────────────────────────────────────
# لغو FSM
# ──────────────────────────────────────────────

@router.callback_query(F.data == "ticket_cancel")
async def cb_ticket_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """لغو عملیات جاری FSM."""
    await state.clear()
    await callback.answer("❌ لغو شد.")
    await callback.message.answer("عملیات لغو شد. می‌توانید از منو ادامه دهید.")  # type: ignore[union-attr]


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

    text = (
        f"📨 *پاسخ پشتیبانی برای تیکت #{ticket.id}*\n\n"
        f"📌 موضوع: {ticket.subject}\n\n"
        f"💬 پاسخ ادمین:\n{reply_text[:500]}"
    )
    try:
        await bot.send_message(
            telegram_id, text, parse_mode="Markdown",
            reply_markup=get_ticket_detail_keyboard(ticket.id),
        )
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
        await bot.send_message(
            tg_id,
            f"✅ *تیکت #{ticket.id} بسته شد*\n"
            f"📌 موضوع: {ticket.subject}\n\n"
            "اگر مشکل حل نشده یا سوال جدیدی دارید، می‌توانید تیکت را دوباره باز کنید.",
            parse_mode="Markdown",
            reply_markup=get_ticket_detail_keyboard(ticket.id, is_closed=True),
        )
    except Exception as e:
        logger.warning(f"ارسال نوتیف بستن تیکت ناموفق: {e}")


async def _notify_user_ticket_reopened(bot, ticket) -> None:
    """اطلاع‌رسانی به کاربر هنگام بازگشایی تیکت توسط ادمین."""
    tg_id = await _get_ticket_owner_tgid(ticket)
    if not tg_id:
        return
    try:
        await bot.send_message(
            tg_id,
            f"🔓 *تیکت #{ticket.id} توسط ادمین دوباره باز شد*\n"
            f"📌 موضوع: {ticket.subject}\n\n"
            "می‌توانید ادامه مکالمه را دنبال کنید.",
            parse_mode="Markdown",
            reply_markup=get_ticket_detail_keyboard(ticket.id, is_closed=False),
        )
    except Exception as e:
        logger.warning(f"ارسال نوتیف بازگشایی تیکت ناموفق: {e}")
