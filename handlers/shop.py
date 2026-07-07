"""
handlers/shop.py — خرید کانفیگ و اشتراک تست

Flow خرید:
  1. کاربر «🛒 خرید کانفیگ» → لیست پلن‌های دیتابیس
  2. انتخاب پلن → نمایش جزئیات + دکمه تأیید + دکمه کد تخفیف
  3. (اختیاری) کاربر کد تخفیف وارد می‌کند → تأیید قیمت جدید
  4. تأیید → ایجاد invoice پرداخت
  5. پرداخت → ایجاد کانفیگ در پنل + ارسال لینک
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from loguru import logger

from config import settings
from database import AsyncSessionLocal
from database.crud import (
    create_payment, get_discount_code, get_or_create_user, get_plan,
    get_active_plans, has_used_test_subscription,
    record_test_subscription, use_discount_code, validate_discount,
)
from keyboards.main_menu import get_main_menu
from keyboards.plans import (
    get_confirm_after_discount_keyboard, get_payment_status_keyboard,
    get_plan_confirm_keyboard, get_plans_keyboard,
)
from services.payments import CryptoPaymentService, PaymentError, PaymentAPIError
from services.subscription import create_new_subscription
from services.xui_api import XUIClient, XUIError
from services.banner import send_with_banner
from services.payment_methods import get_payment_status
from services.wallet import wallet_balance, wallet_balance_toman

router = Router(name="shop")


# ──────────────────────────────────────────────
# Helper: ارسال کانفیگ‌ها به کاربر
# ──────────────────────────────────────────────

async def _send_subscription_to_user(message: Message, result, plan_name: str = "") -> None:
    """
    بعد از ایجاد اشتراک:
      1. QR Code از sub_link (برای import همه کانفیگ‌ها)
      2. لینک subscription متنی
      3. هر کانفیگ تکی (vless://... vmess://...) در پیام جداگانه با code block
    """
    import io
    # ── پیام اصلی با sub_link ──────────────────
    title = f"{plan_name} — " if plan_name else ""
    header = f"🎉 *{title}اشتراک شما آماده شد!*\n"
    body = (
        f"━━━━━━━━━━━━━━━\n"
        f"📧 شناسه اشتراک: `{result.email}`\n\n"
        f"🔗 *لینک اشتراک* (همه سرورها):\n`{result.sub_link}`\n\n"
        "📲 این لینک را در اپ‌های زیر وارد کنید:\n"
        "• اندروید: وی‌تو‌ری‌ان‌جی، هیدیفای\n"
        "• آیفون: استرایزند، شدوراکت\n"
        "• ویندوز: هیدیفای، وی‌تو‌ری‌ان\n\n"
        "یا QR کد زیر را اسکن کنید 👇"
    )
    await message.answer(header + body, parse_mode="Markdown")

    # ── QR Code از sub_link ────────────────────
    if result.qr_bytes:
        await message.answer_photo(
            BufferedInputFile(result.qr_bytes, filename="sub_qr.png"),
            caption="📷 اسکن کنید تا همه کانفیگ‌ها اضافه شوند",
        )

    # ── کانفیگ‌های تکی ────────────────────────
    links = result.config_links
    if links:
        await message.answer(
            f"📋 *کانفیگ‌های مستقل ({len(links)} سرور)*\n"
            "اگر نمی‌توانید لینک اشتراک وارد کنید، هر کانفیگ را جداگانه کپی کنید:",
            parse_mode="Markdown",
        )
        for i, link in enumerate(links, 1):
            proto = link.split("://")[0].upper() if "://" in link else "سرور"
            await message.answer(
                f"*سرور {i} — {proto}:*\n`{link}`",
                parse_mode="Markdown",
            )
    else:
        await message.answer(
            "ℹ️ کانفیگ مستقل در دسترس نیست.\n"
            "از لینک اشتراک بالا برای اتصال استفاده کنید."
        )


# ──────────────────────────────────────────────
# FSM
# ──────────────────────────────────────────────

class DiscountState(StatesGroup):
    waiting_code = State()


# ──────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────

_FARSI_USERS = {
    1: "یک کاربره",
    2: "دو کاربره",
    3: "سه کاربره",
    4: "چهار کاربره",
    5: "پنج کاربره",
}


async def _get_usdt_rate() -> int:
    """نرخ دلار به تومان را از DB می‌خواند."""
    try:
        from database.crud import get_setting
        async with AsyncSessionLocal() as s:
            val = await get_setting(s, "usdt_to_toman_rate", "0")
            return int(val) if val and val.isdigit() else 0
    except Exception:
        return 0


async def _get_wallet_balances(session, tg_user) -> tuple[float, int]:
    db_user, _ = await get_or_create_user(
        session,
        tg_user.id,
        tg_user.username,
        tg_user.first_name,
        admin_ids=settings.admin_ids,
    )
    return await wallet_balance(session, db_user.id), await wallet_balance_toman(session, db_user.id)


def _fmt_usdt(price: float) -> str:
    """
    نمایش هوشمند قیمت دلاری — بدون صفر اضافه، بدون برش اشتباه.
    مثال‌ها:
      5.0   → "5"
      3.5   → "3.5"
      0.03  → "0.03"
      0.005 → "0.005"
    """
    if price == int(price):
        return f"{int(price)}"
    formatted = f"{price:g}"
    if "e" in formatted or "E" in formatted:
        decimals = max(2, -int(f"{price:.0e}".split("e")[1]) + 1)
        formatted = f"{price:.{decimals}f}".rstrip("0")
        if formatted.endswith("."):
            formatted += "0"
    return formatted


def _plan_toman_price(plan, fallback_rate: int = 0) -> int:
    price_toman = int(getattr(plan, "price_toman", 0) or 0)
    if price_toman <= 0 and fallback_rate > 0:
        price_toman = int(round(float(plan.price_usdt) * fallback_rate))
    return max(price_toman, 0)


async def _fmt_plan_with_price(plan, show_crypto_price: bool = True, show_toman_price: bool = True, price_toman: int | None = None) -> str:
    """فرمت پلن برای نمایش عمومی — کارت تومان، کریپتو دلار."""
    if plan.traffic_gb == 0:
        if plan.limit_ip and plan.limit_ip > 0:
            user_label = _FARSI_USERS.get(plan.limit_ip, f"{plan.limit_ip} کاربره")
            traffic = f"نامحدود — {user_label}"
        else:
            traffic = "نامحدود"
    else:
        traffic = f"{plan.traffic_gb} گیگابایت"

    price_str = _fmt_usdt(plan.price_usdt)
    if price_toman is None:
        price_toman = getattr(plan, "price_toman", 0) or 0
    if not show_toman_price or price_toman <= 0:
        price_line = f"💠 قیمت کریپتو: `${price_str}`"
    elif price_toman > 0 and not show_crypto_price:
        price_line = f"💵 قیمت کارت: `{price_toman:,} تومان`"
    else:
        price_line = f"💵 قیمت کارت: `{price_toman:,} تومان`\n💠 قیمت کریپتو: `${price_str}`"

    return (
        f"📦 *{plan.name}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🗂 حجم ترافیک: `{traffic}`\n"
        f"⏱ مدت اعتبار: `{plan.duration_days} روز`\n"
        f"{price_line}"
    )


def _fmt_plan(plan, show_crypto_price: bool = True, show_toman_price: bool = True, price_toman: int | None = None) -> str:
    """نسخه sync برای نمایش عمومی — کارت تومان، کریپتو دلار."""
    if plan.traffic_gb == 0:
        if plan.limit_ip and plan.limit_ip > 0:
            user_label = _FARSI_USERS.get(plan.limit_ip, f"{plan.limit_ip} کاربره")
            traffic = f"نامحدود — {user_label}"
        else:
            traffic = "نامحدود"
    else:
        traffic = f"{plan.traffic_gb} گیگابایت"
    price_str = _fmt_usdt(plan.price_usdt)
    if price_toman is None:
        price_toman = getattr(plan, "price_toman", 0) or 0
    price_line = f"💠 قیمت کریپتو: `${price_str}`"
    if not show_toman_price or price_toman <= 0:
        price_line = f"💠 قیمت کریپتو: `${price_str}`"
    elif price_toman > 0 and not show_crypto_price:
        price_line = f"💵 قیمت کارت: `{price_toman:,} تومان`"
    else:
        price_line = f"💵 قیمت کارت: `{price_toman:,} تومان`\n💠 قیمت کریپتو: `${price_str}`"
    return (
        f"📦 *{plan.name}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🗂 حجم ترافیک: `{traffic}`\n"
        f"⏱ مدت اعتبار: `{plan.duration_days} روز`\n"
        f"{price_line}"
    )


def _xui_client() -> XUIClient:
    return XUIClient(
        panel_url=settings.panel_url,
        username=settings.panel_username,
        password=settings.panel_password,
        api_path=settings.panel_api_path,
        sub_port=settings.sub_port,
    )


# ──────────────────────────────────────────────
# 🛒 خرید کانفیگ — منوی اصلی
# ──────────────────────────────────────────────

@router.message(F.text == "🛒 خرید کانفیگ")
async def msg_buy(message: Message) -> None:
    async with AsyncSessionLocal() as session:
        plans = await get_active_plans(session)
    if not plans:
        await message.answer(
            "⚠️ در حال حاضر پلنی موجود نیست.\nلطفاً بعداً مراجعه کنید.",
            reply_markup=get_main_menu(),
        )
        return
    limited_count = sum(1 for p in plans if p.traffic_gb > 0)
    unlimited_count = sum(1 for p in plans if p.traffic_gb == 0)
    desc_parts = []
    if limited_count:
        desc_parts.append(f"{limited_count} پلن حجمی")
    if unlimited_count:
        desc_parts.append(f"{unlimited_count} پلن نامحدود")

    summary_line = f"{' | '.join(desc_parts)}\n\n" if desc_parts else ""

    rate = await _get_usdt_rate()
    pm = await get_payment_status()
    await send_with_banner(
        message,
        f"🛒 <b>خرید اشتراک VPN</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{summary_line}"
        "👇 پلن مورد نظر خود را انتخاب کنید:",
        parse_mode="HTML",
        reply_markup=get_plans_keyboard(plans, rate, show_crypto_price=pm["crypto"], show_toman_price=pm["card"]),
    )


async def _safe_edit_cb(callback: CallbackQuery, text: str, **kwargs) -> None:
    """edit_text امن — اگه پیام عکس‌دار بود، answer جدید می‌فرسته."""
    try:
        if callback.message.photo or callback.message.document:  # type: ignore
            await callback.message.edit_caption(caption=text, **kwargs)  # type: ignore
        else:
            await callback.message.edit_text(text, **kwargs)  # type: ignore
    except Exception:
        await callback.message.answer(text, **kwargs)  # type: ignore


@router.callback_query(F.data == "show_plans")
async def cb_show_plans(callback: CallbackQuery) -> None:
    await callback.answer()
    async with AsyncSessionLocal() as session:
        plans = await get_active_plans(session)
    rate = await _get_usdt_rate()
    pm = await get_payment_status()
    await _safe_edit_cb(
        callback,
        "🛒 *خرید اشتراک VPN*\n"
        "━━━━━━━━━━━━━━━\n"
        "👇 پلن مورد نظر خود را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=get_plans_keyboard(plans, rate, show_crypto_price=pm["crypto"], show_toman_price=pm["card"]),
    )


# ──────────────────────────────────────────────
# انتخاب پلن → نمایش جزئیات + تأیید
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("plan:"))
async def cb_plan_select(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    plan_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
        wallet_usdt, wallet_toman = await _get_wallet_balances(session, callback.from_user) if callback.from_user else (0.0, 0)
    if not plan or not plan.is_active:
        await callback.answer("❌ این پلن در دسترس نیست.", show_alert=True)
        return
    flow_data = await state.get_data()
    flow = str(flow_data.get("action", "new") or "new")
    target_sub_id = int(flow_data.get("sub_id", 0) or 0)
    pm = await get_payment_status()
    rate = await _get_usdt_rate()
    price_toman = _plan_toman_price(plan, rate)
    plan_text = await _fmt_plan_with_price(
        plan,
        show_crypto_price=pm["crypto"],
        show_toman_price=pm["card"],
        price_toman=price_toman,
    )
    await _safe_edit_cb(
        callback,
        plan_text + "\n\n✅ روش پرداخت را انتخاب کنید:",
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
            flow=flow,
            target_sub_id=target_sub_id,
            wallet_balance_usdt=wallet_usdt,
            wallet_balance_toman=wallet_toman,
        ),
    )
    if flow != "new":
        await state.clear()


# ──────────────────────────────────────────────
# کد تخفیف
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("discount:"))
async def cb_discount_request(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    plan_id = int(callback.data.split(":")[1])
    await state.set_state(DiscountState.waiting_code)
    await state.update_data(
        plan_id=plan_id,
        plan_chat_id=callback.message.chat.id if callback.message else None,
        plan_message_id=callback.message.message_id if callback.message else None,
    )
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ انصراف", callback_data=f"discount_cancel:{plan_id}")
    await _safe_edit_cb(
        callback,
        "🏷 کد تخفیف خود را وارد کنید:",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("discount_cancel:"))
async def cb_discount_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """لغو ورود کد تخفیف — برگشت به صفحه پلن با روش‌های پرداخت واقعی."""
    state_data = await state.get_data()
    await state.clear()
    await callback.answer("❌ انصراف از کد تخفیف.")
    plan_id = int(callback.data.split(":")[1])
    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
        wallet_usdt, wallet_toman = await _get_wallet_balances(session, callback.from_user) if callback.from_user else (0.0, 0)
    if plan:
        # وضعیت روش‌های پرداخت را از DB بخوان — نه از default
        pm = await get_payment_status()
        rate = await _get_usdt_rate()
        price_toman = _plan_toman_price(plan, rate)
        plan_message_id = state_data.get("plan_message_id")
        chat_id = state_data.get("plan_chat_id") or callback.message.chat.id
        text = await _fmt_plan(plan, show_crypto_price=pm["crypto"], show_toman_price=pm["card"], price_toman=price_toman)
        text += "\n\n✅ روش پرداخت را انتخاب کنید:"
        markup = get_plan_confirm_keyboard(
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
        )
        if plan_message_id:
            try:
                await callback.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=plan_message_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=markup,
                )
            except Exception:
                await callback.message.answer(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await callback.message.answer("بازگشت به لیست پلن‌ها:", reply_markup=None)
        await cb_show_plans.__wrapped__(callback)  # type: ignore


@router.message(DiscountState.waiting_code, F.text.in_({"/cancel", "انصراف", "cancel"}))
async def msg_discount_cancel_text(message: Message, state: FSMContext) -> None:
    """لغو با تایپ /cancel."""
    data = await state.get_data()
    plan_id = data.get("plan_id")
    await state.clear()
    await message.answer("❌ ورود کد تخفیف لغو شد.")
    if plan_id:
        async with AsyncSessionLocal() as session:
            plan = await get_plan(session, plan_id)
            wallet_usdt, wallet_toman = await _get_wallet_balances(session, message.from_user) if message.from_user else (0.0, 0)
        if plan:
            # وضعیت روش‌های پرداخت را از DB بخوان — نه از default
            pm = await get_payment_status()
            rate = await _get_usdt_rate()
            price_toman = _plan_toman_price(plan, rate)
            await message.answer(
                (await _fmt_plan(plan, show_crypto_price=pm["crypto"], show_toman_price=pm["card"], price_toman=price_toman)) + "\n\n✅ روش پرداخت را انتخاب کنید:",
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


@router.message(DiscountState.waiting_code)
async def msg_discount_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip().upper()
    data = await state.get_data()
    plan_id = data["plan_id"]

    async with AsyncSessionLocal() as session:
        dc = await get_discount_code(session, code)
        if not dc:
            from aiogram.utils.keyboard import InlineKeyboardBuilder
            kb = InlineKeyboardBuilder()
            kb.button(text="❌ انصراف", callback_data=f"discount_cancel:{plan_id}")
            await message.answer(
                "❌ کد تخفیف پیدا نشد. دوباره وارد کنید یا انصراف دهید:",
                reply_markup=kb.as_markup(),
            )
            return
        valid, err_msg = validate_discount(dc)
        if not valid:
            await state.clear()
            await message.answer(f"❌ {err_msg}")
            return
        plan = await get_plan(session, plan_id)
        wallet_usdt, wallet_toman = await _get_wallet_balances(session, message.from_user) if message.from_user else (0.0, 0)
        state_data = await state.get_data()
        plan_chat_id = state_data.get("plan_chat_id")
        plan_message_id = state_data.get("plan_message_id")

    rate = await _get_usdt_rate()
    base_toman = _plan_toman_price(plan, rate)
    discount_amount = plan.price_usdt * dc.percent / 100
    final_price = round(plan.price_usdt - discount_amount, 2)
    final_toman = max(int(round(base_toman * (1 - dc.percent / 100))), 0)

    await state.clear()
    pm = await get_payment_status()
    text = (
        f"✅ کد تخفیف *{dc.code}* اعمال شد!\n\n"
        f"💲 قیمت اصلی: `${_fmt_usdt(plan.price_usdt)}`\n"
        f"💳 قیمت اصلی: `{base_toman:,} تومان`\n"
        f"🏷 تخفیف {dc.percent}٪: `-${_fmt_usdt(discount_amount)}` / `-{int(round(base_toman * dc.percent / 100)):,} تومان`\n"
        f"💰 قیمت نهایی: `${_fmt_usdt(final_price)}`\n"
        f"💰 قیمت نهایی: `{final_toman:,} تومان`\n\n"
        f"روش پرداخت را انتخاب کنید:"
    )
    markup = get_confirm_after_discount_keyboard(
        plan_id, code,
        crypto_on=pm["crypto"],
        card_on=pm["card"],
        crypto_invoice=pm.get("crypto_invoice", False),
        crypto_gateway=pm.get("crypto_gateway", "nowpayments"),
        amount=final_price,
        amount_toman=final_toman,
        plan_name=plan.name,
        wallet_balance_usdt=wallet_usdt,
        wallet_balance_toman=wallet_toman,
    )
    if plan_message_id and plan_chat_id:
        try:
            await message.bot.edit_message_text(
                chat_id=plan_chat_id,
                message_id=plan_message_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=markup,
            )
        except Exception:
            await message.answer(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await message.answer(text, parse_mode="Markdown", reply_markup=markup)


# ──────────────────────────────────────────────
# پرداخت — ایجاد invoice
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay:"))
async def cb_pay(callback: CallbackQuery) -> None:
    await callback.answer()
    tg_user = callback.from_user
    if not tg_user:
        return

    parts = callback.data.split(":")
    plan_id = int(parts[1])
    discount_code = parts[2] if len(parts) > 2 else None

    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
        db_user, _ = await get_or_create_user(
            session, tg_user.id, tg_user.username, tg_user.first_name,
            admin_ids=settings.admin_ids,
        )

        if not plan or not plan.is_active:
            await callback.answer("❌ این پلن در دسترس نیست.", show_alert=True)
            return

        # اعمال تخفیف
        final_price = plan.price_usdt
        dc = None
        if discount_code:
            dc = await get_discount_code(session, discount_code)
            if dc:
                valid, _ = validate_discount(dc)
                if valid:
                    final_price = round(plan.price_usdt * (1 - dc.percent / 100), 2)

        order_id = f"vpn_{tg_user.id}_{plan_id}_{uuid.uuid4().hex[:8]}"
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.invoice_expire_minutes)

        # ایجاد invoice
        try:
            svc = CryptoPaymentService()
            invoice = await svc.create_invoice(
                amount_usdt=final_price,
                order_id=order_id,
                inbound_id=plan_id,
                expire_minutes=settings.invoice_expire_minutes,
            )
        except (PaymentError, PaymentAPIError) as e:
            logger.error(f"خطای پرداخت: {e}")
            if not settings.nowpayments_api_key:
                await _safe_edit_cb(
                    callback,
                    "⚠️ *درگاه پرداخت تنظیم نشده*\n\n"
                    "برای فعال‌سازی پرداخت، `NOWPAYMENTS_API_KEY` را در `.env` وارد کنید.\n"
                    "تا آن زمان با ادمین تماس بگیرید.",
                    parse_mode="Markdown",
                )
            else:
                await _safe_edit_cb(callback, "❌ خطا در ایجاد invoice. لطفاً دوباره تلاش کنید.")
            return

        # ذخیره پرداخت
        await create_payment(
            session=session,
            user_id=db_user.id,
            order_id=order_id,
            amount_usdt=final_price,
            inbound_id=plan_id,
            payment_id=invoice.payment_id,
            pay_address=invoice.pay_address,
            expires_at=invoice.expiration_time,
        )

        # استفاده از کد تخفیف
        if dc:
            await use_discount_code(session, dc.id)

    text = (
        f"💳 *پرداخت اشتراک VPN*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📦 پلن انتخابی: `{plan.name}`\n"
        f"💰 مبلغ قابل پرداخت: `${invoice.pay_amount}`\n\n"
        f"📬 آدرس کیف پول:\n`{invoice.pay_address}`\n\n"
        f"⏰ مهلت پرداخت: `{settings.invoice_expire_minutes} دقیقه`\n\n"
        "پس از واریز، دکمه «بررسی پرداخت» را بزنید."
    )
    await _safe_edit_cb(
        callback,
        text,
        parse_mode="Markdown",
        reply_markup=get_payment_status_keyboard(order_id),
    )


# ──────────────────────────────────────────────
# پرداخت Invoice — صفحه انتخاب ارز NOWPayments
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay_invoice:"))
async def cb_pay_invoice(callback: CallbackQuery) -> None:
    """
    کاربر روی «پرداخت با کریپتو» زد →
    یک Invoice در NOWPayments می‌سازیم و لینک صفحه انتخاب ارز رو می‌دیم.
    کاربر خودش BTC / ETH / هر ارزی انتخاب می‌کنه.
    بعد از پرداخت NOWPayments یک IPN webhook می‌فرسته.
    """
    await callback.answer()
    tg_user = callback.from_user
    if not tg_user:
        return

    parts = callback.data.split(":")
    plan_id = int(parts[1])
    discount_code = parts[2] if len(parts) > 2 else None

    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
        db_user, _ = await get_or_create_user(
            session, tg_user.id, tg_user.username, tg_user.first_name,
            admin_ids=settings.admin_ids,
        )
        if not plan or not plan.is_active:
            await callback.answer("❌ این پلن در دسترس نیست.", show_alert=True)
            return

        final_price = plan.price_usdt
        dc = None
        if discount_code:
            dc = await get_discount_code(session, discount_code)
            if dc:
                valid, _ = validate_discount(dc)
                if valid:
                    final_price = round(plan.price_usdt * (1 - dc.percent / 100), 2)

        order_id = f"inv_{tg_user.id}_{plan_id}_{uuid.uuid4().hex[:8]}"

        try:
            svc = CryptoPaymentService()
            inv = await svc.create_invoice_page(
                amount_usdt=final_price,
                order_id=order_id,
                expire_minutes=settings.invoice_expire_minutes,
            )
        except Exception as e:
            logger.error(f"خطا در ساخت Invoice: {e}")
            await _safe_edit_cb(callback, "❌ خطا در ایجاد لینک پرداخت. لطفاً دوباره تلاش کنید.")
            return

        # ذخیره در دیتابیس — payment_id خالی چون هنوز پرداخت نشده
        await create_payment(
            session=session,
            user_id=db_user.id,
            order_id=order_id,
            amount_usdt=final_price,
            inbound_id=plan_id,
            payment_method="crypto_invoice",
        )
        if dc:
            await use_discount_code(session, dc.id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="🌐 باز کردن صفحه پرداخت", url=inv.invoice_url)
    kb.button(text="🔄 بررسی پرداخت", callback_data=f"check_inv:{order_id}")
    kb.adjust(1)

    await _safe_edit_cb(
        callback,
        f"🌐 *پرداخت با کریپتو*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📦 پلن: `{plan.name}`\n"
        f"💰 مبلغ: `${final_price}`\n\n"
        f"👇 روی دکمه زیر کلیک کنید تا وارد صفحه پرداخت شوید.\n"
        f"در آنجا می‌توانید از بین *بیتکوین، اتریوم، تتر و ۱۰۰+ ارز دیگر* انتخاب کنید.\n\n"
        f"⏰ مهلت پرداخت: `{settings.invoice_expire_minutes} دقیقه`\n"
        f"🔖 شناسه سفارش: `{order_id}`\n\n"
        f"پس از پرداخت، اشتراک *خودکار* فعال می‌شود.",
        parse_mode="Markdown",
        reply_markup=kb.as_markup(),
    )


# ──────────────────────────────────────────────
# 🎁 اشتراک تست
# ──────────────────────────────────────────────

@router.message(F.text == "🎁 اشتراک تست")
async def msg_test_sub(message: Message) -> None:
    tg_user = message.from_user
    if not tg_user:
        return

    # بررسی فعال بودن + خواندن تنظیمات از DB (اولویت بالاتر از .env)
    from database.crud import get_setting
    async with AsyncSessionLocal() as _s:
        _enabled  = await get_setting(_s, "test_sub_enabled",       str(settings.test_subscription_enabled).lower())
        _traffic  = await get_setting(_s, "test_sub_traffic_gb",    str(settings.test_traffic_gb))
        _days     = await get_setting(_s, "test_sub_duration_days", str(settings.test_duration_days))

    if _enabled.lower() != "true":
        await message.answer(
            "⚠️ اشتراک تست در حال حاضر غیرفعال است.\n"
            "برای خرید اشتراک از بخش 🛒 خرید اقدام کنید."
        )
        return

    traffic_gb    = int(_traffic) if _traffic.isdigit() else settings.test_traffic_gb
    duration_days = int(_days)    if _days.isdigit()    else settings.test_duration_days

    async with AsyncSessionLocal() as session:
        used = await has_used_test_subscription(session, tg_user.id)
        if used:
            await message.answer(
                "⚠️ شما قبلاً از اشتراک تست استفاده کرده‌اید.\n"
                "هر آیدی تلگرام فقط یک‌بار می‌تواند اشتراک تست دریافت کند.",
            )
            return

        db_user, _ = await get_or_create_user(
            session, tg_user.id, tg_user.username, tg_user.first_name,
            admin_ids=settings.admin_ids,
        )

        await message.answer("⏳ در حال ایجاد اشتراک تست...")

        try:
            result = await create_new_subscription(
                session=session,
                user_id=db_user.id,
                telegram_id=tg_user.id,
                inbound_id=0,
                traffic_gb=traffic_gb,
                expire_days=duration_days,
                is_gift=True,
            )
            await record_test_subscription(session, tg_user.id)
        except XUIError as e:
            logger.error(f"خطا در ایجاد اشتراک تست: {e}")
            await message.answer("❌ خطا در ایجاد اشتراک. لطفاً بعداً تلاش کنید.")
            return

    await message.answer(
        f"🎁 *اشتراک تست — {traffic_gb}GB / {duration_days} روز*\n"
        "━━━━━━━━━━━━━━━━━━━━",
        parse_mode="Markdown",
    )
    await _send_subscription_to_user(message, result, plan_name="اشتراک تست")
