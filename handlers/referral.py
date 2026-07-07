"""
handlers/referral.py — هندلرهای سیستم دعوت و referral
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from loguru import logger

from config import settings
from database import AsyncSessionLocal
from database.crud import get_or_create_user, get_user_by_telegram_id
from services.referral import get_user_referral_stats, process_referral
from utils.qrcode_gen import generate_qr_code

router = Router(name="referral")

# نام کاربری ربات — هنگام start در dp.workflow_data ذخیره می‌شود
_BOT_USERNAME: str = ""


async def _get_bot_username(bot) -> str:
    global _BOT_USERNAME
    if not _BOT_USERNAME:
        me = await bot.get_me()
        _BOT_USERNAME = me.username or ""
    return _BOT_USERNAME


# ──────────────────────────────────────────────
# /start ref_{code} — پردازش referral
# ──────────────────────────────────────────────

@router.message(CommandStart(deep_link=True, magic=F.args.startswith("ref_")))
async def cmd_start_referral(message: Message) -> None:
    """
    پردازش deep link هنگام ورود کاربر از لینک دعوت.
    فرمت: /start ref_XXXXXXXX
    """
    args = message.text.split(maxsplit=1)[1] if message.text and " " in message.text else ""
    referral_code = args[4:] if args.startswith("ref_") else ""

    tg_user = message.from_user
    if not tg_user or not referral_code:
        return

    bot_username = await _get_bot_username(message.bot)  # type: ignore[union-attr]

    async with AsyncSessionLocal() as session:
        db_user, created = await get_or_create_user(
            session=session,
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            admin_ids=settings.admin_ids,
        )

        referrer_name = await process_referral(
            session=session,
            new_user=db_user,
            referral_code=referral_code,
            bot_username=bot_username,
        )
        if referrer_name:
            await message.answer(
                f"🎉 *خوش آمدید!*\n\n"
                f"شما از طریق دعوت *{referrer_name}* وارد شدید.\n"
                "از این به بعد درصدی از خریدهای شما به کیف پول دعوت‌کننده اضافه می‌شود.",
                parse_mode="Markdown",
            )
            await _notify_referrer(message, referral_code, tg_user)
        elif not created:
            await message.answer("👋 خوش آمدید! شما قبلاً ثبت‌نام کرده‌اید.")

    # نمایش منوی اصلی
    from keyboards.main_menu import get_main_menu
    await message.answer(
        "از منوی زیر گزینه مورد نظر را انتخاب کنید:",
        reply_markup=get_main_menu(is_admin=db_user.is_admin),
    )


# ──────────────────────────────────────────────
# دکمه «دعوت دوستان»
# ──────────────────────────────────────────────

@router.message(F.text == "👥 دعوت دوستان")
@router.callback_query(F.data == "referral_menu")
async def menu_referral(event: Message | CallbackQuery) -> None:
    """نمایش لینک دعوت + آمار referral کاربر."""
    if isinstance(event, CallbackQuery):
        await event.answer()
        tg_user = event.from_user
        send = event.message.answer  # type: ignore[union-attr]
        bot = event.bot
    else:
        tg_user = event.from_user
        send = event.answer
        bot = event.bot

    if not tg_user:
        return

    bot_username = await _get_bot_username(bot)  # type: ignore[union-attr]

    async with AsyncSessionLocal() as session:
        db_user = await get_user_by_telegram_id(session, tg_user.id)
        if not db_user:
            await send("❌ ابتدا /start بزنید.")
            return

        stats = await get_user_referral_stats(session, db_user, bot_username)

    text = (
        "👥 *سیستم دعوت دوستان*\n\n"
        f"🔗 *لینک دعوت شما:*\n`{stats.referral_link}`\n\n"
        f"📊 *آمار شما:*\n"
        f"• کل دعوت‌شده‌ها: `{stats.total_referrals}` نفر\n"
        f"• تعداد فعال‌شده‌ها: `{stats.rewarded_referrals}` نفر\n"
        f"• مجموع کمیسیون:\n"
        f"  • `${stats.total_commission_usdt:.2f}`\n"
        f"  • `{stats.total_commission_toman:,} تومان`\n\n"
        "🎁 *قوانین پاداش:*\n"
        "• اولین لینک دعوتی که کاربر با آن وارد شود برای همیشه ثبت می‌شود\n"
        "• درصدی از تمام خریدهای بعدی او به کیف پول شما اضافه می‌شود\n\n"
        "لینک را برای دوستان خود ارسال کنید! 🚀"
    )

    # ارسال QR Code لینک دعوت
    try:
        qr_bytes = await generate_qr_code(stats.referral_link)
        qr_file = BufferedInputFile(file=qr_bytes, filename="referral_qr.png")
        await send.__self__.answer_photo(  # type: ignore[attr-defined]
            photo=qr_file,
            caption=text,
            parse_mode="Markdown",
        )
    except Exception:
        # fallback بدون QR
        await send(text, parse_mode="Markdown")


# ──────────────────────────────────────────────
# اطلاع‌رسانی به دعوت‌کننده
# ──────────────────────────────────────────────

async def _notify_referrer(message: Message, referral_code: str, new_tg_user) -> None:
    """ارسال پیام به کسی که لینک دعوت داده."""
    from database.crud import get_user_by_referral_code
    async with AsyncSessionLocal() as session:
        referrer = await get_user_by_referral_code(session, referral_code)
        if not referrer:
            return

    name = new_tg_user.first_name or f"@{new_tg_user.username}" or "یک کاربر"
    text = (
        f"🎉 *دعوت موفق!*\n\n"
        f"*{name}* با لینک دعوت شما ثبت‌نام کرد.\n"
        "اگر خرید انجام دهد، درصد کمیسیونش به کیف پول شما اضافه می‌شود. 🎁"
    )
    try:
        await message.bot.send_message(referrer.telegram_id, text, parse_mode="Markdown")  # type: ignore[union-attr]
    except Exception as e:
        logger.warning(f"ارسال نوتیف به referrer {referrer.telegram_id} ناموفق: {e}")
