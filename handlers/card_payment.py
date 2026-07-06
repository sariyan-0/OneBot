"""
handlers/card_payment.py — پرداخت کارت به کارت

Flow:
  ۱. کاربر «💳 کارت به کارت» را انتخاب می‌کند
  ۲. شماره کارت + مبلغ (دلار و تومان) نمایش داده می‌شود
  ۳. کاربر رسید (عکس یا متن) می‌فرستد
  ۴. رسید به همه ادمین‌ها فوروارد می‌شود + دکمه تأیید
  ۵. ادمین تأیید می‌کند → اشتراک فعال می‌شود + کاربر اطلاع‌رسانی می‌شود
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from config import settings
from database import AsyncSessionLocal
from database.crud import (
    create_payment, get_or_create_user, get_payment_by_order_id,
    get_user_by_telegram_id, update_payment_status,
)
from services.card_payment import calc_rial_amount, fmt_card_number, get_card_info
from services.subscription import create_new_subscription, apply_paid_plan_to_subscription
from services.wallet import credit_wallet

router = Router(name="card_payment")


class CardPayStates(StatesGroup):
    waiting_receipt = State()   # انتظار رسید از کاربر


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────

async def _notify_admins_receipt(
    bot: Bot,
    order_id: str,
    plan_name: str,
    amount_toman: int,
    receipt_msg: Message,
    original_price_usdt: float = 0.0,
    final_price_usdt: float = 0.0,
    discount_code: Optional[str] = None,
    discount_percent: int = 0,
) -> None:
    """ارسال رسید به همه ادمین‌ها با دکمه تأیید.
    
    اگر کد تخفیف اعمال شده باشد، قیمت اصلی، درصد تخفیف و قیمت نهایی
    به صورت برجسته در پیام ادمین نمایش داده می‌شود.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ تأیید پرداخت و فعال‌سازی", callback_data=f"card_approve:{order_id}")
    kb.button(text="❌ رد پرداخت",                  callback_data=f"card_reject:{order_id}")
    kb.adjust(1)

    # بخش قیمت — اگر تخفیف دارد خط اضافی نشان بده
    if discount_code and discount_percent > 0 and original_price_usdt > 0:
        price_lines = (
            f"💵 قیمت اصلی: <s>{original_price_usdt:.2f} دلار</s>\n"
            f"🎟 کد تخفیف: <code>{discount_code}</code>  ({discount_percent}٪ تخفیف)\n"
            f"💰 مبلغ پرداختی: <b>{amount_toman:,} تومان</b>  "
            f"(<b>{final_price_usdt:.2f} دلار</b>)\n"
        )
    else:
        price_lines = f"💰 مبلغ: <b>{amount_toman:,} تومان</b>\n"

    caption = (
        f"💳 <b>رسید پرداخت کارت به کارت</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📦 پلن: <b>{plan_name}</b>\n"
        f"{price_lines}"
        f"🔖 سفارش: <code>{order_id}</code>\n"
        f"👤 کاربر: <code>{receipt_msg.from_user.id}</code> "
        f"(@{receipt_msg.from_user.username or '-'})"
    )

    for admin_id in settings.admin_ids:
        try:
            if receipt_msg.photo:
                await bot.send_photo(
                    chat_id=admin_id,
                    photo=receipt_msg.photo[-1].file_id,
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=kb.as_markup(),
                )
            else:
                text_receipt = receipt_msg.text or receipt_msg.caption or "—"
                await bot.send_message(
                    chat_id=admin_id,
                    text=caption + f"\n\n📝 <b>متن رسید:</b>\n<code>{text_receipt}</code>",
                    parse_mode="HTML",
                    reply_markup=kb.as_markup(),
                )
        except Exception as e:
            logger.warning(f"ارسال رسید به ادمین {admin_id} ناموفق: {e}")


