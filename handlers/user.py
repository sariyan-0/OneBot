"""
handlers/user.py — هندلرهای اصلی ربات برای کاربران
"""

from __future__ import annotations

import io
import uuid
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    Message,
)
from loguru import logger

from config import settings
from database import AsyncSessionLocal, get_or_create_user, get_user_by_telegram_id
from database.crud import create_payment, get_active_plans, get_all_plans, get_plan, get_user_subscriptions
from keyboards.main_menu import get_main_menu
from services.banner import send_with_banner
from services.card_payment import calc_rial_amount, fmt_card_number, get_card_info, toman_from_usdt, usdt_amount_from_toman
from services.maxelpay import MaxelPayClient, MaxelPayError
from services.payment_config import get_maxelpay_config
from services.payments import CryptoPaymentService, PaymentAPIError, PaymentError
from services.payment_methods import get_payment_status
from services.welcome import (
    check_user_joined,
    send_join_required_message,
    send_welcome_banner,
    get_welcome_banner_file_id,
)
from keyboards.plans import (
    get_confirm_purchase_keyboard,
    get_plan_confirm_keyboard,
    get_plan_detail_keyboard,
    get_plans_keyboard,
    get_subscription_detail_keyboard,
)
from services.subscription import (
    create_new_subscription,
    delete_subscription_completely,
    get_subscriptions_status,
    rotate_subscription_link,
)
from services.wallet import wallet_balance, wallet_balance_toman
from services.xui_api import XUIClient
from utils.qrcode_gen import generate_qr_code

router = Router(name="user")


class SubscriptionActionStates(StatesGroup):
    waiting_plan = State()


class WalletTopupStates(StatesGroup):
    waiting_crypto_amount = State()
    waiting_toman_amount = State()


# ──────────────────────────────────────────────
# Helper: safe edit — اگه پیام عکس داشت از answer استفاده کن
# ──────────────────────────────────────────────

async def _safe_edit(callback: CallbackQuery, text: str, **kwargs) -> None:
    """edit_text امن — اگه پیام عکس‌دار بود، answer جدید می‌فرسته."""
    try:
        if callback.message.photo or callback.message.document:  # type: ignore
            await callback.message.edit_caption(caption=text, **kwargs)  # type: ignore
        else:
            await callback.message.edit_text(text, **kwargs)  # type: ignore
    except Exception:
        await callback.message.answer(text, **kwargs)  # type: ignore


# ──────────────────────────────────────────────
# Helper: تبدیل بایت به واحد خوانا
# ──────────────────────────────────────────────

def _fmt_bytes(b: int) -> str:
    """تبدیل بایت به GB/MB خوانا."""
    if b == 0:
        return "نامحدود"
    gb = b / 1024 ** 3
    if gb >= 1:
        return f"{gb:.2f} GB"
    mb = b / 1024 ** 2
    return f"{mb:.1f} MB"


def _fmt_ts(ts: int) -> str:
    """تبدیل timestamp میلی‌ثانیه به تاریخ فارسی‌پسند."""
    if ts == 0:
        return "نامحدود"
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _fmt_expiry(expiry_date) -> str:
    """نمایش تاریخ انقضا + روزهای باقی‌مانده.

    مثال خروجی:
      2026-07-14  (۱۱ روز مانده)
      2026-07-01  (منقضی شده)
      نامحدود
    """
    from datetime import datetime, timezone
    if not expiry_date:
        return "نامحدود"
    if expiry_date.tzinfo is None:
        expiry_date = expiry_date.replace(tzinfo=timezone.utc)
    date_str = expiry_date.strftime("%Y-%m-%d")
    delta = (expiry_date - datetime.now(timezone.utc)).days
    if delta < 0:
        return f"{date_str}  (منقضی شده)"
    elif delta == 0:
        return f"{date_str}  (امروز منقضی می‌شود)"
    else:
        return f"{date_str}  ({delta} روز مانده)"


async def _plan_toman_price(plan) -> int:
    price_toman = int(getattr(plan, "price_toman", 0) or 0)
    if price_toman > 0:
        return price_toman
    try:
        card = await get_card_info()
        rate = int(card.get("rate", 0) or 0)
    except Exception:
        rate = 0
    return toman_from_usdt(float(plan.price_usdt), rate) if rate > 0 else 0


async def _build_profile_view(tg_user, db_user) -> tuple[str, object]:
    async with AsyncSessionLocal() as session:
        subs = await get_subscriptions_status(session, db_user.id)
        balance = await wallet_balance(session, db_user.id)
        balance_toman = await wallet_balance_toman(session, db_user.id)

    from html import escape
    first_name = escape(tg_user.first_name or "-")
    username = escape(tg_user.username or "-")

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="💰 شارژ کیف پول", callback_data="wallet_topup")
    kb.button(text="🔄 بروزرسانی", callback_data="profile_refresh")
    kb.button(text="🔙 بازگشت", callback_data="back_main")
    kb.adjust(2, 1)

    text = (
        f"👤 <b>پروفایل شما</b>\n\n"
        f"🆔 آی‌دی: <code>{tg_user.id}</code>\n"
        f"👋 نام: {first_name}\n"
        f"📝 نام کاربری: @{username}\n"
        f"💼 موجودی کیف پول:\n"
        f"  • <b>${balance:.2f}</b>\n"
        f"  • <b>{balance_toman:,} تومان</b>\n"
        f"📦 اشتراک‌های فعال: {len(subs)}\n"
        f"📅 تاریخ ثبت‌نام: {db_user.created_at.strftime('%Y-%m-%d')}"
    )
    return text, kb.as_markup()


