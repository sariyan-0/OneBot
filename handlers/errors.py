"""
handlers/errors.py — Global error handler برای ربات
تمام خطاهای unhandled را لاگ می‌کند و پیام مناسب به کاربر ارسال می‌کند.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import ExceptionTypeFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import ErrorEvent
from loguru import logger

router = Router(name="errors")


@router.errors()
async def global_error_handler(event: ErrorEvent) -> None:
    """
    هندلر سراسری خطا — همه exceptions unhandled را می‌گیرد.
    لاگ کامل می‌کند و پیام دوستانه به کاربر می‌دهد.
    """
    exc = event.exception
    update = event.update

    # لاگ کامل با stack trace
    logger.exception(
        f"خطای unhandled در update_id={update.update_id}: {type(exc).__name__}: {exc}"
    )

    # پیدا کردن chat برای ارسال پیام خطا
    message = None
    if update.message:
        message = update.message
    elif update.callback_query and update.callback_query.message:
        message = update.callback_query.message

    if message:
        try:
            await message.answer(
                "⚠️ خطای غیرمنتظره‌ای رخ داد.\n\n"
                "لطفاً دوباره تلاش کنید یا با پشتیبانی تماس بگیرید.",
            )
        except Exception:
            pass  # اگر ارسال پیام هم ناموفق بود، ignore کن


@router.errors(ExceptionTypeFilter(PermissionError))
async def permission_error_handler(event: ErrorEvent) -> None:
    """هندلر اختصاصی برای خطاهای دسترسی."""
    logger.warning(f"PermissionError: {event.exception}")
    update = event.update
    if update.callback_query:
        try:
            await update.callback_query.answer("🚫 دسترسی ندارید.", show_alert=True)
        except Exception:
            pass


@router.errors(ExceptionTypeFilter(ValueError))
async def value_error_handler(event: ErrorEvent) -> None:
    """هندلر اختصاصی برای ValueError — معمولاً خطاهای منطقی."""
    logger.warning(f"ValueError: {event.exception}")
    update = event.update
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg:
        try:
            await msg.answer(f"❌ {event.exception}")
        except Exception:
            pass