# ──────────────────────────────────────────────
# ورود به flow کارت به کارت
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("card_pay:"))
async def cb_card_pay(callback: CallbackQuery, state: FSMContext) -> None:
    """
    callback_data: card_pay:{plan_id}:{discount_code_or_empty}
    نمایش شماره کارت و مبلغ + درخواست ارسال رسید
    """
    await callback.answer()
    tg_user = callback.from_user
    parts = callback.data.split(":", 2)
    plan_id = int(parts[1])
    discount_code = parts[2] if len(parts) > 2 and parts[2] else None

    # دریافت اطلاعات کارت
    card = await get_card_info()
    if not card["number"]:
        await callback.message.answer(
            "⚠️ شماره کارت هنوز تنظیم نشده.\nلطفاً با روش پرداخت دیگری اقدام کنید."
        )
        return

    async with AsyncSessionLocal() as session:
        from database.crud import get_plan, get_discount_code, validate_discount
        plan = await get_plan(session, plan_id)
        if not plan or not plan.is_active:
            await callback.message.answer("❌ این پلن در دسترس نیست.")
            return

        original_price = plan.price_usdt
        final_price    = plan.price_usdt
        discount_percent = 0
        if discount_code:
            dc = await get_discount_code(session, discount_code)
            if dc:
                valid, _ = validate_discount(dc)
                if valid:
                    discount_percent = dc.percent
                    final_price = round(original_price * (1 - discount_percent / 100), 2)

        db_user, _ = await get_or_create_user(
            session, tg_user.id, tg_user.username, tg_user.first_name,
            admin_ids=settings.admin_ids,
        )

    rial, toman = calc_rial_amount(final_price, card["rate"])
    order_id = f"card_{tg_user.id}_{plan_id}_{uuid.uuid4().hex[:8]}"
    card_fmt = fmt_card_number(card["number"])
    holder   = card["holder"] or "—"

    # ذخیره در FSM — شامل اطلاعات تخفیف برای نمایش به ادمین
    await state.set_state(CardPayStates.waiting_receipt)
    await state.update_data(
        order_id=order_id,
        plan_id=plan_id,
        plan_name=plan.name,
        amount_toman=toman,
        amount_rial=rial,
        amount_usdt=final_price,
        original_price_usdt=original_price,
        discount_code=discount_code,
        discount_percent=discount_percent,
        user_db_id=db_user.id,
    )

    # ذخیره پرداخت با وضعیت awaiting_review
    async with AsyncSessionLocal() as session:
        await create_payment(
            session=session,
            user_id=db_user.id,
            order_id=order_id,
            amount_usdt=final_price,
            inbound_id=plan_id,
            payment_method="card",
            amount_rial=rial,
        )

    # بخش تخفیف — اگه تخفیف داره نشون بده
    discount_line = ""
    if discount_percent > 0:
        discount_line = (
            f"🏷 تخفیف {discount_percent}٪ اعمال شد "
            f"({original_price:.2f}$ → <b>{final_price:.2f}$</b>)\n"
        )

    await callback.message.answer(
        f"💳 <b>پرداخت کارت به کارت</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📦 پلن: <b>{plan.name}</b>\n"
        f"💵 قیمت: <b>{final_price:.2f} دلار</b>\n"
        f"{discount_line}"
        f"💱 نرخ محاسبه: <b>{card['rate']:,} تومان</b> / دلار\n\n"
        f"🏦 شماره کارت:\n"
        f"<code>{card_fmt}</code>\n"
        f"👤 به نام: <b>{holder}</b>\n\n"
        f"💵 مبلغ دقیق واریزی:\n"
        f"  • <b>{toman:,} تومان</b>\n"
        f"  • <b>{rial:,} ریال</b>\n\n"
        f"⚠️ <b>مبلغ را دقیقاً به همین مقدار واریز کنید.</b>\n\n"
        f"پس از واریز، <b>رسید</b> را اینجا بفرستید.\n"
        f"(عکس رسید یا متن شناسه تراکنش)\n\n"
        f"برای لغو: /cancel",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("subcard:"))