def _sub_list_status(sub) -> tuple[str, str]:
    """Return (badge, label) for subscription history rows."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    expiry = sub.expiry_date
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    is_active = sub.status == "active" and (not expiry or expiry > now)
    if is_active:
        return "🟢", "فعال"
    if sub.status in {"expired", "depleted", "disabled"}:
        return "🔴", "غیرفعال"
    return "🔴", "پایان‌یافته"


# ──────────────────────────────────────────────
# /start
# ──────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """
    /start — flow:
      1. اگه welcome banner تنظیم شده → نشون بده (اول بار)
      2. چک عضویت کانال
      3. نمایش منوی اصلی
    """
    user = message.from_user
    if not user:
        return

    # ── ۱. نمایش welcome banner اگه تنظیم شده ──────────────
    # فقط بار اولی که کاربر /start میزند (یا اگه هنوز ثبت نشده)
    has_welcome = await get_welcome_banner_file_id()
    async with AsyncSessionLocal() as session:
        db_user, created = await get_or_create_user(
            session=session,
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            admin_ids=settings.admin_ids,
        )

    if created and has_welcome:
        # کاربر جدید + welcome banner → نشون بده و منتظر کلیک "شروع کن" بمون
        await send_welcome_banner(message)
        return

    # ── ۲. چک عضویت کانال ─────────────────────────────────
    if not await check_user_joined(message.bot, user.id):  # type: ignore[arg-type]
        await send_join_required_message(message)
        return

    # ── ۳. منوی اصلی ──────────────────────────────────────
    await _show_main_menu(message, db_user)


async def _show_main_menu(target: Message, db_user) -> None:
    """نمایش منوی اصلی به کاربر."""
    user = target.from_user
    name = (user.first_name if user else None) or (user.username if user else None) or "کاربر"
    greeting = "🌟 به ربات خوش آمدید" if True else "👋 سلام"
    # اگر کاربر قبلاً /start زده بود greeting متفاوت نمی‌خواهیم
    text = (
        f"👋 سلام <b>{name}</b>!\n"
        "━━━━━━━━━━━━━━━\n"
        "🔐 <b>ربات فروش اشتراک VPN</b>\n\n"
        "با این ربات می‌توانید:\n"
        "• اشتراک VPN بخرید\n"
        "• وضعیت اشتراک خود را ببینید\n"
        "• با پشتیبانی در تماس باشید\n\n"
        "👇 از منوی زیر انتخاب کنید:"
    )
    try:
        await send_with_banner(
            target,
            text,
            parse_mode="HTML",
            reply_markup=get_main_menu(is_admin=db_user.is_admin),
        )
    except Exception:
        await target.answer(
            text,
            parse_mode="HTML",
            reply_markup=get_main_menu(is_admin=db_user.is_admin),
        )


@router.callback_query(F.data == "welcome_start")
async def cb_welcome_start(callback: CallbackQuery) -> None:
    """کاربر دکمه «شروع کن» را در welcome banner زد."""
    await callback.answer()
    user = callback.from_user
    if not user:
        return

    # چک عضویت کانال
    if not await check_user_joined(callback.bot, user.id):  # type: ignore[arg-type]
        await send_join_required_message(callback.message)  # type: ignore[arg-type]
        return

    async with AsyncSessionLocal() as session:
        db_user, _ = await get_or_create_user(
            session=session,
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            admin_ids=settings.admin_ids,
        )
    await _show_main_menu(callback.message, db_user)  # type: ignore[arg-type]


@router.callback_query(F.data == "check_join")
async def cb_check_join(callback: CallbackQuery) -> None:
    """کاربر ادعا می‌کند عضو شده — دوباره چک کن."""
    await callback.answer("🔄 در حال بررسی عضویت...")
    user = callback.from_user
    if not user:
        return

    if not await check_user_joined(callback.bot, user.id):  # type: ignore[arg-type]
        await callback.answer("⛔ هنوز عضو نشدید! لطفاً ابتدا عضو کانال شوید.", show_alert=True)
        return

    # عضو شد → منوی اصلی
    async with AsyncSessionLocal() as session:
        db_user, _ = await get_or_create_user(
            session=session,
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            admin_ids=settings.admin_ids,
        )
    await _show_main_menu(callback.message, db_user)  # type: ignore[arg-type]


# ──────────────────────────────────────────────
# منوی خرید کانفیگ (دکمه Reply)
# ──────────────────────────────────────────────

@router.message(F.text == "🛒 خرید کانفیگ")
async def menu_buy_config(message: Message) -> None:
    """دریافت لیست پلن‌ها از پنل و نمایش به کاربر."""
    await message.answer("⏳ در حال دریافت پلن‌ها از سرور...")

    try:
        async with AsyncSessionLocal() as session:
            plans = await get_active_plans(session)
        rate = 0
        try:
            rate = (await get_card_info()).get("rate", 0) or 0
        except Exception:
            rate = 0

        if not plans:
            await message.answer("❌ در حال حاضر هیچ پلنی در دسترس نیست.")
            return

        limited_count = sum(1 for p in plans if p.traffic_gb > 0)
        unlimited_count = sum(1 for p in plans if p.traffic_gb == 0)
        sections = []
        if limited_count:
            sections.append(f"{limited_count} پلن حجمی")
        if unlimited_count:
            sections.append(f"{unlimited_count} پلن نامحدود")

        summary_line = f"{' | '.join(sections)}\n\n" if sections else ""

        text = (
            "📦 *پلن‌های موجود:*\n\n"
            f"{summary_line}"
            "یک پلن برای مشاهده جزئیات انتخاب کنید:"
        )
        pm = await get_payment_status()
        await message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=get_plans_keyboard(plans, rate, show_crypto_price=pm["crypto"], show_toman_price=pm["card"]),
        )

    except Exception as e:
        logger.error(f"خطا در دریافت پلن‌ها: {e}")
        await message.answer("⚠️ خطا در اتصال به سرور. لطفاً بعداً تلاش کنید.")


# ──────────────────────────────────────────────
# Callback: show_plans (از دکمه بازگشت)
# ──────────────────────────────────────────────

@router.callback_query(F.data == "show_plans")
async def cb_show_plans(callback: CallbackQuery) -> None:
    """نمایش مجدد لیست پلن‌ها."""
    await callback.answer()
    try:
        async with AsyncSessionLocal() as session:
            plans = await get_active_plans(session)
        rate = 0
        try:
            rate = (await get_card_info()).get("rate", 0) or 0
        except Exception:
            rate = 0
        pm = await get_payment_status()

        await _safe_edit(
            callback,
            "📦 *پلن‌های موجود:*\n\nیک پلن برای مشاهده جزئیات انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=get_plans_keyboard(plans, rate, show_crypto_price=pm["crypto"], show_toman_price=pm["card"]),
        )
    except Exception as e:
        logger.error(f"خطا در دریافت پلن‌ها: {e}")
        await callback.message.answer("⚠️ خطا در دریافت پلن‌ها.")  # type: ignore[union-attr]


# ──────────────────────────────────────────────
# Callback: plan:{inbound_id} — جزئیات پلن
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("plan:"))
async def cb_plan_detail(callback: CallbackQuery) -> None:
    """نمایش جزئیات یک پلن انتخابی."""
    await callback.answer()
    plan_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
        db_user = await get_user_by_telegram_id(session, callback.from_user.id) if callback.from_user else None
        wallet_usdt = await wallet_balance(session, db_user.id) if db_user else 0.0
        wallet_toman = await wallet_balance_toman(session, db_user.id) if db_user else 0

    if not plan or not plan.is_active:
        await callback.answer("⚠️ این پلن دیگر در دسترس نیست.", show_alert=True)
        return

    traffic_text = "نامحدود" if plan.traffic_gb == 0 else f"{plan.traffic_gb} گیگ"
    device_text = "نامحدود" if not plan.limit_ip else f"{plan.limit_ip} دستگاه"
    inbound_ids = plan.get_inbound_ids()
    inbound_text = ", ".join(map(str, inbound_ids)) if inbound_ids else "همه اینباندهای فعال"
    price_toman = await _plan_toman_price(plan)
    pm = await get_payment_status()
    card_line = f"💵 قیمت کارت: `{price_toman:,} تومان`\n" if (price_toman and pm["card"]) else ""
    crypto_line = f"💠 قیمت کریپتو: `${plan.price_usdt:.2f}`\n" if pm["crypto"] else ""

    text = (
        f"📋 *جزئیات پلن:*\n\n"
        f"🏷 نام: `{plan.name}`\n"
        f"📦 ترافیک: `{traffic_text}`\n"
        f"⏱ مدت: `{plan.duration_days} روز`\n"
        + card_line
        + crypto_line
        + f"👤 محدودیت دستگاه: `{device_text}`\n"
        + f"🔌 اینباندهای مجاز: `{inbound_text}`\n\n"
        + f"برای خرید این پلن روی دکمه زیر کلیک کنید:"
    )

    await _safe_edit(
        callback,
        text,
        parse_mode="Markdown",
        reply_markup=get_plan_confirm_keyboard(
            plan_id,
            crypto_on=pm["crypto"],
            card_on=pm["card"],
            crypto_invoice=pm.get("crypto_invoice", False),
            crypto_gateway=pm.get("crypto_gateway", "nowpayments"),
            amount=float(plan.price_usdt),
            amount_toman=price_toman,
            plan_name=plan.name,
            wallet_balance_usdt=wallet_usdt,
            wallet_balance_toman=wallet_toman,
        ),
    )


# ──────────────────────────────────────────────
# Callback: buy:{inbound_id} — تأیید خرید
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("buy:"))
async def cb_buy_plan(callback: CallbackQuery) -> None:
    """نمایش صفحه تأیید پرداخت."""
    await callback.answer()
    inbound_id = int(callback.data.split(":")[1])  # type: ignore[union-attr]

    from config import settings as _s
    price = _s.plan_price_usdt
    traffic_text = "نامحدود" if _s.default_traffic_gb == 0 else f"{_s.default_traffic_gb} GB"
    try:
        card = await get_card_info()
        rate = int(card.get("rate", 0) or 0)
    except Exception:
        rate = 0
    price_toman = toman_from_usdt(price, rate) if rate > 0 else 0

    text = (
        "💳 *تأیید خرید*\n\n"
        f"پلن انتخابی: `{inbound_id}`\n"
        f"مدت اعتبار: `{_s.default_subscription_days} روز`\n"
        f"ترافیک: `{traffic_text}`\n"
        f"💰 مبلغ قابل پرداخت: `${price:.2f}`\n\n"
        "برای ادامه روی دکمه پرداخت کلیک کنید."
    )

    await _safe_edit(
        callback,
        text,
        parse_mode="Markdown",
        reply_markup=get_plan_confirm_keyboard(
            inbound_id,
            amount=price,
            amount_toman=price_toman,
            plan_name=str(inbound_id),
        ),
    )


# ──────────────────────────────────────────────
# Callback: confirm_buy (deprecated) — redirect به pay
# ──────────────────────────────────────────────
# این callback دیگر از کیبورد فراخوانی نمی‌شود.
# اما به عنوان fallback نگه داشته شده.

@router.callback_query(F.data.startswith("confirm_buy:"))
async def cb_confirm_buy_legacy(callback: CallbackQuery) -> None:
    """Redirect قدیمی — به pay handler هدایت می‌شود."""
    await callback.answer()
    inbound_id = callback.data.split(":")[1]  # type: ignore[union-attr]
    # بازنویسی callback_data و اجرای pay handler
    callback.data = f"pay:{inbound_id}"  # type: ignore[union-attr]
    from handlers.payments import cb_pay_plan
    await cb_pay_plan(callback)


# ──────────────────────────────────────────────
# منوی اشتراک‌های من
# ──────────────────────────────────────────────

@router.message(F.text == "📊 اشتراک‌های من")
@router.callback_query(F.data == "my_subs")
async def menu_my_subscriptions(event: Message | CallbackQuery) -> None:
    """نمایش فهرست اشتراک‌ها به صورت قابل کلیک."""
    if isinstance(event, CallbackQuery):
        await event.answer()
        tg_user = event.from_user
        target_msg = event.message  # type: ignore[union-attr]
    else:
        tg_user = event.from_user
        target_msg = event

    if not tg_user:
        return

    await _render_my_subscriptions(target_msg, tg_user.id)
    if isinstance(event, CallbackQuery) and event.message:
        try:
            await event.message.delete()
        except Exception:
            pass


async def _render_my_subscriptions(target_msg, telegram_id: int) -> None:
    """رندر لیست اشتراک‌های کاربر بدون نیاز به callback جدید."""
    if not telegram_id:
        return

    async with AsyncSessionLocal() as session:
        db_user = await get_user_by_telegram_id(session, telegram_id)
        if not db_user:
            await target_msg.answer("❌ حساب شما پیدا نشد. لطفاً /start بزنید.")
            return
        plans = await get_all_plans(session)
        plan_names = {p.id: p.name for p in plans}
        subs = await get_user_subscriptions(session, db_user.id, active_only=False)

    if not subs:
        await target_msg.answer(
            "📭 شما هنوز هیچ اشتراکی ندارید.\n\n"
            "از منوی <b>🛒 خرید کانفیگ</b> اولین اشتراک خود را بخرید!",
            parse_mode="HTML",
        )
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    def _sort_key(sub):
        from datetime import timezone
        badge, _ = _sub_list_status(sub)
        active_rank = 0 if badge == "🟢" else 1
        expiry = sub.expiry_date
        if expiry and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        exp = expiry.timestamp() if expiry else 0
        return (active_rank, -exp, sub.id)

    kb = InlineKeyboardBuilder()
    for sub in sorted(subs, key=_sort_key):
        badge, status_label = _sub_list_status(sub)
        plan_label = plan_names.get(sub.plan_id, f"پلن #{sub.plan_id}" if getattr(sub, "plan_id", None) else "پلن نامشخص")
        expire = _fmt_expiry(sub.expiry_date)
        kb.button(
            text=f"{badge} {plan_label}\n{status_label} • {expire}",
            callback_data=f"sub_detail:{sub.id}",
        )
    kb.button(text="🔙 بازگشت", callback_data="back_main")
    kb.adjust(1)

    await target_msg.answer(
        "📊 <b>اشتراک‌های شما</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "سبز = فعال\n"
        "قرمز = پایان‌یافته یا غیرفعال\n\n"
        "یکی را انتخاب کنید تا جزئیات، QR و لینک‌ها را ببینید.",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("sub_detail:"))
async def cb_sub_detail(callback: CallbackQuery) -> None:
    await callback.answer()
    sub_id = int(callback.data.split(":", 1)[1])

    async with AsyncSessionLocal() as session:
        from database.models import Subscription, Plan
        sub = await session.get(Subscription, sub_id)
        if not sub:
            await callback.answer("اشتراک پیدا نشد.", show_alert=True)
            return
        plan = await session.get(Plan, sub.plan_id) if getattr(sub, "plan_id", None) else None

    from config import settings as _s
    from services.xui_api import build_sub_link_for
    from utils.qrcode_gen import generate_qr_code
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    sub_link = build_sub_link_for(_s.panel_url, sub.sub_id, _s.sub_port)
    qr_bytes = await generate_qr_code(sub_link)

    used_gb = sub.used_traffic_bytes / 1024 ** 3
    limit_label = f"{sub.traffic_limit_gb} گیگ" if sub.traffic_limit_gb else "نامحدود"
    status_map = {
        "active": "✅ فعال",
        "expired": "⏰ منقضی",
        "depleted": "📭 تمام‌شده",
        "disabled": "🚫 غیرفعال",
        "pending": "⏳ در انتظار",
    }
    status_fa = status_map.get(sub.status, sub.status)
    plan_name = plan.name if plan else (f"#{sub.plan_id}" if getattr(sub, "plan_id", None) else "نامشخص")
    ip_limit_val = getattr(sub, "limit_ip", 0) or 0
    ip_line = f"\n📡 محدودیت دستگاه: <b>{ip_limit_val} همزمان</b>" if ip_limit_val else ""

    text = (
        f"📦 <b>اشتراک شما</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 پلن: <b>{plan_name}</b>\n"
        f"📧 شناسه: <code>{sub.email}</code>\n"
        f"📊 مصرف: <code>{used_gb:.2f} GB</code> از <code>{limit_label}</code>\n"
        f"⏳ انقضا: <code>{_fmt_expiry(sub.expiry_date)}</code>\n"
        f"🔘 وضعیت: {status_fa}"
        f"{ip_line}\n\n"
        f"🔗 <b>لینک اشتراک:</b>\n<code>{sub_link}</code>"
    )

    b = InlineKeyboardBuilder()
    b.button(text="🔄 تغییر لینک", callback_data=f"rotate_link:{sub.id}")
    b.button(text="🔁 تمدید", callback_data=f"renew_sub:{sub.id}")
    b.button(text="🔄 تغییر پلن", callback_data=f"change_plan:{sub.id}")
    b.button(text="📋 کانفیگ‌های مستقل", callback_data=f"sub_configs:{sub.id}")
    b.button(text="🔙 بازگشت به لیست", callback_data="my_subs")
    b.button(text="🗑 حذف اشتراک", callback_data=f"sub_delete_confirm:{sub.id}")
    b.adjust(2, 2, 1, 1, 1)

    if qr_bytes:
        await callback.message.answer_photo(
            BufferedInputFile(qr_bytes, filename=f"sub_{sub.id}_qr.png"),
            caption=text,
            parse_mode="HTML",
            reply_markup=b.as_markup(),
        )
    else:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())


async def _resolve_subscription_plan(session, sub):
    """پیدا کردن پلن قابل تمدید برای اشتراک فعلی."""
    from database.models import Plan
    if getattr(sub, "plan_id", None):
        plan = await session.get(Plan, sub.plan_id)
        if plan:
            return plan
    plans = await get_active_plans(session)
    candidates = [
        p for p in plans
        if p.traffic_gb == sub.traffic_limit_gb and (p.limit_ip or 0) == (sub.limit_ip or 0)
    ]
    if candidates:
        candidates.sort(key=lambda p: (p.sort_order, p.id))
        return candidates[0]
    return None


@router.callback_query(F.data.startswith("resend_link:"))
async def cb_resend_link(callback: CallbackQuery) -> None:
    await callback.answer("⏳ در حال ساخت لینک...")
    sub_id = int(callback.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        from database.models import Subscription
        sub = await session.get(Subscription, sub_id)
        if not sub:
            await callback.answer("اشتراک پیدا نشد.", show_alert=True)
            return
    from config import settings as _s
    from services.xui_api import build_sub_link_for
    from utils.qrcode_gen import generate_qr_code
    sub_link = build_sub_link_for(_s.panel_url, sub.sub_id, _s.sub_port)
    qr_bytes = await generate_qr_code(sub_link)
    text = f"🔗 <b>لینک اشتراک:</b>\n<code>{sub_link}</code>"
    if qr_bytes:
        await callback.message.answer_photo(
            BufferedInputFile(qr_bytes, filename=f"sub_{sub.id}_qr.png"),
            caption=text,
            parse_mode="HTML",
        )
    else:
        await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data.startswith("rotate_link:"))
async def cb_rotate_link(callback: CallbackQuery) -> None:
    await callback.answer("⏳ در حال تغییر لینک...")
    sub_id = int(callback.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        from database.models import Subscription
        sub = await session.get(Subscription, sub_id)
        if not sub:
            await callback.answer("اشتراک پیدا نشد.", show_alert=True)
            return
        try:
            result = await rotate_subscription_link(session, sub_id)
        except Exception as exc:
            logger.error(f"خطا در تغییر لینک اشتراک {sub_id}: {exc}")
            await callback.answer("❌ تغییر لینک ناموفق بود.", show_alert=True)
            return

    text = (
        f"🔄 <b>لینک اشتراک بازسازی شد</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📧 شناسه: <code>{result.email}</code>\n"
        f"🔗 لینک جدید:\n<code>{result.sub_link}</code>"
    )
    if result.qr_bytes:
        await callback.message.answer_photo(
            BufferedInputFile(result.qr_bytes, filename=f"sub_{sub_id}_rotated.png"),
            caption=text,
            parse_mode="HTML",
        )
    else:
        await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data.startswith("sub_delete_confirm:"))
async def cb_sub_delete_confirm(callback: CallbackQuery) -> None:
    await callback.answer()
    sub_id = int(callback.data.split(":", 1)[1])
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ بله، حذف کن", callback_data=f"sub_delete:{sub_id}")
    kb.button(text="❌ انصراف", callback_data=f"sub_detail:{sub_id}")
    kb.adjust(1)

    await _safe_edit(
        callback,
        "🗑 <b>حذف اشتراک</b>\n\n"
        "آیا مطمئن هستید؟",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("sub_delete:"))
async def cb_sub_delete(callback: CallbackQuery) -> None:
    await callback.answer("⏳ در حال حذف اشتراک...")
    sub_id = int(callback.data.split(":", 1)[1])

    async with AsyncSessionLocal() as session:
        from database.models import Subscription

        sub = await session.get(Subscription, sub_id)
        if not sub:
            await callback.answer("اشتراک پیدا نشد.", show_alert=True)
            return

        db_user = await get_user_by_telegram_id(session, callback.from_user.id) if callback.from_user else None
        if not db_user or sub.user_id != db_user.id:
            await callback.answer("🚫 دسترسی ندارید.", show_alert=True)
            return

        try:
            await delete_subscription_completely(session, sub)
        except Exception as exc:
            logger.error(f"خطا در حذف اشتراک کاربر {sub_id}: {exc}")
            await callback.answer("❌ حذف اشتراک ناموفق بود.", show_alert=True)
            return

    await _safe_edit(
        callback,
        "✅ اشتراک با موفقیت حذف شد.",
        parse_mode="HTML",
    )
    await _render_my_subscriptions(callback.message, callback.from_user.id if callback.from_user else 0)  # type: ignore[arg-type]


@router.callback_query(F.data.startswith("renew_sub:"))
async def cb_renew_sub(callback: CallbackQuery) -> None:
    await callback.answer()
    sub_id = int(callback.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        from database.models import Subscription
        sub = await session.get(Subscription, sub_id)
        if not sub:
            await callback.answer("اشتراک پیدا نشد.", show_alert=True)
            return
        plan = await _resolve_subscription_plan(session, sub)
        db_user = await get_user_by_telegram_id(session, callback.from_user.id) if callback.from_user else None
        wallet_usdt = await wallet_balance(session, db_user.id) if db_user else 0.0
        wallet_toman = await wallet_balance_toman(session, db_user.id) if db_user else 0
    if not plan:
        await callback.answer(
            "این اشتراک هنوز پلن قابل تمدید ندارد. برای ادامه از گزینه «تغییر پلن» استفاده کنید.",
            show_alert=True,
        )
        return
    from keyboards.plans import get_plan_confirm_keyboard
    pm = await get_payment_status()
    plan_price_toman = await _plan_toman_price(plan)
    card_line = f"💵 قیمت کارت: <code>{plan_price_toman:,} تومان</code>\n" if plan_price_toman > 0 else ""
    crypto_line = f"💠 قیمت کریپتو: <code>${plan.price_usdt:.2f}</code>\n\n" if pm["crypto"] else "\n"
    plan_text = (
        f"🔁 <b>تمدید پلن</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📦 پلن: <b>{plan.name}</b>\n"
        f"⏱ مدت: <code>{plan.duration_days} روز</code>\n"
        f"{card_line}"
        f"{crypto_line}"
        "پس از پرداخت، همین اشتراک تمدید می‌شود."
    )
    pm = await get_payment_status()
    await _safe_edit(
        callback,
        plan_text,
        parse_mode="HTML",
        reply_markup=get_plan_confirm_keyboard(
            plan.id,
            crypto_on=pm["crypto"],
            card_on=pm["card"],
            crypto_invoice=pm.get("crypto_invoice", False),
            crypto_gateway=pm.get("crypto_gateway", "nowpayments"),
            amount=float(plan.price_usdt),
            amount_toman=plan_price_toman,
            plan_name=plan.name,
            flow="renew",
            target_sub_id=sub.id,
            wallet_balance_usdt=wallet_usdt,
            wallet_balance_toman=wallet_toman,
        ),
    )


@router.callback_query(F.data.startswith("change_plan:"))
async def cb_change_plan(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    sub_id = int(callback.data.split(":", 1)[1])
    async with AsyncSessionLocal() as session:
        from database.models import Subscription
        sub = await session.get(Subscription, sub_id)
        if not sub:
            await callback.answer("اشتراک پیدا نشد.", show_alert=True)
            return
        plans = await get_active_plans(session)
    if not plans:
        await callback.answer("❌ فعلاً پلن فعالی وجود ندارد.", show_alert=True)
        return
    await state.set_state(SubscriptionActionStates.waiting_plan)
    await state.update_data(action="change", sub_id=sub.id)
    await _safe_edit(
        callback,
        "🔄 پلن جدید را انتخاب کنید:\n"
        "بعد از پرداخت، همین اشتراک با پلن جدید جایگزین می‌شود.",
        reply_markup=get_plans_keyboard(plans),
    )


@router.callback_query(F.data.startswith("sub_configs:"))
async def cb_sub_configs(callback: CallbackQuery) -> None:
    await callback.answer()
    sub_id = int(callback.data.split(":", 1)[1])
    await _send_subscription_configs(callback, sub_id)


@router.callback_query(F.data.startswith("get_configs:"))
async def cb_get_configs(callback: CallbackQuery) -> None:
    """دریافت تمام کانفیگ‌های مستقل یک اشتراک — همه در یک پیام."""
    await callback.answer("⏳ در حال دریافت کانفیگ‌ها...")
    sub_id = int(callback.data.split(":")[1])

    await _send_subscription_configs(callback, sub_id)


async def _send_subscription_configs(callback: CallbackQuery, sub_id: int) -> None:
    """ارسال کانفیگ‌های مستقل یک اشتراک بر اساس sub_id."""

    async with AsyncSessionLocal() as session:
        from database.models import Subscription
        from sqlalchemy import select as sa_select
        res = await session.execute(sa_select(Subscription).where(Subscription.id == sub_id))
        sub = res.scalar_one_or_none()

    if not sub:
        await callback.answer("اشتراک پیدا نشد!", show_alert=True)
        return

    try:
        async with XUIClient(
            panel_url=settings.panel_url,
            username=settings.panel_username,
            password=settings.panel_password,
            api_path=settings.panel_api_path,
            sub_port=settings.sub_port,
        ) as xui:
            links = await xui.get_client_links(sub.email)
            if not links:
                links = await xui.get_sub_links(sub.sub_id)

        if not links:
            await callback.answer("⚠️ کانفیگی یافت نشد.", show_alert=True)
            return

        await callback.message.answer(  # type: ignore[union-attr]
            f"📋 <b>کانفیگ‌های مستقل — {sub.email}</b> ({len(links)} سرور)",
            parse_mode="HTML",
        )
        for j, link in enumerate(links, 1):
            proto = link.split("://")[0].upper() if "://" in link else "سرور"
            await callback.message.answer(  # type: ignore[union-attr]
                f"<b>{j}. {proto}</b>\n<code>{link}</code>",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"خطا در دریافت کانفیگ‌های {sub.email}: {e}")
        await callback.answer("❌ خطا در دریافت کانفیگ‌ها. لطفاً دوباره امتحان کنید.", show_alert=True)


# ──────────────────────────────────────────────
# منوی پروفایل
# ──────────────────────────────────────────────

@router.message(F.text == "👤 پروفایل")
async def menu_profile(message: Message) -> None:
    """نمایش اطلاعات پروفایل کاربر."""
    tg_user = message.from_user
    if not tg_user:
        return

    async with AsyncSessionLocal() as session:
        db_user = await get_user_by_telegram_id(session, tg_user.id)
        if not db_user:
            await message.answer("❌ حساب شما یافت نشد. لطفاً /start بزنید.")
            return
    text, markup = await _build_profile_view(tg_user, db_user)
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


def _wallet_topup_keyboard():
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="💠 کریپتو", callback_data="wallet_topup_crypto")
    kb.button(text="💳 تومان / کارت", callback_data="wallet_topup_toman")
    kb.button(text="🔙 بازگشت به پروفایل", callback_data="profile_refresh")
    kb.adjust(2, 1)
    return kb.as_markup()


@router.callback_query(F.data == "profile_refresh")
async def cb_profile_refresh(callback: CallbackQuery) -> None:
    await callback.answer()
    tg_user = callback.from_user
    if not tg_user:
        return
    async with AsyncSessionLocal() as session:
        db_user = await get_user_by_telegram_id(session, tg_user.id)
        if not db_user:
            await callback.answer("❌ حساب شما یافت نشد.", show_alert=True)
            return
    text, markup = await _build_profile_view(tg_user, db_user)
    await _safe_edit(callback, text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data == "wallet_topup")
async def cb_wallet_topup(callback: CallbackQuery) -> None:
    await callback.answer()
    await _safe_edit(
        callback,
        "💼 <b>شارژ کیف پول</b>\n\n"
        "یک روش پرداخت را انتخاب کنید:",
        parse_mode="HTML",
        reply_markup=_wallet_topup_keyboard(),
    )


@router.callback_query(F.data == "wallet_topup_crypto")
async def cb_wallet_topup_crypto(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(WalletTopupStates.waiting_crypto_amount)
    await _safe_edit(
        callback,
        "💠 مبلغ شارژ را به <b>$</b> وارد کنید.\n"
        "حداقل مبلغ: <b>$5</b>",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "wallet_topup_toman")
async def cb_wallet_topup_toman(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(WalletTopupStates.waiting_toman_amount)
    await _safe_edit(
        callback,
        "💳 مبلغ شارژ را به <b>تومان</b> وارد کنید.\n"
        "بعد از پرداخت، کیف پول شما شارژ می‌شود.",
        parse_mode="HTML",
    )


@router.message(WalletTopupStates.waiting_crypto_amount)
async def msg_wallet_crypto_amount(message: Message, state: FSMContext) -> None:
    tg_user = message.from_user
    if not tg_user:
        return

    try:
        amount_usdt = float((message.text or "").replace(",", "").strip())
    except ValueError:
        await message.answer("❌ عدد معتبر وارد کنید.")
        return

    if amount_usdt < 5:
        await message.answer("❌ حداقل شارژ $5 است.")
        return

    await state.clear()
    pm = await get_payment_status()
    order_id = f"wallet_crypto_{tg_user.id}_{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as session:
        db_user, _ = await get_or_create_user(
            session=session,
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            admin_ids=settings.admin_ids,
        )

    if pm.get("crypto_gateway") == "maxelpay":
        runtime = await get_maxelpay_config()
        if not runtime["api_key"]:
            await message.answer("⚠️ درگاه MaxelPay فعال نیست. از روش تومان استفاده کنید.")
            return
        client = MaxelPayClient(
            api_key=runtime["api_key"],
            webhook_url=runtime["webhook_url"] or settings.maxelpay_webhook_callback_url(),
            success_url=f"https://t.me/{(getattr(settings,'bot_username','') or '').lstrip('@')}",
            cancel_url=f"https://t.me/{(getattr(settings,'bot_username','') or '').lstrip('@')}",
        )
        processing = await message.answer("⏳ در حال ساخت لینک شارژ کیف پول...")
        try:
            session_info = await client.create_session(
                order_id=order_id,
                amount_usd=amount_usdt,
                description=f"wallet topup — {order_id}",
                customer_name=tg_user.first_name or "",
                expiration_minutes=60,
                metadata={"telegram_id": str(tg_user.id), "purpose": "wallet"},
            )
            async with AsyncSessionLocal() as session:
                await create_payment(
                    session=session,
                    user_id=db_user.id,
                    order_id=order_id,
                    amount_usdt=amount_usdt,
                    inbound_id=0,
                    payment_id=session_info.session_id,
                    payment_method="maxelpay",
                )
            from aiogram.utils.keyboard import InlineKeyboardBuilder
            kb = InlineKeyboardBuilder()
            kb.button(text="💳 پرداخت در MaxelPay", url=session_info.checkout_url)
            kb.button(text="🔄 بررسی پرداخت", callback_data=f"check_maxel:{order_id}")
            kb.adjust(1)
            await processing.delete()
            await message.answer(
                f"💼 <b>شارژ کیف پول</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"💰 مبلغ: <b>${amount_usdt:.2f}</b>\n"
                f"🔖 سفارش: <code>{order_id}</code>\n\n"
                "پرداخت را انجام دهید تا کیف پول شارژ شود.",
                parse_mode="HTML",
                reply_markup=kb.as_markup(),
            )
            return
        except Exception as exc:
            logger.error(f"خطا در شارژ کیف پول با MaxelPay: {exc}")
            await processing.edit_text("❌ خطا در ایجاد لینک پرداخت.")
            return

    service = CryptoPaymentService()
    try:
        if pm.get("crypto_invoice"):
            inv = await service.create_invoice_page(
                amount_usdt=amount_usdt,
                order_id=order_id,
                expire_minutes=settings.invoice_expire_minutes,
            )
            async with AsyncSessionLocal() as session:
                await create_payment(
                    session=session,
                    user_id=db_user.id,
                    order_id=order_id,
                    amount_usdt=amount_usdt,
                    inbound_id=0,
                    payment_id=inv.invoice_id,
                    payment_method="crypto_invoice",
                )
            from aiogram.utils.keyboard import InlineKeyboardBuilder
            kb = InlineKeyboardBuilder()
            kb.button(text="🌐 باز کردن صفحه پرداخت", url=inv.invoice_url)
            kb.button(text="🔄 بررسی پرداخت", callback_data=f"check_inv:{order_id}")
            kb.adjust(1)
            await message.answer(
                f"💼 <b>شارژ کیف پول</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"💰 مبلغ: <b>${amount_usdt:.2f}</b>\n"
                f"🔖 سفارش: <code>{order_id}</code>\n\n"
                "بعد از پرداخت، کیف پول شما شارژ می‌شود.",
                parse_mode="HTML",
                reply_markup=kb.as_markup(),
            )
        else:
            inv = await service.create_invoice(
                amount_usdt=amount_usdt,
                order_id=order_id,
                inbound_id=0,
                expire_minutes=settings.invoice_expire_minutes,
            )
            async with AsyncSessionLocal() as session:
                await create_payment(
                    session=session,
                    user_id=db_user.id,
                    order_id=order_id,
                    amount_usdt=amount_usdt,
                    inbound_id=0,
                    payment_id=inv.payment_id,
                    pay_address=inv.pay_address,
                    pay_currency=inv.pay_currency,
                    expires_at=inv.expiration_time,
                )
            qr_bytes = await generate_qr_code(inv.qr_data)
            await message.answer_photo(
                BufferedInputFile(qr_bytes, filename="wallet_topup_qr.png"),
                caption=(
                    f"💼 <b>شارژ کیف پول</b>\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"💰 مبلغ: <b>${amount_usdt:.2f}</b>\n"
                    f"🔖 سفارش: <code>{order_id}</code>\n\n"
                    "QR را اسکن کنید یا آدرس را کپی کنید."
                ),
                parse_mode="HTML",
                reply_markup=_wallet_topup_keyboard(),
            )
    except (PaymentError, PaymentAPIError) as exc:
        logger.error(f"خطا در ساخت پرداخت کیف پول: {exc}")
        await message.answer("❌ خطا در ایجاد پرداخت. لطفاً دوباره تلاش کنید.")


@router.message(WalletTopupStates.waiting_toman_amount)
async def msg_wallet_toman_amount(message: Message, state: FSMContext) -> None:
    tg_user = message.from_user
    if not tg_user:
        return
    try:
        amount_toman = int((message.text or "").replace(",", "").strip())
    except ValueError:
        await message.answer("❌ عدد معتبر وارد کنید.")
        return
    if amount_toman <= 0:
        await message.answer("❌ مبلغ باید بیشتر از صفر باشد.")
        return

    card = await get_card_info()
    if not card["number"]:
        await message.answer("⚠️ اطلاعات کارت هنوز تنظیم نشده است.")
        return
    rate = card["rate"] or 90000

    await state.clear()
    order_id = f"wallet_card_{tg_user.id}_{uuid.uuid4().hex[:8]}"
    amount_usdt = usdt_amount_from_toman(amount_toman, rate)
    toman = amount_toman
    rial = amount_toman * 10
    card_fmt = fmt_card_number(card["number"])
    holder = card["holder"] or "—"

    async with AsyncSessionLocal() as session:
        db_user, _ = await get_or_create_user(
            session=session,
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            admin_ids=settings.admin_ids,
        )
        await create_payment(
            session=session,
            user_id=db_user.id,
            order_id=order_id,
            amount_usdt=amount_usdt,
            inbound_id=0,
            payment_method="card",
            amount_rial=rial,
        )

    from handlers.card_payment import CardPayStates
    await state.set_state(CardPayStates.waiting_receipt)
    await state.update_data(
        order_id=order_id,
        plan_name="شارژ کیف پول",
        amount_toman=toman,
        amount_rial=rial,
        amount_usdt=amount_usdt,
        original_price_usdt=amount_usdt,
        discount_code=None,
        discount_percent=0,
        user_db_id=db_user.id,
    )

    await message.answer(
        f"💳 <b>شارژ کیف پول با تومان</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 مبلغ: <b>{amount_toman:,} تومان</b>\n"
        f"🏦 شماره کارت:\n<code>{card_fmt}</code>\n"
        f"👤 به نام: <b>{holder}</b>\n\n"
        f"پس از واریز، <b>رسید</b> را ارسال کنید.\n"
        f"برای لغو: /cancel",
        parse_mode="HTML",
    )


# پشتیبانی اکنون توسط handlers/tickets.py مدیریت می‌شود
# (F.text == "❓ پشتیبانی" → menu_support در ticket_router)


# ──────────────────────────────────────────────
# دستور ورود ادمین — دینامیک (آخرین handler)
# باید در user_router باشد تا /start و سایر دستورات
# مشابه در router های قبلی اول پردازش شوند
# ──────────────────────────────────────────────

@router.message(F.text.regexp(r"^/[a-zA-Z]\w*(\s+\S+)?$"))
async def catch_admin_command(message: Message) -> None:
    """
    آخرین handler — فقط دستور ورود ادمین رو پردازش می‌کنه.
    بقیه دستورات توسط router های قبلی handle شدن.
    """
    from handlers.admin import handle_dynamic_admin_login_if_match
    await handle_dynamic_admin_login_if_match(message)


# ──────────────────────────────────────────────
# Callback: back_main
# ──────────────────────────────────────────────

@router.callback_query(F.data == "back_main")
async def cb_back_main(callback: CallbackQuery) -> None:
    """بازگشت به منوی اصلی."""
    await callback.answer()
    tg_user = callback.from_user
    async with AsyncSessionLocal() as session:
        db_user = await get_user_by_telegram_id(session, tg_user.id)  # type: ignore[union-attr]
    is_admin = db_user.is_admin if db_user else False

    await callback.message.answer(  # type: ignore[union-attr]
        "🏠 منوی اصلی:",
        reply_markup=get_main_menu(is_admin=is_admin),
    )
    await callback.message.delete()  # type: ignore[union-attr]
