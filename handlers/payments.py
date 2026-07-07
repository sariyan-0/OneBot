"""
handlers/payments.py — بررسی وضعیت پرداخت کریپتو

توجه: handler اصلی ایجاد invoice (pay:) در shop.py قرار دارد
      تا کد تخفیف و روش پرداخت به درستی اعمال شوند.
      این فایل فقط check_payment: و _confirm_payment را مدیریت می‌کند.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery
from loguru import logger

from config import settings
from database import AsyncSessionLocal
from database.crud import (
    create_payment,
    get_payment_by_order_id,
    get_payment_by_payment_id,
    update_payment_status,
    get_or_create_user,
)
from keyboards.plans import get_payment_status_keyboard
from services.payments import PaymentError, crypto_payment_service
from services.referral import grant_referral_commission_for_payment
from services.subscription import create_new_subscription, apply_paid_plan_to_subscription
from services.wallet import credit_wallet, debit_wallet, wallet_balance, wallet_balance_toman
from utils.qrcode_gen import generate_qr_code

router = Router(name="payments")


async def _replace_callback_message(callback: CallbackQuery, text: str, **kwargs) -> None:
    message = callback.message
    if not message:
        return
    try:
        if message.photo or message.document:  # type: ignore[attr-defined]
            await message.edit_caption(caption=text, **kwargs)  # type: ignore[attr-defined]
        else:
            await message.edit_text(text, **kwargs)  # type: ignore[attr-defined]
    except Exception:
        await message.answer(text, **kwargs)  # type: ignore[attr-defined]


def _parse_subscription_payment_callback(data: str) -> tuple[str, int, int, str]:
    """
    پارس callback های renew/change:
      subpay:{flow}:{sub_id}:{plan_id}
      subinvoice:{flow}:{sub_id}:{plan_id}
    خروجی: flow, sub_id, plan_id, discount_code
    """
    parts = data.split(":")
    flow = parts[1] if len(parts) > 1 else "renew"
    sub_id = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 0
    plan_id = int(parts[3]) if len(parts) > 3 and str(parts[3]).isdigit() else 0
    discount_code = parts[4] if len(parts) > 4 else ""
    return flow, sub_id, plan_id, discount_code


def _parse_wallet_payment_callback(data: str) -> tuple[str, str, int, int, float, int]:
    parts = data.split(":")
    if parts and parts[0] == "walletpay_select":
        flow = parts[1] if len(parts) > 1 else "new"
        sub_id = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 0
        plan_id = int(parts[3]) if len(parts) > 3 and str(parts[3]).isdigit() else 0
        amount_usd = float(parts[4]) if len(parts) > 4 else 0.0
        amount_toman = int(float(parts[5])) if len(parts) > 5 else 0
        return "select", flow, sub_id, plan_id, amount_usd, amount_toman
    if len(parts) >= 7 and parts[1] in {"usd", "toman"}:
        currency = parts[1]
        flow = parts[2] if len(parts) > 2 else "new"
        sub_id = int(parts[3]) if len(parts) > 3 and str(parts[3]).isdigit() else 0
        plan_id = int(parts[4]) if len(parts) > 4 and str(parts[4]).isdigit() else 0
        amount_usd = float(parts[5]) if len(parts) > 5 else 0.0
        amount_toman = int(float(parts[6])) if len(parts) > 6 else 0
        return currency, flow, sub_id, plan_id, amount_usd, amount_toman
    flow = parts[1] if len(parts) > 1 else "new"
    sub_id = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 0
    plan_id = int(parts[3]) if len(parts) > 3 and str(parts[3]).isdigit() else 0
    amount = float(parts[4]) if len(parts) > 4 else 0.0
    return "usd", flow, sub_id, plan_id, amount, 0


async def _wallet_purchase_by_callback(
    callback: CallbackQuery,
    currency: str,
    flow: str,
    sub_id: int,
    plan_id: int,
    amount_usd: float,
    amount_toman: int,
) -> None:
    tg_user = callback.from_user
    if not tg_user:
        return

    async with AsyncSessionLocal() as session:
        db_user, _ = await get_or_create_user(
            session=session,
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            admin_ids=settings.admin_ids,
        )
        if currency == "toman":
            current_balance = await wallet_balance_toman(session, db_user.id)
            if current_balance < amount_toman:
                await callback.answer("موجودی کیف پول تومان کافی نیست.", show_alert=True)
                return
        else:
            current_balance = await wallet_balance(session, db_user.id)
            if current_balance < amount_usd:
                await callback.answer("موجودی کیف پول دلاری کافی نیست.", show_alert=True)
                return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(
        text="✅ تأیید",
        callback_data=f"walletpay_confirm:{currency}:{flow}:{sub_id}:{plan_id}:{amount_usd:.2f}:{amount_toman}",
    )
    kb.button(text="❌ انصراف", callback_data=f"walletpay_cancel:{flow}:{sub_id}:{plan_id}")
    kb.adjust(2)
    if currency == "toman":
        amount_lines = f"  • <b>{amount_toman:,} تومان</b>\n"
    else:
        amount_lines = f"  • <b>${amount_usd:.2f}</b>\n"
    await _replace_callback_message(
        callback,
        f"💼 <b>پرداخت از کیف پول {('تومان' if currency == 'toman' else 'دلار')}</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"💰 مبلغ:\n"
        f"{amount_lines}"
        "با تأیید، مبلغ از کیف پول شما کسر می‌شود و سفارش ثبت می‌شود.",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )


# ──────────────────────────────────────────────
# Callback: pay_crypto:{plan_id} — ایجاد invoice
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("pay_crypto:"))
async def cb_pay_crypto(callback: CallbackQuery) -> None:
    """
    ایجاد invoice پرداخت کریپتو:
      1. دریافت/ایجاد کاربر
      2. ایجاد invoice از NOWPayments
      3. ذخیره در دیتابیس
      4. نمایش QR Code آدرس والت + آدرس + زمان انقضا
    """
    await callback.answer()
    tg_user = callback.from_user
    if not tg_user:
        return

    parts = callback.data.split(":")  # type: ignore[union-attr]
    plan_id   = int(parts[1]) if len(parts) > 1 else 0
    amount    = float(parts[2]) if len(parts) > 2 else settings.plan_price_usdt

    processing_msg = await callback.message.answer(  # type: ignore[union-attr]
        "⏳ در حال ایجاد فاکتور پرداخت...\nلطفاً چند لحظه صبر کنید."
    )

    try:
        async with AsyncSessionLocal() as session:
            db_user, _ = await get_or_create_user(
                session=session,
                telegram_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                admin_ids=settings.admin_ids,
            )

            order_id = f"crypto_{tg_user.id}_{plan_id}_{uuid.uuid4().hex[:8]}"

            invoice = await crypto_payment_service.create_invoice(
                amount_usdt=amount,
                order_id=order_id,
                inbound_id=plan_id,
                expire_minutes=settings.invoice_expire_minutes,
            )

            await create_payment(
                session=session,
                user_id=db_user.id,
                order_id=order_id,
                amount_usdt=amount,
                inbound_id=plan_id,
                payment_id=invoice.payment_id,
                pay_address=invoice.pay_address,
                pay_currency=invoice.pay_currency,
                expires_at=invoice.expiration_time,
            )

        qr_bytes = await generate_qr_code(invoice.qr_data)
        qr_file  = BufferedInputFile(file=qr_bytes, filename="payment_qr.png")

        expire_str   = invoice.expiration_time.strftime("%H:%M")
        sandbox_note = "\n\n⚠️ *حالت آزمایشی* — پرداخت واقعی نیست." if not settings.nowpayments_api_key else ""

        caption = (
            "💳 *فاکتور پرداخت*\n"
            "━━━━━━━━━━━━━━━\n"
            f"💰 مبلغ: `{invoice.pay_amount:.4f}`\n"
            f"🌐 شبکه: `TRON — TRC-20`\n\n"
            f"📋 *آدرس کیف پول:*\n`{invoice.pay_address}`\n\n"
            f"⏰ مهلت پرداخت تا: `{expire_str}`\n"
            f"🔖 شناسه سفارش: `{order_id}`\n\n"
            "روش پرداخت:\n"
            "۱. QR کد را اسکن کنید\n"
            "۲. یا آدرس بالا را کپی کنید\n"
            "۳. مبلغ *دقیق* را ارسال کنید\n\n"
            "پس از تأیید شبکه، اشتراک خودکار فعال می‌شود."
            f"{sandbox_note}"
        )

        await callback.message.answer_photo(  # type: ignore[union-attr]
            photo=qr_file,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=get_payment_status_keyboard(order_id),
        )
        await processing_msg.delete()

    except PaymentError as e:
        logger.error(f"خطای پرداخت برای user {tg_user.id}: {e}")
        await processing_msg.edit_text(
            f"❌ خطا در ایجاد فاکتور:\n`{e}`\n\nلطفاً مجدداً تلاش کنید.",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.exception(f"خطای ناشناخته پرداخت: {e}")
        await processing_msg.edit_text(
            "❌ خطای غیرمنتظره رخ داد. لطفاً با پشتیبانی تماس بگیرید."
        )


@router.callback_query(F.data.startswith("subpay:"))
async def cb_sub_pay_crypto(callback: CallbackQuery) -> None:
    await callback.answer()
    tg_user = callback.from_user
    if not tg_user:
        return

    flow, sub_id, plan_id, _discount = _parse_subscription_payment_callback(callback.data)
    processing_msg = await callback.message.answer("⏳ در حال ایجاد فاکتور پرداخت...")

    try:
        async with AsyncSessionLocal() as session:
            from database.crud import get_plan
            plan = await get_plan(session, plan_id)
            amount = float(plan.price_usdt) if plan else 0.0
            db_user, _ = await get_or_create_user(
                session=session,
                telegram_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                admin_ids=settings.admin_ids,
            )
            order_id = f"{flow}_{sub_id}_{plan_id}_{uuid.uuid4().hex[:8]}"
            invoice = await crypto_payment_service.create_invoice(
                amount_usdt=amount,
                order_id=order_id,
                inbound_id=plan_id,
                expire_minutes=settings.invoice_expire_minutes,
            )
            await create_payment(
                session=session,
                user_id=db_user.id,
                order_id=order_id,
                amount_usdt=amount,
                inbound_id=plan_id,
                payment_id=invoice.payment_id,
                pay_address=invoice.pay_address,
                pay_currency=invoice.pay_currency,
                expires_at=invoice.expiration_time,
            )

        qr_bytes = await generate_qr_code(invoice.qr_data)
        qr_file = BufferedInputFile(file=qr_bytes, filename="payment_qr.png")
        await callback.message.answer_photo(
            photo=qr_file,
            caption=(
                "💳 *فاکتور پرداخت*\n"
                "━━━━━━━━━━━━━━━\n"
                f"🔖 سفارش: `{order_id}`\n\n"
                "پس از تأیید شبکه، اشتراک به‌روزرسانی می‌شود."
            ),
            parse_mode="Markdown",
            reply_markup=get_payment_status_keyboard(order_id),
        )
        await processing_msg.delete()
    except Exception as e:
        logger.error(f"خطای renew/change crypto: {e}")
        await processing_msg.edit_text(f"❌ خطا در ایجاد فاکتور:\n`{e}`", parse_mode="Markdown")


@router.callback_query(F.data.startswith("subinvoice:"))
async def cb_sub_pay_invoice(callback: CallbackQuery) -> None:
    await callback.answer()
    tg_user = callback.from_user
    if not tg_user:
        return
    flow, sub_id, plan_id, _discount = _parse_subscription_payment_callback(callback.data)

    async with AsyncSessionLocal() as session:
        from database.crud import get_plan
        plan = await get_plan(session, plan_id)
        amount = float(plan.price_usdt) if plan else 0.0
        db_user, _ = await get_or_create_user(
            session=session,
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            admin_ids=settings.admin_ids,
        )

    order_id = f"{flow}_{sub_id}_{plan_id}_{uuid.uuid4().hex[:8]}"
    try:
        svc = CryptoPaymentService()
        inv = await svc.create_invoice_page(
            amount_usdt=amount,
            order_id=order_id,
            expire_minutes=settings.invoice_expire_minutes,
        )
    except Exception as e:
        logger.error(f"خطا در ساخت Invoice renew/change: {e}")
        await _replace_callback_message(callback, "❌ خطا در ایجاد لینک پرداخت. لطفاً دوباره تلاش کنید.")
        return

    async with AsyncSessionLocal() as session:
        await create_payment(
            session=session,
            user_id=db_user.id,
            order_id=order_id,
            amount_usdt=amount,
            inbound_id=plan_id,
            payment_method="crypto_invoice",
        )

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    kb.button(text="🌐 باز کردن صفحه پرداخت", url=inv.invoice_url)
    kb.button(text="🔄 بررسی پرداخت", callback_data=f"check_inv:{order_id}")
    kb.adjust(1)

    await _replace_callback_message(
        callback,
        f"🌐 *پرداخت اشتراک*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔖 شناسه سفارش: `{order_id}`\n\n"
        f"بعد از پرداخت، اشتراک به‌روزرسانی می‌شود.",
        parse_mode="Markdown",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("walletpay:"))
async def cb_wallet_pay(callback: CallbackQuery) -> None:
    await callback.answer()
    currency, flow, sub_id, plan_id, amount_usd, amount_toman = _parse_wallet_payment_callback(callback.data)
    if currency == "select":
        await cb_wallet_pay_select(callback)
        return
    await _wallet_purchase_by_callback(callback, currency, flow, sub_id, plan_id, amount_usd, amount_toman)


@router.callback_query(F.data.startswith("walletpay_select:"))
async def cb_wallet_pay_select(callback: CallbackQuery) -> None:
    await callback.answer()
    currency, flow, sub_id, plan_id, amount_usd, amount_toman = _parse_wallet_payment_callback(callback.data)
    if currency != "select":
        await _wallet_purchase_by_callback(callback, currency, flow, sub_id, plan_id, amount_usd, amount_toman)
        return

    async with AsyncSessionLocal() as session:
        db_user, _ = await get_or_create_user(
            session=session,
            telegram_id=callback.from_user.id if callback.from_user else 0,
            username=callback.from_user.username if callback.from_user else None,
            first_name=callback.from_user.first_name if callback.from_user else None,
            admin_ids=settings.admin_ids,
        )
        wallet_usdt = await wallet_balance(session, db_user.id)
        wallet_toman = await wallet_balance_toman(session, db_user.id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    if wallet_usdt >= amount_usd and amount_usd > 0:
        kb.button(
            text=f"💼 دلار (${amount_usd:.2f})",
            callback_data=f"walletpay:usd:{flow}:{sub_id}:{plan_id}:{amount_usd:.2f}:{amount_toman}",
        )
    if wallet_toman >= amount_toman and amount_toman > 0:
        kb.button(
            text=f"💼 تومان ({amount_toman:,} تومان)",
            callback_data=f"walletpay:toman:{flow}:{sub_id}:{plan_id}:{amount_usd:.2f}:{amount_toman}",
        )
    kb.button(text="❌ انصراف", callback_data=f"walletpay_cancel:{flow}:{sub_id}:{plan_id}")
    kb.adjust(1)

    await _replace_callback_message(
        callback,
        "💼 <b>کدام کیف پول را می‌خواهید استفاده کنید؟</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"💵 قیمت دلاری: <b>${amount_usd:.2f}</b>\n"
        f"💳 قیمت تومانی: <b>{amount_toman:,} تومان</b>\n",
        parse_mode="HTML",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("walletpay_cancel:"))
async def cb_wallet_pay_cancel(callback: CallbackQuery) -> None:
    await callback.answer("❌ انصراف شد.")


@router.callback_query(F.data.startswith("walletpay_confirm:"))
async def cb_wallet_pay_confirm(callback: CallbackQuery) -> None:
    await callback.answer()
    tg_user = callback.from_user
    if not tg_user:
        return

    parts = callback.data.split(":")
    if len(parts) >= 7 and parts[1] in {"usd", "toman"}:
        currency = parts[1]
        flow = parts[2] if len(parts) > 2 else "new"
        sub_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        plan_id = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        amount_usd = float(parts[5]) if len(parts) > 5 else 0.0
        amount_toman = int(float(parts[6])) if len(parts) > 6 else 0
    else:
        currency = "usd"
        flow = parts[1] if len(parts) > 1 else "new"
        sub_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        plan_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        amount_usd = float(parts[4]) if len(parts) > 4 else 0.0
        amount_toman = 0

    processing_msg = await callback.message.answer("⏳ در حال اعمال پرداخت از کیف پول...")
    debited = False

    try:
        async with AsyncSessionLocal() as session:
            db_user, _ = await get_or_create_user(
                session=session,
                telegram_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                admin_ids=settings.admin_ids,
            )
            if currency == "toman":
                current_balance = await wallet_balance_toman(session, db_user.id)
                if current_balance < amount_toman:
                    await processing_msg.edit_text("❌ موجودی کیف پول تومان کافی نیست.")
                    return
                new_balance = await debit_wallet(session, db_user.id, amount_toman, currency="toman")
            else:
                current_balance = await wallet_balance(session, db_user.id)
                if current_balance < amount_usd:
                    await processing_msg.edit_text("❌ موجودی کیف پول دلاری کافی نیست.")
                    return
                new_balance = await debit_wallet(session, db_user.id, amount_usd, currency="usd")
            debited = True

            if flow != "new":
                action = flow
                result = await apply_paid_plan_to_subscription(
                    session=session,
                    subscription_id=sub_id,
                    plan_id=plan_id,
                    telegram_id=tg_user.id,
                    action=action,
                )
            else:
                result = await create_new_subscription(
                    session=session,
                    user_id=db_user.id,
                    telegram_id=tg_user.id,
                    inbound_id=0,
                    plan_id=plan_id,
                )

            payment = await create_payment(
                session=session,
                user_id=db_user.id,
                order_id=f"wallet_{currency}_{flow}_{sub_id}_{plan_id}_{uuid.uuid4().hex[:8]}",
                amount_usdt=amount_usd,
                inbound_id=plan_id,
                payment_method=f"wallet_{currency}",
                amount_rial=amount_toman * 10 if currency == "toman" else None,
            )
            await update_payment_status(session, payment.id, "confirmed", getattr(result.subscription, "id", None))

        if result and getattr(result, "qr_bytes", None):
            qr_file = BufferedInputFile(file=result.qr_bytes, filename="wallet_sub_qr.png")
            if currency == "toman":
                amount_lines = f"  • <b>{amount_toman:,} تومان</b>\n"
                balance_lines = f"  • <b>{int(new_balance):,} تومان</b>"
            else:
                amount_lines = f"  • <b>${amount_usd:.2f}</b>\n"
                balance_lines = f"  • <b>${float(new_balance):.2f}</b>"
            await callback.message.answer_photo(
                photo=qr_file,
                caption=(
                    "🎉 <b>پرداخت از کیف پول تأیید شد!</b>\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"💰 مبلغ کسرشده:\n"
                    f"{amount_lines}"
                    f"💳 موجودی جدید:\n"
                    f"{balance_lines}\n\n"
                    f"🔗 <b>لینک اشتراک:</b>\n<code>{result.sub_link}</code>"
                ),
                parse_mode="HTML",
            )
        else:
            await callback.message.answer(
                "✅ پرداخت از کیف پول تأیید شد.",
                parse_mode="HTML",
            )
        await processing_msg.delete()
    except Exception as exc:
        logger.error(f"خطا در پرداخت از کیف پول: {exc}")
        if debited:
            try:
                async with AsyncSessionLocal() as session:
                    db_user, _ = await get_or_create_user(
                        session=session,
                        telegram_id=tg_user.id,
                        username=tg_user.username,
                        first_name=tg_user.first_name,
                        admin_ids=settings.admin_ids,
                    )
                    if currency == "toman":
                        await credit_wallet(session, db_user.id, amount_toman, currency="toman")
                    else:
                        await credit_wallet(session, db_user.id, amount_usd, currency="usd")
            except Exception as refund_exc:
                logger.error(f"خطا در بازگردانی موجودی کیف پول: {refund_exc}")
        await processing_msg.edit_text("❌ خطا در پردازش پرداخت از کیف پول.")


# ──────────────────────────────────────────────
# Callback: check_payment:{order_id} — بررسی وضعیت
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("check_payment:"))
async def cb_check_payment(callback: CallbackQuery) -> None:
    """بررسی وضعیت پرداخت توسط کاربر (polling دستی)."""
    await callback.answer("🔄 در حال بررسی...")
    order_id = callback.data.split(":", 1)[1]  # type: ignore[union-attr]
    tg_user = callback.from_user

    async with AsyncSessionLocal() as session:
        payment = await get_payment_by_order_id(session, order_id)

    if not payment:
        await callback.answer("❌ فاکتور پیدا نشد.", show_alert=True)
        return

    # بررسی انقضا
    if payment.expires_at and datetime.now(timezone.utc) > payment.expires_at:
        async with AsyncSessionLocal() as session:
            await update_payment_status(session, payment.id, "expired")
        await callback.answer("⏰ فاکتور منقضی شده. لطفاً مجدداً خرید کنید.", show_alert=True)
        return

    # اگر قبلاً تأیید شده
    if payment.status in ("confirmed", "finished"):
        await callback.answer("✅ پرداخت قبلاً تأیید شده است.", show_alert=True)
        return

    # بررسی از NOWPayments
    if not payment.payment_id:
        await callback.answer("⏳ هنوز پرداختی دریافت نشده.", show_alert=True)
        return

    try:
        ps = await crypto_payment_service.get_payment_status(payment.payment_id)

        if crypto_payment_service.is_paid(ps.status):
            await _confirm_payment_and_create_sub(callback, payment, order_id)
        elif crypto_payment_service.is_failed(ps.status):
            async with AsyncSessionLocal() as session:
                await update_payment_status(session, payment.id, ps.status)
            await callback.answer(
                f"❌ پرداخت ناموفق بود (وضعیت: {ps.status}).", show_alert=True
            )
        else:
            await callback.answer(
                f"⏳ وضعیت: {ps.status}\nلطفاً صبر کنید و مجدداً بررسی کنید.",
                show_alert=True,
            )
    except Exception as e:
        logger.error(f"خطا در بررسی وضعیت پرداخت {order_id}: {e}")
        await callback.answer("⚠️ خطا در بررسی وضعیت. لطفاً دقایقی دیگر امتحان کنید.", show_alert=True)


# ──────────────────────────────────────────────
# Callback: check_inv:{order_id} — بررسی Invoice
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("check_inv:"))
async def cb_check_invoice(callback: CallbackQuery) -> None:
    """
    بررسی وضعیت پرداخت Invoice (صفحه انتخاب ارز).
    IPN معمولاً خودکار اشتراک می‌سازه — این دکمه fallback دستی هست.
    """
    await callback.answer("🔄 در حال بررسی...")
    order_id = callback.data.split(":", 1)[1]

    async with AsyncSessionLocal() as session:
        payment = await get_payment_by_order_id(session, order_id)

    if not payment:
        await callback.answer("❌ سفارش پیدا نشد.", show_alert=True)
        return

    if payment.status in ("confirmed", "finished"):
        await callback.answer("✅ پرداخت قبلاً تأیید و اشتراک فعال شده.", show_alert=True)
        return

    # Invoice ها payment_id ندارند تا کاربر پرداخت نکنه
    # → فقط وضعیت DB رو چک می‌کنیم
    await callback.answer(
        "⏳ پرداخت هنوز تأیید نشده.\n"
        "بعد از پرداخت در صفحه NOWPayments، اشتراک خودکار فعال می‌شود.\n"
        "معمولاً تا چند دقیقه طول می‌کشد.",
        show_alert=True,
    )


# ──────────────────────────────────────────────
# تأیید پرداخت + ایجاد اشتراک
# ──────────────────────────────────────────────

async def _confirm_payment_and_create_sub(
    callback: CallbackQuery,
    payment: object,
    order_id: str,
) -> None:
    """پس از تأیید پرداخت، اشتراک ایجاد می‌کند."""
    tg_user = callback.from_user
    if not tg_user:
        return

    processing_msg = await callback.message.answer(  # type: ignore[union-attr]
        "✅ پرداخت تأیید شد!\n⏳ در حال ایجاد کانفیگ VPN..."
    )

    try:
        async with AsyncSessionLocal() as session:
            db_user, _ = await get_or_create_user(
                session=session,
                telegram_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
                admin_ids=settings.admin_ids,
            )
            if order_id.startswith("wallet_"):
                payment_method = str(getattr(payment, "payment_method", "")).lower()
                wallet_currency = "toman" if "toman" in order_id or payment_method.endswith("wallet_toman") else "usd"
                if wallet_currency == "toman":
                    wallet_amount = float(getattr(payment, "amount_rial", 0) or 0)
                    credited = await credit_wallet(session, db_user.id, wallet_amount / 10 if wallet_amount else float(getattr(payment, "amount_usdt", 0.0)), currency="toman")
                else:
                    credited = await credit_wallet(session, db_user.id, float(getattr(payment, "amount_usdt", 0.0)), currency="usd")
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
                    session=session,
                    user_id=db_user.id,
                    telegram_id=tg_user.id,
                    inbound_id=0,  # 0 = انتخاب خودکار بر اساس پلن یا اینباندهای عمومی
                    plan_id=getattr(payment, "inbound_id", 0),  # inbound_id در جدول payment = plan_id
                )

                await update_payment_status(
                    session, payment.id, "confirmed", result.subscription.id  # type: ignore[attr-defined]
                )
                await grant_referral_commission_for_payment(session, payment)

        if order_id.startswith("wallet_"):
            payment_method = str(getattr(payment, "payment_method", "")).lower()
            wallet_currency = "toman" if "toman" in order_id or payment_method.endswith("wallet_toman") else "usd"
            if wallet_currency == "toman":
                added_amount = float(getattr(payment, "amount_rial", 0) or 0) / 10 if float(getattr(payment, "amount_rial", 0) or 0) else float(getattr(payment, "amount_usdt", 0.0))
                added_lines = f"  • <b>{int(round(added_amount)):,} تومان</b>\n"
                credit_lines = f"  • <b>{int(round(float(credited) if credited is not None else 0)):,} تومان</b>\n"
            else:
                added_amount = float(getattr(payment, "amount_usdt", 0.0))
                added_lines = f"  • <b>${added_amount:.2f}</b>\n"
                credit_lines = f"  • <b>${float(credited):.2f}</b>\n"
            text = (
                "💼 <b>شارژ کیف پول شما تأیید شد!</b>\n"
                "━━━━━━━━━━━━━━━\n"
                f"🔖 سفارش: <code>{order_id}</code>\n"
                f"💰 مبلغ افزوده‌شده:\n"
                f"{added_lines}"
                f"💳 موجودی جدید:\n"
                f"{credit_lines}"
            )
            await callback.message.answer(text, parse_mode="HTML")
            await processing_msg.delete()
            return

        # ارسال QR Code کانفیگ
        qr_file = BufferedInputFile(file=result.qr_bytes, filename="vpn_qrcode.png")
        ip_line = f"📡 محدودیت دستگاه: *{result.limit_ip} دستگاه همزمان*\n" if result.limit_ip else ""
        caption = (
            "🎉 *اشتراک شما آماده شد!*\n"
            "━━━━━━━━━━━━━━━\n"
            f"📧 شناسه اشتراک: `{result.email}`\n"
            f"{ip_line}"
            "\n📱 *روش اتصال:*\n"
            "۱. QR کد را اسکن کنید\n"
            "۲. یا لینک زیر را در اپ کپی کنید\n\n"
            f"🔗 *لینک اشتراک:*\n`{result.sub_link}`\n\n"
            "📲 *اپ‌های پیشنهادی:*\n"
            "• اندروید: هیدیفای، وی‌تو‌ری‌ان‌جی\n"
            "• آیفون: استرایزند، شدوراکت\n"
            "• ویندوز: هیدیفای، وی‌تو‌ری‌ان\n"
            "• مک: هیدیفای، وی‌تو‌باکس\n\n"
            "⚠️ این لینک را با کسی به اشتراک نگذارید."
        )
        await callback.message.answer_photo(  # type: ignore[union-attr]
            photo=qr_file,
            caption=caption,
            parse_mode="Markdown",
        )
        await processing_msg.delete()

    except Exception as e:
        logger.exception(f"خطا در ایجاد اشتراک بعد از پرداخت {order_id}: {e}")
        await processing_msg.edit_text(
            "✅ پرداخت تأیید شد اما خطایی در ایجاد کانفیگ رخ داد.\n"
            "لطفاً با پشتیبانی تماس بگیرید و این شناسه را ارسال کنید:\n"
            f"`{order_id}`",
            parse_mode="Markdown",
        )
