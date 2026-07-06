"""
main.py — نقطه ورود ربات تلگرام VPN
فاز ۵: APScheduler + rate limiting + error handling
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from config import settings
from database import AsyncSessionLocal, init_db
from database.crud import create_plan, get_all_plans
from handlers.admin import router as admin_router
from handlers.broadcast import router as broadcast_router
from handlers.errors import router as error_router
from handlers.payments import router as payment_router
from handlers.referral import router as referral_router
from handlers.shop import router as shop_router
from handlers.tickets import router as ticket_router
from handlers.user import router as user_router
from handlers.uuid_import import router as uuid_router
from handlers.card_payment import router as card_payment_router
from handlers.maxelpay_payment import router as maxelpay_router
from middlewares.activity_log import ActivityLogMiddleware
from middlewares.blocked_user import BlockedUserMiddleware
from middlewares.rate_limit import RateLimitMiddleware
from services.activity_log import ActivityLoggingBot
from services.notifications import check_expired_subscriptions, cleanup_stale_payments
from services.backup import send_daily_backups
from services.webhook_server import start_webhook_server


# ──────────────────────────────────────────────
# تنظیم لاگ
# ──────────────────────────────────────────────

async def _notify_admins_startup(bot) -> None:
    """
    بعد از راه‌اندازی به ادمین‌ها پیام می‌فرسته.
    اگه کریپتو فعاله ولی API Key نداره، هشدار قرمز می‌ده.
    """
    from services.payment_methods import get_payment_status

    warnings = []

    if not settings.nowpayments_api_key:
        pm = await get_payment_status()
        if pm["crypto"]:
            warnings.append(
                "🚨 <b>کریپتو فعاله ولی API Key ندارد!</b>\n"
                "کاربران آدرس جعلی می‌بینند.\n"
                "→ <code>NOWPAYMENTS_API_KEY</code> را در .env وارد کنید."
            )

    if settings.nowpayments_api_key and not settings.nowpayments_ipn_secret:
        warnings.append(
            "⚠️ <b>IPN Secret تنظیم نشده.</b>\n"
            "تأیید خودکار پرداخت غیرفعال است.\n"
            "→ <code>NOWPAYMENTS_IPN_SECRET</code> را در .env وارد کنید."
        )

    if not warnings:
        return

    text = (
        "🤖 <b>ربات راه‌اندازی شد</b> — هشدار تنظیمات:\n"
        "━━━━━━━━━━━━━━━\n"
        + "\n\n".join(warnings)
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception:
            pass


async def _seed_default_plans() -> None:
    """
    اگر هیچ پلنی در دیتابیس نیست، پلن‌های پیش‌فرض ایجاد می‌کند.
    ادمین می‌تواند بعداً قیمت‌ها را از بات تغییر دهد.
    """
    async with AsyncSessionLocal() as session:
        plans = await get_all_plans(session)
        if plans:
            return  # پلن وجود دارد، seed نمی‌کند

        defaults = [
            # (نام، حجم GB، روز، قیمت USDT، limit_ip، sort)
            ("10 گیگ — ۱ ماهه", 10, 30, 3.0, 0, 1),
            ("20 گیگ — ۱ ماهه", 20, 30, 5.0, 0, 2),
            ("40 گیگ — ۱ ماهه", 40, 30, 8.0, 0, 3),
            ("60 گیگ — ۱ ماهه", 60, 30, 11.0, 0, 4),
            ("100 گیگ — ۱ ماهه", 100, 30, 15.0, 0, 5),
            ("نامحدود — ۱ کاربره", 0, 30, 10.0, 1, 6),
            ("نامحدود — ۲ کاربره", 0, 30, 18.0, 2, 7),
            ("نامحدود — ۳ کاربره", 0, 30, 25.0, 3, 8),
        ]
        for name, gb, days, price, lip, sort in defaults:
            await create_plan(session, name=name, traffic_gb=gb,
                              duration_days=days, price_usdt=price,
                              limit_ip=lip, sort_order=sort)
        logger.success(f"✅ {len(defaults)} پلن پیش‌فرض ایجاد شد.")


def setup_logging() -> None:
    """
    پیکربندی loguru: console رنگی + فایل چرخشی.
    اگر پوشه لاگ قابل نوشتن نباشد، فقط stdout فعال می‌ماند و crash نمی‌کند.
    """
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level:<8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> — {message}"
        ),
        colorize=True,
    )

    # فایل لاگ — اختیاری، در صورت مشکل permission فقط هشدار می‌دهد
    try:
        log_path = Path(settings.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            settings.log_file,
            level=settings.log_level,
            rotation="10 MB",
            retention="30 days",
            compression="zip",
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} — {message}",
        )
    except PermissionError:
        logger.warning("⚠️  دسترسی نوشتن در پوشه logs نیست — فقط stdout فعال است.")


# ──────────────────────────────────────────────
# راه‌اندازی Scheduler
# ──────────────────────────────────────────────

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """
    ایجاد و پیکربندی APScheduler.

    Job‌ها:
      • check_expired_subscriptions   — هر ۶ ساعت
      • cleanup_stale_payments        — هر ساعت یک‌بار
      • send_daily_backups            — هر روز ساعت ۰۲:۰۰ UTC
    """
    scheduler = AsyncIOScheduler(timezone="UTC")

    # بررسی انقضا و sync ترافیک ← هر ۶ ساعت
    scheduler.add_job(
        check_expired_subscriptions,
        trigger=CronTrigger(hour="*/6", minute=0),
        args=[bot],
        id="six_hour_expiry_check",
        name="بررسی اشتراک‌ها هر ۶ ساعت",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # پاک‌سازی تراکنش‌های کریپتوی منقضی‌شده ← هر ساعت
    # تراکنش‌هایی که expires_at گذشته ولی status هنوز waiting است
    scheduler.add_job(
        cleanup_stale_payments,
        trigger=CronTrigger(minute=0),   # دقیقه ۰ هر ساعت
        id="hourly_payment_cleanup",
        name="پاک‌سازی تراکنش‌های کریپتوی منقضی",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # backup روزانه ← هر روز ۰۲:۰۰ UTC (قبل از ساعت شلوغ)
    scheduler.add_job(
        send_daily_backups,
        trigger=CronTrigger(hour=2, minute=0),
        args=[bot],
        id="daily_backup",
        name="پشتیبان‌گیری روزانه دیتابیس ربات و پنل",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    return scheduler


# ──────────────────────────────────────────────
# راه‌اندازی ربات
# ──────────────────────────────────────────────

async def main() -> None:
    setup_logging()
    logger.info("🚀 ربات VPN در حال راه‌اندازی...")

    # ── آماده‌سازی دیتابیس ───────────────────
    logger.info("بررسی و ایجاد جداول دیتابیس...")
    await init_db()
    logger.success("دیتابیس آماده است ✓")

    # ── ایجاد پلن‌های پیش‌فرض اگر هیچ پلنی وجود ندارد ──
    await _seed_default_plans()

    # ── پاک‌سازی تراکنش‌های کریپتوی منقضی‌شده (startup) ──
    # این مخصوصاً برای مواقع restore بک‌آپ لازم است:
    # تراکنش‌هایی که expires_at گذشته ولی status هنوز waiting است
    # بلافاصله بعد از راه‌اندازی به expired تغییر می‌کنند
    stale_count = await cleanup_stale_payments()
    if stale_count:
        logger.info(f"startup cleanup: {stale_count} تراکنش کریپتوی منقضی‌شده پاک‌سازی شد.")

    # ── ساخت Bot ─────────────────────────────
    bot = ActivityLoggingBot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # ── هشدار startup به ادمین‌ها ────────────
    await _notify_admins_startup(bot)

    # ── ساخت Dispatcher ──────────────────────
    dp = Dispatcher(storage=MemoryStorage())

    # ── Middleware‌ها ─────────────────────────
    blocked_middleware = BlockedUserMiddleware(admin_ids=list(settings.admin_ids))
    dp.message.middleware(blocked_middleware)
    dp.callback_query.middleware(blocked_middleware)
    dp.message.middleware(ActivityLogMiddleware())

    # Rate limiting: burst detection + progressive cooldown (5s→10s→...→30s)
    # ریست خودکار هر ۱ ساعت | ادمین‌ها و UUID معاف هستن
    dp.message.middleware(
        RateLimitMiddleware(
            rate_limit=3,
            window_sec=5.0,
            admin_ids=list(settings.admin_ids),
        )
    )

    # ── ثبت Router‌ها (ترتیب مهم است) ─────────
    dp.include_router(error_router)        # خطاهای سراسری — اول
    dp.include_router(admin_router)        # پنل ادمین — قبل از بقیه
    dp.include_router(broadcast_router)    # broadcast ادمین
    dp.include_router(referral_router)     # deep link — قبل از user
    dp.include_router(shop_router)         # خرید کانفیگ + pay: handler اصلی
    dp.include_router(card_payment_router) # پرداخت کارت به کارت
    dp.include_router(maxelpay_router)     # پرداخت MaxelPay
    dp.include_router(payment_router)      # check_payment + webhook — بعد از shop
    dp.include_router(uuid_router)         # افزودن اشتراک قدیمی / import link
    dp.include_router(ticket_router)       # تیکت (FSM)
    dp.include_router(user_router)         # هندلرهای عمومی — آخر

    # ── Scheduler ────────────────────────────
    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.success(f"Scheduler راه‌اندازی شد — {len(scheduler.get_jobs())} job فعال")

    # ── Webhook Server (NOWPayments IPN) ─────
    # اگر IPN URL تنظیم شده یا WEBHOOK_PORT وجود دارد، server شروع می‌شود
    webhook_runner = None
    if settings.nowpayments_ipn_secret or getattr(settings, "webhook_port", 0):
        try:
            webhook_runner = await start_webhook_server()
        except Exception as e:
            logger.warning(f"Webhook server راه‌اندازی نشد: {e} — پرداخت polling دستی کار می‌کند")

    # ── شروع polling ─────────────────────────
    logger.info("شروع دریافت پیام‌ها (polling)...")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=True,
        )
    finally:
        scheduler.shutdown(wait=False)
        if webhook_runner:
            await webhook_runner.cleanup()
        await bot.session.close()
        logger.info("ربات متوقف شد.")


if __name__ == "__main__":
    asyncio.run(main())
