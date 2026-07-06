"""
services/banner.py — مدیریت بنر ربات

بنر یک عکس است که در پیام‌های متنی (بدون عکس دیگر) ارسال می‌شود.
file_id عکس در AdminSetting ذخیره می‌شود تا نیازی به آپلود مجدد نباشد.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from loguru import logger

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

BANNER_KEY = "banner_file_id"


async def get_banner_file_id() -> Optional[str]:
    """دریافت file_id بنر از دیتابیس."""
    from database import AsyncSessionLocal
    from database.crud import get_setting
    async with AsyncSessionLocal() as session:
        val = await get_setting(session, BANNER_KEY, "")
    return val if val else None


async def set_banner_file_id(file_id: str) -> None:
    """ذخیره file_id بنر در دیتابیس."""
    from database import AsyncSessionLocal
    from database.crud import set_setting
    async with AsyncSessionLocal() as session:
        await set_setting(session, BANNER_KEY, file_id)


async def clear_banner() -> None:
    """حذف بنر."""
    from database import AsyncSessionLocal
    from database.crud import set_setting
    async with AsyncSessionLocal() as session:
        await set_setting(session, BANNER_KEY, "")


async def send_with_banner(
    message: "Message",
    text: str,
    parse_mode: str = "HTML",
    reply_markup=None,
) -> "Message":
    """
    ارسال پیام همراه با بنر.
    اگر بنر تنظیم شده باشد: عکس+کپشن ارسال می‌شود.
    اگر بنر نباشد: فقط متن ارسال می‌شود.

    این تابع فقط برای پیام‌هایی که عکس دیگری ندارند استفاده می‌شود.
    """
    file_id = await get_banner_file_id()
    if file_id:
        try:
            return await message.answer_photo(
                photo=file_id,
                caption=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.warning(f"ارسال بنر ناموفق (file_id احتمالاً منقضی): {e}")
            # اگر file_id منقضی شده، بنر رو پاک کن
            await clear_banner()

    # fallback: فقط متن
    return await message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)
