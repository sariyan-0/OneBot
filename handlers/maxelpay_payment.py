"""
handlers/maxelpay_payment.py — پرداخت از طریق MaxelPay

Flow:
  1. کاربر پلن انتخاب می‌کند → shop.py callback_data: pay_maxel:{plan_id}:{amount}
  2. ایجاد session در MaxelPay → دریافت checkoutUrl
  3. ارسال لینک checkout به کاربر (در مرورگر باز می‌شود)
  4. MaxelPay webhook → webhook_server.py → فعال‌سازی اشتراک
  5. یا polling دستی با دکمه «بررسی پرداخت»
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery
from loguru import logger

from config import settings
from database import AsyncSessionLocal
from database.crud import (
    create_payment,
    get_payment_by_order_id,
    get_or_create_user,
    update_payment_status,
)
from services.payment_config import get_maxelpay_config
from services.maxelpay import MaxelPayClient, MaxelPayError, PAID_STATUSES, FAILED_STATUSES
from services.referral import grant_referral_commission_for_payment
from services.subscription import create_new_subscription, apply_paid_plan_to_subscription
from services.wallet import credit_wallet

router = Router(name="maxelpay_payment")


async def _maxelpay_client() -> MaxelPayClient:
    """ساخت کلاینت MaxelPay از تنظیمات ذخیره‌شده یا .env."""
    runtime = await get_maxelpay_config()
    return MaxelPayClient(
        api_key     = runtime["api_key"],
        webhook_url = runtime["webhook_url"] or settings.maxelpay_webhook_callback_url(),
        success_url = f"https://t.me/{(getattr(settings,'bot_username','') or '').lstrip('@')}",
        cancel_url  = f"https://t.me/{(getattr(settings,'bot_username','') or '').lstrip('@')}",
    )


# ──────────────────────────────────────────────
# Callback: pay_maxel:{plan_id}:{amount}
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay_maxel:"))
async def cb_pay_maxel(callback: CallbackQuery) -> None:
    """ایجاد session MaxelPay و ارسال لینک checkout به کاربر."""
    await callback.answer()
    tg_user = callback.from_user

    parts     = callback.data.split(":")
    plan_id   = int(parts[1]) if len(parts) > 1 else 0
    amount    = float(parts[2]) if len(parts) > 2 else 0.0
    plan_name = parts[3] if len(parts) > 3 else "اشتراک VPN"

    runtime = await get_maxelpay_config()
    if not runtime["api_key"]:
        await callback.message.answer(
            "⚠️ درگاه MaxelPay هنوز تنظیم نشده.\n"
            "ادمین باید <code>MAXELPAY_API_KEY</code> را در تنظیمات وارد کند.",
            parse_mode="HTML",
        )
        return

    processing = await callback.message.answer("⏳ در حال ایجاد فاکتور پرداخت MaxelPay...")

    try:
        async with AsyncSessionLocal() as session:
            db_user, _ = await get_or_create_user(
                session, tg_user.id, tg_user.username, tg_user.first_name,
                admin_ids=settings.admin_ids,
            )

        order_id = f"maxel_{tg_user.id}_{plan_id}_{uuid.uuid4().hex[:8]}"
        client   = await _maxelpay_client()
        pay_session = await client.create_session(
            order_id           = order_id,
            amount_usd         = amount,
            description        = f"{plan_name} — {order_id}",
            customer_name      = tg_user.first_name or "",
            expiration_minutes = 60,
            metadata           = {
                "telegram_id": str(tg_user.id),
                "plan_id":     str(plan_id),
                "plan_name":   plan_name,
            },
        )

        # ── validate checkout_url ────────────────────────────────────────
        # اگه MaxelPay جواب داد ولی checkoutUrl نداد، خطا می‌دهیم
        checkout_url = (pay_session.checkout_url or "").strip()
        logger.info(
            f"MaxelPay session details: "
            f"session_id={pay_session.session_id!r} "
            f"checkout_url={checkout_url!r} "
            f"status={pay_session.status!r}"
        )
        if not checkout_url or not checkout_url.startswith("http"):
            logger.error(
                f"MaxelPay returned empty/invalid checkout_url for order {order_id}. "
                f"Full session: session_id={pay_session.session_id!r} status={pay_session.status!r}"
            )
            await processing.edit_text(
                "❌ خطا در دریافت لینک پرداخت از MaxelPay.\n"
                f"<code>session_id={pay_session.session_id or 'empty'}</code>\n\n"
                "لطفاً از API Key و Webhook URL در .env مطمئن شوید.",
                parse_mode="HTML",
            )
            return
        # ────────────────────────────────────────────────────────────────

        async with AsyncSessionLocal() as session:
            await create_payment(
                session        = session,
                user_id        = db_user.id,
                order_id       = order_id,
                amount_usdt    = amount,
                inbound_id     = plan_id,
                payment_id     = pay_session.session_id,
                payment_method = "maxelpay",
            )

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        kb.button(text="💳 پرداخت در MaxelPay",   url=checkout_url)
        kb.button(text="🔄 بررسی پرداخت",         callback_data=f"check_maxel:{order_id}")
        kb.adjust(1)

        await processing.delete()
        await callback.message.answer(
            f"💳 <b>فاکتور MaxelPay</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📦 پلن: <b>{plan_name}</b>\n"
            f"💰 مبلغ: <b>{amount:.2f} دلار</b>\n"
            f"🔖 سفارش: <code>{order_id}</code>\n\n"
            "روی دکمه زیر کلیک کنید و پرداخت را در MaxelPay انجام دهید.\n"
            "بعد از پرداخت «بررسی پرداخت» را بزنید.",
            parse_mode="HTML",
            reply_markup=kb.as_markup(),
        )

    except MaxelPayError as e:
        logger.error(f"MaxelPay error for user {tg_user.id}: {e}")
        try:
            await processing.edit_text(
                f"❌ خطا در ایجاد فاکتور MaxelPay:\n<code>{e}</code>",
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(
                f"❌ خطا در ایجاد فاکتور MaxelPay:\n<code>{e}</code>",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.exception(f"Unexpected MaxelPay error for user {tg_user.id}: {e}")
        try:
            await processing.edit_text("❌ خطای غیرمنتظره. لطفاً با پشتیبانی تماس بگیرید.")
        except Exception:
            await callback.message.answer("❌ خطای غیرمنتظره. لطفاً با پشتیبانی تماس بگیرید.")


@router.callback_query(F.data.startswith("submaxel:"))
async def cb_sub_pay_maxel(callback: CallbackQuery) -> None:
    await callback.answer()
    tg_user = callback.from_user
    if not tg_user:
        return

    parts = callback.data.split(":")
    flow = parts[1] if len(parts) > 1 else "renew"
    target_sub_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    plan_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    amount = float(parts[4]) if len(parts) > 4 else 0.0

    runtime = await get_maxelpay_config()
    if not runtime["api_key"]:
        await callback.message.answer(
            "⚠️ درگاه MaxelPay هنوز تنظیم نشده.\n"
            "ادمین باید <code>MAXELPAY_API_KEY</code> را در تنظیمات وارد کند.",
            parse_mode="HTML",
        )
        return

    processing = await callback.message.answer("⏳ در حال ایجاد فاکتور پرداخت MaxelPay...")
    try:
        async with AsyncSessionLocal() as session:
            db_user, _ = await get_or_create_user(
                session, tg_user.id, tg_user.username, tg_user.first_name,
                admin_ids=settings.admin_ids,
            )

        order_id = f"{flow}_{target_sub_id}_{plan_id}_{uuid.uuid4().hex[:8]}"
        client = await _maxelpay_client()
        pay_session = await client.create_session(
            order_id=order_id,
            amount_usd=amount,
            description=f"subscription update — {order_id}",
            customer_name=tg_user.first_name or "",
            expiration_minutes=60,
            metadata={
                "telegram_id": str(tg_user.id),
                "plan_id": str(plan_id),
                "flow": flow,
                "sub_id": str(target_sub_id),
            },
        )

        checkout_url = (pay_session.checkout_url or "").strip()
        if not checkout_url or not checkout_url.startswith("http"):
            await processing.edit_text("❌ خطا در دریافت لینک پرداخت از MaxelPay.", parse_mode="HTML")
            return

        async with AsyncSessionLocal() as session:
            await create_payment(
                session=session,
                user_id=db_user.id,
                order_id=order_id,
                amount_usdt=amount,
                inbound_id=plan_id,
                payment_id=pay_session.session_id,
                payment_method="maxelpay",
            )

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        kb = InlineKeyboardBuilder()
        kb.button(text="💳 پرداخت در MaxelPay", url=checkout_url)
        kb.button(text="🔄 بررسی پرداخت", callback_data=f"check_maxel:{order_id}")
        kb.adjust(1)
        await processing.delete()
        await callback.message.answer(
            f"💳 <b>فاکتور MaxelPay</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔖 سفارش: <code>{order_id}</code>\n"
            f"💰 مبلغ: <b>{amount:.2f} دلار</b>\n\n"
            "بعد از پرداخت «بررسی پرداخت» را بزنید.",
            parse_mode="HTML",
            reply_markup=kb.as_markup(),
        )
    except Exception as e:
        logger.exception(f"MaxelPay renew/change error: {e}")
        try:
            await processing.edit_text("❌ خطای غیرمنتظره. لطفاً با پشتیبانی تماس بگیرید.")
        except Exception:
            await callback.message.answer("❌ خطای غیرمنتظره. لطفاً با پشتیبانی تماس بگیرید.")


# ──────────────────────────────────────────────
# Callback: check_maxel:{order_id} — polling دستی
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("check_maxel:"))
async def cb_check_maxel(callback: CallbackQuery) -> None:
    """بررسی وضعیت پرداخت MaxelPay توسط کاربر."""
    await callback.answer("🔄 در حال بررسی...")
    order_id = callback.data.split(":", 1)[1]

    async with AsyncSessionLocal() as session:
        payment = await get_payment_by_order_id(session, order_id)

    if not payment:
        await callback.answer("❌ فاکتور پیدا نشد.", show_alert=True)
        return

    if payment.status in ("confirmed", "finished"):
        await callback.answer("✅ پرداخت قبلاً تأیید شده است.", show_alert=True)
        return

    if not payment.payment_id:
        await callback.answer("⏳ هنوز session ایجاد نشده.", show_alert=True)
        return

    try:
        client = _maxelpay_client()
        status_obj = await client.get_status(payment.payment_id)

        if status_obj.is_paid:
            await _confirm_maxel_and_create_sub(callback, payment, order_id)
        elif status_obj.is_failed:
            async with AsyncSessionLocal() as session:
                await update_payment_status(session, payment.id, status_obj.status.lower())
            await callback.answer(
                f"❌ پرداخت ناموفق بود (وضعیت: {status_obj.status}).",
                show_alert=True,
            )
        else:
            await callback.answer(
                f"⏳ وضعیت: {status_obj.status}\nلطفاً بعد از پرداخت مجدداً بررسی کنید.",
                show_alert=True,
            )
    except Exception as e:
        logger.error(f"خطا در بررسی MaxelPay {order_id}: {e}")
        await callback.answer("⚠️ خطا در بررسی. لطفاً دقایقی دیگر امتحان کنید.", show_alert=True)


# ──────────────────────────────────────────────
# تأیید پرداخت + ایجاد اشتراک
# ──────────────────────────────────────────────

async def _confirm_maxel_and_create_sub(callback: CallbackQuery, payment, order_id: str) -> None:
    """پس از تأیید پرداخت MaxelPay، اشتراک ایجاد می‌کند."""
    from aiogram.types import BufferedInputFile
    tg_user = callback.from_user

    processing = await callback.message.answer("✅ پرداخت تأیید شد!\n⏳ در حال ایجاد کانفیگ VPN...")

    try:
        async with AsyncSessionLocal() as session:
            db_user, _ = await get_or_create_user(
                session, tg_user.id, tg_user.username, tg_user.first_name,
                admin_ids=settings.admin_ids,
            )
            if order_id.startswith("wallet_"):
                credited = await credit_wallet(session, db_user.id, float(payment.amount_usdt), currency="usd")
                await update_payment_status(session, payment.id, "confirmed")
                result = None
                await grant_referral_commission_for_payment(session, payment)
            elif order_id.startswith(("renew_", "change_")):
                parts = order_id.split("_", 3)
                action = parts[0]
                target_sub_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                plan_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else getattr(payment, "inbound_id", 0)
                result = await apply_paid_plan_to_subscription(
                    session=session,
                    subscription_id=target_sub_id,
                    plan_id=plan_id,
                    telegram_id=tg_user.id,
                    action=action,
                )
                await update_payment_status(session, payment.id, "confirmed", result.subscription.id)
                await grant_referral_commission_for_payment(session, payment)
            else:
                result = await create_new_subscription(
                    session    = session,
                    user_id    = db_user.id,
                    telegram_id= tg_user.id,
                    inbound_id = 0,
                    plan_id    = getattr(payment, "inbound_id", 0),
                )
                await update_payment_status(session, payment.id, "confirmed", result.subscription.id)
                await grant_referral_commission_for_payment(session, payment)

        if order_id.startswith("wallet_"):
            await callback.message.answer(
                f"💼 <b>شارژ کیف پول شما تأیید شد!</b>\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🔖 سفارش: <code>{order_id}</code>\n"
                f"💰 مبلغ افزوده‌شده: <b>{float(payment.amount_usdt):.2f} دلار</b>\n"
                f"💳 موجودی جدید: <b>{float(credited):.2f} دلار</b>",
                parse_mode="HTML",
            )
        else:
            qr_file = BufferedInputFile(result.qr_bytes, "vpn_qr.png")
            await callback.message.answer_photo(
                photo   = qr_file,
                caption = (
                    f"🎉 <b>اشتراک آماده شد!</b>\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"📧 شناسه: <code>{result.email}</code>\n\n"
                    f"🔗 <b>لینک اشتراک:</b>\n<code>{result.sub_link}</code>"
                ),
                parse_mode="HTML",
            )
        await processing.delete()

    except Exception as e:
        logger.exception(f"خطا در ایجاد اشتراک بعد از MaxelPay {order_id}: {e}")
        await processing.edit_text(
            f"✅ پرداخت تأیید شد اما خطایی رخ داد.\n"
            f"شناسه سفارش: <code>{order_id}</code>\n"
            f"با پشتیبانی تماس بگیرید.",
            parse_mode="HTML",
        )
