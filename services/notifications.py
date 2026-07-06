"""
services/notifications.py — سرویس نوتیفیکیشن انقضا و ترافیک

هماهنگ‌شده با API جدید سنایی:
  - sync ترافیک واقعی از پنل با GET /clients/traffic/:email
  - آمار دیتابیس قبل از بررسی به‌روز می‌شود
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.engine import AsyncSessionLocal
from database.models import Payment, Subscription, User
from services.xui_api import XUIClient, XUIError

if TYPE_CHECKING:
    from aiogram import Bot

# ── ثرشولدهای هشدار انقضا ───────────────────────
EXPIRY_WARN_DAYS = [7, 3, 1]   # هشدار ۷، ۳ و ۱ روز قبل از انقضا
TRAFFIC_WARN_PERCENT = 80      # هشدار وقتی ۸۰٪ ترافیک مصرف شد

DEFAULT_EXPIRY_WARNING_TEMPLATE = (
    "{badge} <b>هشدار انقضای اشتراک</b>\n"
    "━━━━━━━━━━━━━━━\n"
    "اشتراک شما <b>{time_str}</b> منقضی می‌شود.\n\n"
    "📧 ایمیل: <code>{email}</code>\n"
    "📅 تاریخ انقضا: <code>{expire_str}</code>\n\n"
    "{note}\n\n"
    "برای تمدید از منو <b>🛒 خرید کانفیگ</b> را انتخاب کنید."
)

DEFAULT_TRAFFIC_WARNING_TEMPLATE = (
    "📊 <b>هشدار مصرف ترافیک</b>\n\n"
    "شما <b>{used_pct:.0f}٪</b> از ترافیک خود را مصرف کرده‌اید.\n\n"
    "📧 ایمیل: <code>{email}</code>\n"
    "📦 مصرف‌شده: <code>{used_gb:.2f} / {limit_gb} GB</code>\n\n"
    "برای خرید اشتراک جدید از منو <b>🛒 خرید کانفیگ</b> را انتخاب کنید."
)

DEFAULT_EXPIRED_TEMPLATE = (
    "🔴 <b>اشتراک شما منقضی شد</b>\n\n"
    "📧 ایمیل: <code>{email}</code>\n\n"
    "برای ادامه استفاده از VPN، لطفاً اشتراک جدید خریداری کنید.\n"
    "از منو گزینه <b>🛒 خرید کانفیگ</b> را انتخاب کنید."
)


# ──────────────────────────────────────────────
# پیام‌های نوتیفیکیشن
# ──────────────────────────────────────────────

def _expiry_warning_context(sub: Subscription, days_left: int) -> dict[str, str]:
    if days_left >= 7:
        urgency = "⏰"
        note = "هنوز وقت دارید، اما پیشنهاد می‌کنیم از همین حالا تمدید کنید."
    elif days_left >= 3:
        urgency = "🟡"
        note = "برای جلوگیری از قطع سرویس هرچه زودتر تمدید کنید."
    else:
        urgency = "🔴"
        note = "اشتراک شما به‌زودی قطع می‌شود. همین الان تمدید کنید!"

    if days_left == 1:
        time_str = "فردا"
    else:
        time_str = f"{days_left} روز دیگر"
    expire_str = sub.expiry_date.strftime("%Y-%m-%d") if sub.expiry_date else "نامشخص"
    return {
        "badge": urgency,
        "note": note,
        "time_str": time_str,
        "email": sub.email,
        "expire_str": expire_str,
    }


def _traffic_warning_context(sub: Subscription, used_pct: float) -> dict[str, str]:
    return {
        "used_pct": f"{used_pct:.0f}",
        "email": sub.email,
        "used_gb": f"{sub.used_traffic_bytes / 1024 ** 3:.2f}",
        "limit_gb": str(sub.traffic_limit_gb),
    }


# ──────────────────────────────────────────────
# ارسال به کاربر
# ──────────────────────────────────────────────

async def _send_to_user(bot: "Bot", telegram_id: int, text: str) -> bool:
    """ارسال پیام به کاربر — در صورت شکست False برمی‌گرداند."""
    try:
        await bot.send_message(telegram_id, text, parse_mode="HTML")
        return True
    except Exception as e:
        logger.warning(f"ارسال نوتیف به {telegram_id} ناموفق: {e}")
        return False


def _render_template(template: str, **values: str) -> str:
    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    try:
        return template.format_map(_SafeDict(values))
    except Exception as exc:
        logger.warning(f"رندر قالب نوتیفیکیشن ناموفق: {exc}")
        return template


async def _set_warned_flag(sub_id: int, field: str) -> None:
    """ثبت فلگ هشدار در دیتابیس تا پیام تکراری ارسال نشود."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.id == sub_id)
            .values(**{field: True})
        )
        await session.commit()