async def cb_sub_card_pay(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    tg_user = callback.from_user
    parts = callback.data.split(":", 4)
    flow = parts[1] if len(parts) > 1 else "renew"
    target_sub_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    plan_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    discount_code = parts[4] if len(parts) > 4 and parts[4] else None

    card = await get_card_info()
    if not card["number"]:
        await callback.message.answer("⚠️ شماره کارت هنوز تنظیم نشده.\nلطفاً با روش دیگری اقدام کنید.")
        return

    async with AsyncSessionLocal() as session:
        from database.crud import get_plan, get_discount_code, validate_discount
        plan = await get_plan(session, plan_id)
        if not plan or not plan.is_active:
            await callback.message.answer("❌ این پلن در دسترس نیست.")
            return
        original_price = plan.price_usdt
        final_price = plan.price_usdt
        discount_percent = 0
        if discount_code:
            dc = await get_discount_code(session, discount_code)
            if dc:
                valid, _ = validate_discount(dc)
                if valid:
                    discount_percent = dc.percent
                    final_price = round(original_price * (1 - discount_percent / 100), 2)
        db_user, _ = await get_or_create_user(
            session, tg_user.id, tg_user.username, tg_user.first_name,
            admin_ids=settings.admin_ids,
        )

    rial, toman = calc_rial_amount(final_price, card["rate"])
    order_id = f"{flow}_{target_sub_id}_{plan_id}_{uuid.uuid4().hex[:8]}"
    card_fmt = fmt_card_number(card["number"])
    holder = card["holder"] or "—"

    await state.set_state(CardPayStates.waiting_receipt)
    await state.update_data(
        order_id=order_id,
        plan_id=plan_id,
        plan_name=plan.name,
        amount_toman=toman,
        amount_rial=rial,
        amount_usdt=final_price,
        original_price_usdt=original_price,
        discount_code=discount_code,
        discount_percent=discount_percent,
        user_db_id=db_user.id,
    )

    async with AsyncSessionLocal() as session:
        await create_payment(
            session=session,
            user_id=db_user.id,
            order_id=order_id,
            amount_usdt=final_price,
            inbound_id=plan_id,
            payment_method="card",
            amount_rial=rial,
        )

    await callback.message.answer(
        f"💳 <b>پرداخت کارت به کارت</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📦 پلن: <b>{plan.name}</b>\n"
        f"💵 قیمت: <b>{final_price:.2f} دلار</b>\n\n"
        f"🏦 شماره کارت:\n<code>{card_fmt}</code>\n"
        f"👤 به نام: <b>{holder}</b>\n\n"
        f"💵 مبلغ دقیق واریزی:\n  • <b>{toman:,} تومان</b>\n\n"
        f"پس از واریز، رسید را ارسال کنید.\n"
        f"برای لغو: /cancel",
        parse_mode="HTML",
    )


@router.message(CardPayStates.waiting_receipt, F.text == "/cancel")
async def card_cancel(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    order_id = data.get("order_id", "")
    await state.clear()
    if order_id:
        async with AsyncSessionLocal() as session:
            p = await get_payment_by_order_id(session, order_id)
            if p:
                await update_payment_status(session, p.id, "failed")
    await message.answer("❌ پرداخت کارت به کارت لغو شد.")


@router.message(CardPayStates.waiting_receipt)
async def card_receive_receipt(message: Message, state: FSMContext) -> None:
    """دریافت رسید (عکس یا متن) و ارسال به ادمین‌ها."""
    data = await state.get_data()
    order_id         = data["order_id"]
    plan_name        = data["plan_name"]
    toman            = data["amount_toman"]
    rial             = data["amount_rial"]
    original_price   = data.get("original_price_usdt", 0.0)
    final_price      = data.get("amount_usdt", 0.0)
    discount_code    = data.get("discount_code")
    discount_percent = data.get("discount_percent", 0)

    # باید عکس یا متن باشد
    if not message.photo and not message.text and not message.caption:
        await message.answer("⚠️ لطفاً عکس رسید یا متن شناسه تراکنش را بفرستید.")
        return

    await state.clear()

    # ذخیره اطلاعات رسید در دیتابیس
    async with AsyncSessionLocal() as session:
        p = await get_payment_by_order_id(session, order_id)
        if p:
            receipt_fid = message.photo[-1].file_id if message.photo else None
            receipt_type = "photo" if message.photo else "text"
            receipt_text = message.text or message.caption or ""
            from sqlalchemy import update as sql_update
            from database.models import Payment
            await session.execute(
                sql_update(Payment).where(Payment.order_id == order_id).values(
                    status="awaiting_review",
                    receipt_file_id=receipt_fid or receipt_text[:255],
                    receipt_type=receipt_type,
                )
            )
            await session.commit()

    # ارسال به ادمین‌ها — با اطلاعات کامل تخفیف
    await _notify_admins_receipt(
        message.bot, order_id, plan_name, toman, message,
        original_price_usdt=original_price,
        final_price_usdt=final_price,
        discount_code=discount_code,
        discount_percent=discount_percent,
    )

    await message.answer(
        f"✅ <b>رسید دریافت شد!</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔖 شناسه سفارش: <code>{order_id}</code>\n\n"
        f"رسید شما برای بررسی به ادمین ارسال شد.\n"
        f"پس از تأیید، اشتراک شما فعال می‌شود.\n\n"
        f"⏳ معمولاً کمتر از ۳۰ دقیقه طول می‌کشد.",
        parse_mode="HTML",
    )


# ──────────────────────────────────────────────
# تأیید/رد ادمین
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("card_approve:"))
async def cb_card_approve(callback: CallbackQuery) -> None:
    """ادمین پرداخت را تأیید کرد → ایجاد اشتراک."""
    from handlers.admin import _check_admin
    if not await _check_admin(callback):
        return

    order_id = callback.data.split(":", 1)[1]
    await callback.answer("⏳ در حال فعال‌سازی اشتراک...")
    processing = await callback.message.answer("⏳ در حال ایجاد اشتراک...")

    async with AsyncSessionLocal() as session:
        payment = await get_payment_by_order_id(session, order_id)
        if not payment:
            await processing.edit_text("❌ سفارش پیدا نشد.")
            return
        # جلوگیری از تأیید مجدد — هر دو status تأیید‌شده را بررسی می‌کند
        if payment.status in ("confirmed", "finished"):
            await processing.edit_text("✅ این پرداخت قبلاً تأیید شده است.")
            return

        # دریافت user با user_id
        from database.models import User
        from sqlalchemy import select
        res = await session.execute(select(User).where(User.id == payment.user_id))
        user = res.scalar_one_or_none()
        if not user:
            await processing.edit_text("❌ کاربر پیدا نشد.")
            return

        plan_id = getattr(payment, "inbound_id", 0) or 0

        try:
            if order_id.startswith("wallet_"):
                credited = await credit_wallet(session, user.id, float(payment.amount_usdt))
                await update_payment_status(session, payment.id, "confirmed")
                result = None
            elif order_id.startswith(("renew_", "change_")):
                parts = order_id.split("_", 3)
                action = parts[0]
                target_sub_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                plan_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else plan_id
                result = await apply_paid_plan_to_subscription(
                    session=session,
                    subscription_id=target_sub_id,
                    plan_id=plan_id,
                    telegram_id=user.telegram_id,
                    action=action,
                )
                await update_payment_status(session, payment.id, "confirmed", result.subscription.id)
            else:
                result = await create_new_subscription(
                    session=session,
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    inbound_id=0,  # انتخاب خودکار بر اساس پلن
                    plan_id=plan_id,
                )
                await update_payment_status(session, payment.id, "confirmed", result.subscription.id)
        except Exception as e:
            logger.error(f"خطا در ایجاد اشتراک بعد از تأیید کارت {order_id}: {e}")
            await processing.edit_text(
                f"❌ خطا در ایجاد اشتراک: <code>{e}</code>\n\n"
                f"🔖 order_id: <code>{order_id}</code>\n"
                f"👤 telegram_id: <code>{user.telegram_id}</code>\n\n"
                f"می‌توانید اشتراک را دستی از پنل ادمین ایجاد کنید.",
                parse_mode="HTML",
            )
            return

    # اطلاع‌رسانی به کاربر
    try:
        if order_id.startswith("wallet_"):
            payment_rate = 0
            try:
                payment_rate = (await get_card_info()).get("rate", 0) or 0
            except Exception:
                payment_rate = 0
            added_amount = float(payment.amount_usdt)
            added_toman = int(added_amount * payment_rate) if payment_rate > 0 else 0
            credited_toman = int(credited * payment_rate) if payment_rate > 0 else 0
            added_lines = f"  • <b>${added_amount:.2f}</b>\n"
            if added_toman:
                added_lines += f"  • <b>{added_toman:,} تومان</b>\n"
            balance_lines = f"  • <b>${credited:.2f}</b>\n"
            if credited_toman:
                balance_lines += f"  • <b>{credited_toman:,} تومان</b>"
            await callback.bot.send_message(
                chat_id=user.telegram_id,
                text=(
                    f"💼 <b>شارژ کیف پول شما تأیید شد!</b>\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"🔖 سفارش: <code>{order_id}</code>\n"
                    f"💰 مبلغ افزوده‌شده:\n"
                    f"{added_lines}"
                    f"💳 موجودی جدید:\n"
                    f"{balance_lines}"
                ),
                parse_mode="HTML",
            )
        else:
            await callback.bot.send_message(
                chat_id=user.telegram_id,
                text=(
                    f"🎉 <b>پرداخت شما تأیید شد!</b>\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"🔖 سفارش: <code>{order_id}</code>\n\n"
                    f"اشتراک VPN شما فعال شد.\n"
                    f"🔗 <b>لینک اشتراک:</b>\n<code>{result.sub_link}</code>"
                ),
                parse_mode="HTML",
            )
            if result.qr_bytes:
                await callback.bot.send_photo(
                    chat_id=user.telegram_id,
                    photo=BufferedInputFile(result.qr_bytes, "sub_qr.png"),
                    caption="📷 QR کد اشتراک شما",
                )
    except Exception as e:
        logger.warning(f"ارسال اشتراک به کاربر {user.telegram_id} ناموفق: {e}")

    # آپدیت پیام ادمین
    await processing.delete()
    try:
        new_caption = (
            (callback.message.caption or callback.message.text or "") +
            f"\n\n✅ <b>تأیید شد توسط ادمین {callback.from_user.id}</b>"
        )
        if callback.message.photo:
            await callback.message.edit_caption(new_caption, parse_mode="HTML")
        else:
            await callback.message.edit_text(new_caption, parse_mode="HTML")
    except Exception:
        await callback.message.answer("✅ اشتراک فعال شد و به کاربر ارسال شد.")

    if order_id.startswith("wallet_"):
        logger.success(f"پرداخت کارت {order_id} تأیید شد — کیف پول شارژ شد.")
    else:
        logger.success(f"پرداخت کارت {order_id} تأیید شد — اشتراک {result.subscription.id} ایجاد شد.")


@router.callback_query(F.data.startswith("card_reject:"))
async def cb_card_reject(callback: CallbackQuery) -> None:
    """ادمین پرداخت را رد کرد."""
    from handlers.admin import _check_admin
    if not await _check_admin(callback):
        return

    order_id = callback.data.split(":", 1)[1]
    user = None
    async with AsyncSessionLocal() as session:
        payment = await get_payment_by_order_id(session, order_id)
        if payment:
            # جلوگیری از رد کردن پرداخت‌های قبلاً تأیید شده
            if payment.status in ("confirmed", "finished"):
                await callback.answer("⚠️ این پرداخت قبلاً تأیید شده — نمی‌توان رد کرد.", show_alert=True)
                return
            await update_payment_status(session, payment.id, "failed")
            from database.models import User
            from sqlalchemy import select
            res = await session.execute(select(User).where(User.id == payment.user_id))
            user = res.scalar_one_or_none()

    if user:
        try:
            await callback.bot.send_message(
                chat_id=user.telegram_id,
                text=(
                    f"❌ <b>پرداخت شما تأیید نشد.</b>\n\n"
                    f"🔖 سفارش: <code>{order_id}</code>\n\n"
                    f"در صورت وجود مشکل با پشتیبانی تماس بگیرید."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    await callback.answer("❌ پرداخت رد شد.", show_alert=True)
    try:
        new_text = (
            (callback.message.caption or callback.message.text or "") +
            f"\n\n❌ <b>رد شد توسط ادمین {callback.from_user.id}</b>"
        )
        if callback.message.photo:
            await callback.message.edit_caption(new_text, parse_mode="HTML")
        else:
            await callback.message.edit_text(new_text, parse_mode="HTML")
    except Exception:
        pass
