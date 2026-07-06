"""
handlers/broadcast.py — پیام دسته‌جمعی به تمام کاربران

Flow:
  ادمین: دکمه «📢 پیام دسته‌جمعی» → ارسال متن (با عکس اختیاری)
  → پیش‌نمایش + تأیید → ارسال به همه کاربران + گزارش نتیجه
"""

from __future__ import annotations

import asyncio
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    Message,
    PhotoSize,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger
from sqlalchemy import select

from database import AsyncSessionLocal
from database.models import User
from handlers.admin import _check_admin

router = Router(name="broadcast")


# ──────────────────────────────────────────────
# FSM States
# ──────────────────────────────────────────────

class BroadcastStates(StatesGroup):
    waiting_message = State()   # انتظار برای دریافت متن یا عکس+کپشن
    confirm         = State()   # تأیید قبل از ارسال


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────

async def _get_all_user_ids(session) -> list[int]:
    """دریافت telegram_id همه کاربران."""
    result = await session.execute(select(User.telegram_id))
    return [row[0] for row in result.all()]


async def _do_broadcast(
    bot: Bot,
    user_ids: list[int],
    text: str,
    photo_file_id: Optional[str] = None,
) -> tuple[int, int]:
    """
    ارسال پیام به همه کاربران.
    Returns: (sent_count, failed_count)
    """
    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            if photo_file_id:
                await bot.send_photo(
                    chat_id=uid,
                    photo=photo_file_id,
                    caption=text,
                    parse_mode="HTML",
                )
            else:
                await bot.send_message(
                    chat_id=uid,
                    text=text,
                    parse_mode="HTML",
                )
            sent += 1
        except Exception as e:
            logger.debug(f"broadcast به {uid} ناموفق: {e}")
            failed += 1
        # throttle: ۳۰ پیام در ثانیه حد تلگرام
        await asyncio.sleep(0.04)
    return sent, failed


# ──────────────────────────────────────────────
# شروع — دکمه «📢 پیام دسته‌جمعی»
# ──────────────────────────────────────────────

@router.callback_query(F.data == "adm_broadcast")
async def cb_adm_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _check_admin(callback):
        return
    await callback.answer()
    await state.set_state(BroadcastStates.waiting_message)
    await callback.message.answer(
        "📢 <b>پیام دسته‌جمعی</b>\n\n"
        "متن پیام خود را بنویسید.\n"
        "• می‌توانید <b>عکس</b> هم بفرستید (کپشن = متن پیام)\n"
        "• از تگ‌های HTML استفاده کنید: <code>&lt;b&gt;، &lt;i&gt;، &lt;code&gt;</code>\n\n"
        "برای لغو: /cancel",
        parse_mode="HTML",
    )


@router.message(BroadcastStates.waiting_message, F.text == "/cancel")
async def bc_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ لغو شد.")


@router.message(BroadcastStates.waiting_message)
async def bc_receive_message(message: Message, state: FSMContext) -> None:
    """دریافت متن یا عکس+کپشن از ادمین."""
    if not await _check_admin(message):
        await state.clear()
        return

    # تشخیص نوع پیام
    photo_file_id: Optional[str] = None
    text: str = ""

    if message.photo:
        # بهترین کیفیت عکس
        best: PhotoSize = message.photo[-1]
        photo_file_id = best.file_id
        text = message.caption or ""
    elif message.text:
        text = message.text
    else:
        await message.answer("⚠️ فقط متن یا عکس (با یا بدون کپشن) پشتیبانی می‌شود.")
        return

    if not text and not photo_file_id:
        await message.answer("⚠️ پیام نمی‌تواند خالی باشد.")
        return

    # ذخیره در FSM state
    await state.update_data(text=text, photo_file_id=photo_file_id)
    await state.set_state(BroadcastStates.confirm)

    # تعداد کاربران
    async with AsyncSessionLocal() as session:
        user_ids = await _get_all_user_ids(session)
    total = len(user_ids)

    # پیش‌نمایش
    preview_text = (
        f"👁 <b>پیش‌نمایش پیام:</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{text if text else '(بدون متن)'}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{'🖼 همراه با عکس' if photo_file_id else '📝 فقط متن'}\n\n"
        f"👥 ارسال به: <b>{total} کاربر</b>\n\n"
        "آیا ارسال کنم؟"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text=f"✅ ارسال به {total} کاربر", callback_data="bc_confirm")
    builder.button(text="✏️ ویرایش", callback_data="bc_edit")
    builder.button(text="❌ لغو", callback_data="bc_cancel_cb")
    builder.adjust(1)

    if photo_file_id:
        await message.answer_photo(
            photo=photo_file_id,
            caption=preview_text,
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
    else:
        await message.answer(preview_text, parse_mode="HTML", reply_markup=builder.as_markup())


# ──────────────────────────────────────────────
# تأیید / لغو / ویرایش
# ──────────────────────────────────────────────

@router.callback_query(BroadcastStates.confirm, F.data == "bc_cancel_cb")
async def bc_cancel_cb(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("لغو شد.")
    await callback.message.edit_caption("❌ ارسال دسته‌جمعی لغو شد.") if callback.message.photo else \
        await callback.message.edit_text("❌ ارسال دسته‌جمعی لغو شد.")


@router.callback_query(BroadcastStates.confirm, F.data == "bc_edit")
async def bc_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BroadcastStates.waiting_message)
    await callback.answer()
    await callback.message.answer(
        "✏️ پیام جدید را بفرستید (متن یا عکس+کپشن):\n\nبرای لغو: /cancel"
    )


@router.callback_query(BroadcastStates.confirm, F.data == "bc_confirm")
async def bc_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _check_admin(callback):
        await state.clear()
        return

    data = await state.get_data()
    text: str = data.get("text", "")
    photo_file_id: Optional[str] = data.get("photo_file_id")
    await state.clear()

    await callback.answer("🚀 در حال ارسال...")

    # نمایش پیام "در حال ارسال"
    progress_msg = await callback.message.answer(
        "⏳ <b>در حال ارسال...</b>\n"
        "لطفاً صبر کنید.",
        parse_mode="HTML",
    )

    # دریافت لیست کاربران
    async with AsyncSessionLocal() as session:
        user_ids = await _get_all_user_ids(session)

    bot = callback.bot
    sent, failed = await _do_broadcast(bot, user_ids, text, photo_file_id)

    # گزارش نتیجه
    report = (
        f"📊 <b>گزارش ارسال دسته‌جمعی</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"✅ ارسال موفق: <b>{sent}</b>\n"
        f"❌ ناموفق (بلاک/حذف): <b>{failed}</b>\n"
        f"👥 کل کاربران: <b>{len(user_ids)}</b>"
    )
    await progress_msg.edit_text(report, parse_mode="HTML")
    logger.success(f"broadcast تمام شد: {sent} موفق / {failed} ناموفق از {len(user_ids)}")