async def _check_and_warn_expiry(
    bot: "Bot",
    sub: Subscription,
    user: User,
    days_left: int,
    enabled: bool,
    template: str,
) -> bool:
    """
    بررسی و ارسال هشدار انقضا برای یک threshold.
    فلگ مربوطه را چک می‌کند تا پیام تکراری نفرستد.
    Returns True اگر هشدار ارسال شد.
    """
    # تعیین فیلد فلگ بر اساس روزهای مانده
    if days_left <= 1:
        flag_field = "warned_1d"
        already = sub.warned_1d
    elif days_left <= 3:
        flag_field = "warned_3d"
        already = sub.warned_3d
    else:
        flag_field = "warned_7d"
        already = sub.warned_7d

    if not enabled or already:
        return False  # قبلاً هشدار داده شده

    text = _render_template(template, **_expiry_warning_context(sub, days_left))
    sent = await _send_to_user(bot, user.telegram_id, text)
    if sent:
        await _set_warned_flag(sub.id, flag_field)
        logger.info(
            f"هشدار {days_left} روزه ارسال شد: sub#{sub.id} → user {user.telegram_id}"
        )
    return sent


async def send_traffic_warning(bot: "Bot", sub: Subscription, user: User, enabled: bool, template: str) -> None:
    """ارسال هشدار مصرف ترافیک به کاربر."""
    if not enabled or sub.traffic_limit_gb == 0:
        return  # نامحدود
    limit_bytes = sub.traffic_limit_gb * 1024 ** 3
    used_pct = (sub.used_traffic_bytes / limit_bytes) * 100 if limit_bytes else 0
    if used_pct < TRAFFIC_WARN_PERCENT:
        return
    text = _render_template(template, **_traffic_warning_context(sub, used_pct))
    sent = await _send_to_user(bot, user.telegram_id, text)
    if sent:
        logger.info(f"هشدار ترافیک ارسال شد: sub#{sub.id} → {used_pct:.0f}%")


# ──────────────────────────────────────────────
# Job اصلی: بررسی روزانه
# ──────────────────────────────────────────────

async def _sync_traffic_from_panel(sub: Subscription) -> int:
    """
    دریافت ترافیک واقعی کلاینت از پنل با API جدید:
    GET /panel/api/clients/traffic/:email

    Returns:
        مجموع up+down bytes (یا مقدار فعلی در صورت خطا)
    """
    try:
        async with XUIClient(
            panel_url=settings.panel_url,
            username=settings.panel_username,
            password=settings.panel_password,
            api_path=settings.panel_api_path,
            sub_port=settings.sub_port,
        ) as xui:
            traffic = await xui.get_client_traffic(sub.email)
            if traffic:
                return traffic.up + traffic.down
    except XUIError as e:
        logger.warning(f"sync ترافیک sub#{sub.id} ناموفق: {e}")
    return sub.used_traffic_bytes


