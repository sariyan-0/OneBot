"""
services/welcome.py — مدیریت بنر خوش‌آمدگویی و تنظیمات عضویت کانال

کلیدهای AdminSetting:
  welcome_banner_file_id  — file_id عکس بنر خوش‌آمدگویی
  welcome_banner_caption  — کپشن بنر خوش‌آمدگویی
  join_channel_id         — آی‌دی کانال (مثل @mychannel یا -100123456)
  join_channel_link       — لینک دعوت کانال (برای دکمه «عضویت»)
  join_channel_title      — نام نمایشی کانال
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import Message

# ── کلیدهای DB ────────────────────────────────
WELCOME_BANNER_KEY    = "welcome_banner_file_id"
WELCOME_CAPTION_KEY   = "welcome_banner_caption"
JOIN_CHANNEL_ID_KEY   = "join_channel_id"
JOIN_CHANNEL_LINK_KEY = "join_channel_link"
JOIN_CHANNEL_TITLE_KEY = "join_channel_title"

DEFAULT_CAPTION = (
    "👋 به ربات خوش آمدید!\n\n"
    "برای شروع دکمه <b>Start</b> را بزنید."
)


# ── helpers دیتابیس ───────────────────────────

async def _get(key: str, default: str = "") -> str:
    from database import AsyncSessionLocal
    from database.crud import get_setting
    async with AsyncSessionLocal() as s:
        return await get_setting(s, key, default)


async def _set(key: str, value: str) -> None:
    from database import AsyncSessionLocal
    from database.crud import set_setting
    async with AsyncSessionLocal() as s:
        await set_setting(s, key, value)


# ── Welcome Banner ────────────────────────────

async def get_welcome_banner_file_id() -> Optional[str]:
    v = await _get(WELCOME_BANNER_KEY)
    return v if v else None


async def set_welcome_banner_file_id(file_id: str) -> None:
    await _set(WELCOME_BANNER_KEY, file_id)


async def clear_welcome_banner() -> None:
    await _set(WELCOME_BANNER_KEY, "")


async def get_welcome_caption() -> str:
    v = await _get(WELCOME_CAPTION_KEY)
    return v if v else DEFAULT_CAPTION


async def set_welcome_caption(caption: str) -> None:
    await _set(WELCOME_CAPTION_KEY, caption)


async def send_welcome_banner(message: "Message") -> bool:
    """
    ارسال بنر خوش‌آمدگویی به کاربر.
    اگر بنر تنظیم نشده باشد False برمی‌گرداند (کاربر باید flow عادی ببیند).
    اگر ارسال شد True برمی‌گرداند.
    """
    from aiogram.types import InlineKeyboardMarkup
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    file_id = await get_welcome_banner_file_id()
    caption = await get_welcome_caption()

    # دکمه Start
    b = InlineKeyboardBuilder()
    b.button(text="🚀 شروع کن", callback_data="welcome_start")
    kb = b.as_markup()

    if file_id:
        try:
            await message.answer_photo(
                photo=file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )
            return True
        except Exception as e:
            logger.warning(f"ارسال welcome banner ناموفق: {e}")
            await clear_welcome_banner()

    # اگر بنر نبود یا خطا داد، فقط متن
    try:
        await message.answer(caption, parse_mode="HTML", reply_markup=kb)
        return True
    except Exception:
        return False


# ── Join Channel ──────────────────────────────

async def get_join_channel_id() -> Optional[str]:
    """آی‌دی کانال برای چک عضویت (@username یا -100xxx)"""
    v = await _get(JOIN_CHANNEL_ID_KEY)
    return v.strip() if v.strip() else None


async def get_join_channel_link() -> Optional[str]:
    """لینک دعوت کانال برای دکمه"""
    v = await _get(JOIN_CHANNEL_LINK_KEY)
    return v.strip() if v.strip() else None


async def get_join_channel_title() -> str:
    v = await _get(JOIN_CHANNEL_TITLE_KEY, "کانال ما")
    return v if v else "کانال ما"


async def set_join_channel(
    channel_id: str,
    link: str,
    title: str = "",
) -> None:
    await _set(JOIN_CHANNEL_ID_KEY, channel_id.strip())
    await _set(JOIN_CHANNEL_LINK_KEY, link.strip())
    await _set(JOIN_CHANNEL_TITLE_KEY, title.strip())


async def clear_join_channel() -> None:
    await _set(JOIN_CHANNEL_ID_KEY, "")
    await _set(JOIN_CHANNEL_LINK_KEY, "")
    await _set(JOIN_CHANNEL_TITLE_KEY, "")


async def check_user_joined(bot: "Bot", user_id: int) -> bool:
    """
    بررسی عضویت کاربر در کانال.
    اگر کانال تنظیم نشده باشد True برمی‌گرداند (بدون محدودیت).

    شرط کار کردن:
      - ربات باید عضو (یا ادمین) کانال باشد
      - کانال باید public باشد یا ربات ادمین آن باشد
    """
    channel_id = await get_join_channel_id()
    if not channel_id:
        return True
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status not in ("left", "kicked", "banned")
    except Exception as e:
        err = str(e).lower()
        # کانال خصوصی است و ربات ادمین نیست — به ادمین‌ها هشدار بده
        if "member list is inaccessible" in err or "chat not found" in err:
            logger.error(
                f"⚠️ کانال اجباری '{channel_id}' قابل دسترس نیست!\n"
                f"   دلیل: {e}\n"
                f"   راه‌حل: ربات را ادمین کانال کنید یا channel_id را اصلاح کنید.\n"
                f"   تا رفع مشکل، چک عضویت غیرفعال است."
            )
        else:
            logger.warning(f"خطا در چک عضویت کانال {channel_id}: {e}")
        # اگر چک ممکن نبود کاربر را رد نمی‌کنیم
        return True


async def send_join_required_message(message: "Message") -> None:
    """ارسال پیام الزام عضویت با دکمه عضویت."""
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    title = await get_join_channel_title()
    link  = await get_join_channel_link()

    b = InlineKeyboardBuilder()
    if link:
        b.button(text=f"📢 عضویت در {title}", url=link)
    b.button(text="✅ عضو شدم، بررسی کن", callback_data="check_join")
    b.adjust(1)

    await message.answer(
        f"⛔ <b>برای استفاده از ربات باید عضو {title} باشید.</b>\n\n"
        f"۱. روی دکمه زیر کلیک کنید و عضو شوید\n"
        f"۲. سپس «عضو شدم» را بزنید",
        parse_mode="HTML",
        reply_markup=b.as_markup(),
    )