async def check_expired_subscriptions(bot: "Bot") -> None:
    """
    Job روزانه — بررسی اشتراک‌های منقضی یا نزدیک به انقضا:
      0. sync ترافیک از پنل
      1. منقضی شده → status=expired + پیام به کاربر
      2. ۷ روز مانده → هشدار (یک‌بار)
      3. ۳ روز مانده → هشدار (یک‌بار)
      4. ۱ روز مانده → هشدار (یک‌بار)
      5. ≥۸۰٪ ترافیک → هشدار ترافیک
    """
    async with AsyncSessionLocal() as session:
        from database.crud import get_setting
        expiry_enabled = await get_setting(session, "notification_expiry_enabled", "1")
        traffic_enabled = await get_setting(session, "notification_traffic_enabled", "1")
        result = await session.execute(
            select(Subscription, User)
            .join(User, User.id == Subscription.user_id)
            .where(Subscription.status == "active")
        )
        rows = result.all()

    logger.info("⏰ شروع بررسی اشتراک‌ها...")
    now = datetime.now(timezone.utc)

    expired_count  = 0
    warned_count   = 0
    traffic_warned = 0
    synced_count   = 0

    for sub, user in rows:

        # ── Step 0: sync ترافیک از پنل ──────────────
        real_used = await _sync_traffic_from_panel(sub)
        if real_used != sub.used_traffic_bytes:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(Subscription)
                    .where(Subscription.id == sub.id)
                    .values(used_traffic_bytes=real_used)
                )
                await session.commit()
            sub.used_traffic_bytes = real_used
            synced_count += 1

        expiry = sub.expiry_date

        # ── Step 1: منقضی شده ───────────────────────
        if expiry and expiry <= now:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(Subscription)
                    .where(Subscription.id == sub.id)
                    .values(status="expired")
                )
                await session.commit()
            if expiry_enabled == "1":
                text = _render_template(DEFAULT_EXPIRED_TEMPLATE, email=sub.email)
                await _send_to_user(bot, user.telegram_id, text)
            expired_count += 1
            logger.info(f"اشتراک #{sub.id} منقضی شد — user {user.telegram_id}")
            continue  # اشتراک منقضی — نیازی به هشدار نیست

        # ── Step 2-4: هشدارهای ۷، ۳ و ۱ روز ────────
        if expiry:
            days_left = (expiry - now).days  # روزهای کامل مانده

            for threshold in EXPIRY_WARN_DAYS:
                # اگه در بازه threshold قرار داریم هشدار بده
                # بازه: از threshold روز تا threshold-1 روز مانده
                if days_left <= threshold:
                    sent = await _check_and_warn_expiry(
                        bot,
                        sub,
                        user,
                        days_left=threshold,
                        enabled=expiry_enabled == "1",
                        template=DEFAULT_EXPIRY_WARNING_TEMPLATE,
                    )
                    if sent:
                        warned_count += 1
                    break  # فقط نزدیک‌ترین threshold را هشدار بده

        # ── Step 5: هشدار ترافیک ────────────────────
        if sub.traffic_limit_gb > 0:
            limit_bytes = sub.traffic_limit_gb * 1024 ** 3
            used_pct = (sub.used_traffic_bytes / limit_bytes * 100) if limit_bytes else 0
            if used_pct >= TRAFFIC_WARN_PERCENT:
                await send_traffic_warning(
                    bot,
                    sub,
                    user,
                    enabled=traffic_enabled == "1",
                    template=DEFAULT_TRAFFIC_WARNING_TEMPLATE,
                )
                traffic_warned += 1

    logger.success(
        f"بررسی روزانه تمام شد: "
        f"{synced_count} sync | {expired_count} منقضی | "
        f"{warned_count} هشدار انقضا | {traffic_warned} هشدار ترافیک"
    )


# ──────────────────────────────────────────────
# پاک‌سازی تراکنش‌های کریپتوی منقضی‌شده
# ──────────────────────────────────────────────

async def cleanup_stale_payments() -> int:
    """
    تراکنش‌های کریپتویی که expires_at گذشته ولی status هنوز
    waiting/confirming/pending است را به expired تغییر می‌دهد.

    این تابع از دو جا فراخوانی می‌شود:
      ۱. هنگام startup ربات — برای restore شده‌ها یا ربات‌هایی که خاموش بودند
      ۲. هر ساعت از طریق APScheduler

    برمی‌گرداند: تعداد تراکنش‌های به‌روزشده
    """
    now = datetime.now(timezone.utc)
    stale_statuses = ("waiting", "confirming", "pending")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Payment).where(
                Payment.payment_method == "crypto",
                Payment.status.in_(stale_statuses),
                Payment.expires_at.isnot(None),
                Payment.expires_at < now,
            )
        )
        stale = result.scalars().all()
        count = len(stale)

        if count:
            ids = [p.id for p in stale]
            await session.execute(
                update(Payment)
                .where(Payment.id.in_(ids))
                .values(status="expired", updated_at=now)
            )
            await session.commit()
            logger.info(f"cleanup_stale_payments: {count} تراکنش کریپتوی منقضی‌شده به 'expired' تغییر کرد.")
        else:
            logger.debug("cleanup_stale_payments: هیچ تراکنش کریپتوی منقضی‌شده‌ای یافت نشد.")

    return count
