"""
services/webhook_server.py — aiohttp webhook server for payment gateways

NOWPayments IPN:
  POST /webhook/nowpayments
  Signature: HMAC-SHA512(sorted JSON) → x-nowpayments-sig header
  Trigger status: "finished" only

MaxelPay Webhook (official docs: https://docs.maxelpay.com/webhooks-1985141m0):
  POST /webhook/maxelpay
  Signature: HMAC-SHA256(JSON.stringify(body), secret_key) → X-MaxelPay-Signature header
  Payload: { "event": "payment.completed", "data": { "sessionId", "orderId", "status": "paid", ... } }
  Trigger events: payment.completed, payment.overpaid
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Dict

from aiohttp import web
from loguru import logger

from config import settings
from database import AsyncSessionLocal
from database.crud import (
    get_payment_by_order_id,
    get_payment_by_payment_id,
    get_setting,
    update_payment_status,
    get_or_create_user,
)
from services.subscription import create_new_subscription, apply_paid_plan_to_subscription
from services.wallet import credit_wallet
from services.payment_config import get_nowpayments_config, get_maxelpay_config
from services.referral import grant_referral_commission_for_payment

# ──────────────────────────────────────────────
# وضعیت‌هایی که باعث فعال شدن اشتراک می‌شوند
# طبق مستندات NOWPayments: فقط "finished"
# ──────────────────────────────────────────────
TRIGGER_STATUSES = {"finished"}


async def _resolve_bot_token() -> str:
    """
    BOT_TOKEN را از admin_settings دیتابیس می‌خواند و اگر خالی بود،
    از env / .env استفاده می‌کند.
    """
    env_token = settings.bot_token.strip()
    try:
        async with AsyncSessionLocal() as session:
            db_token = (await get_setting(session, "BOT_TOKEN", "")).strip()
            token_source = (await get_setting(session, "BOT_TOKEN_SOURCE", "")).strip().lower()
            if db_token and env_token and db_token != env_token and token_source != "panel":
                return env_token
            if db_token:
                return db_token
    except Exception as exc:
        logger.debug(f"Webhook token lookup skipped: {exc}")

    return env_token


# ──────────────────────────────────────────────
# Signature validation
# ──────────────────────────────────────────────

def _sort_recursive(obj: Any) -> Any:
    """
    مرتب‌سازی recursive کلیدهای dict — طبق مستندات NOWPayments.
    JSON.stringify(params, Object.keys(params).sort()) معادل Python.
    """
    if isinstance(obj, dict):
        return {k: _sort_recursive(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_sort_recursive(i) for i in obj]
    return obj


async def _verify_signature(body_bytes: bytes, received_sig: str) -> bool:
    """
    تأیید امضای IPN از NOWPayments.

    مراحل (طبق مستندات):
      1. parse JSON از body
      2. sort کلیدها به صورت recursive
      3. serialize به JSON compact (بدون فاصله)
      4. HMAC-SHA512 با ipn_secret
      5. مقایسه با x-nowpayments-sig
    """
    runtime = await get_nowpayments_config()
    ipn_secret = runtime["ipn_secret"]
    if not ipn_secret:
        logger.warning("IPN secret تنظیم نشده — signature check رد می‌شود (توسعه)")
        return True

    if not received_sig:
        logger.warning("x-nowpayments-sig header وجود ندارد")
        return False

    try:
        body_json = json.loads(body_bytes)
    except json.JSONDecodeError:
        logger.error("IPN body قابل parse نیست")
        return False

    sorted_body = _sort_recursive(body_json)
    sorted_str  = json.dumps(sorted_body, separators=(",", ":"), sort_keys=False)

    expected = hmac.new(
        ipn_secret.encode("utf-8"),
        sorted_str.encode("utf-8"),
        hashlib.sha512,
    ).hexdigest()

    return hmac.compare_digest(expected, received_sig)


# ──────────────────────────────────────────────
# IPN handler
# ──────────────────────────────────────────────

async def handle_nowpayments_ipn(request: web.Request) -> web.Response:
    """
    POST /webhook/nowpayments

    Flow:
      1. body خواندن
      2. signature تأیید
      3. payment_status چک
      4. اگه "finished" → اشتراک ایجاد کن
      5. به کاربر تلگرام اطلاع بده
    """
    body_bytes = await request.read()
    received_sig = request.headers.get("x-nowpayments-sig", "")

    # تأیید امضا
    if not await _verify_signature(body_bytes, received_sig):
        logger.warning("IPN signature نامعتبر — reject")
        return web.Response(status=400, text="Invalid signature")

    try:
        data: Dict[str, Any] = json.loads(body_bytes)
    except json.JSONDecodeError:
        return web.Response(status=400, text="Invalid JSON")

    payment_id    = str(data.get("payment_id", ""))
    order_id      = str(data.get("order_id", ""))
    status        = str(data.get("payment_status", ""))
    outcome_amount = data.get("outcome_amount")

    logger.info(f"IPN دریافت شد | payment_id={payment_id} order_id={order_id} status={status}")

    # ── فقط status=finished اشتراک می‌دهد ────────────────────────────────
    # طبق مستندات NOWPayments:
    #   "Do not grant goods or services when the payment is in
    #    'confirming' or 'confirmed' statuses."
    if status not in TRIGGER_STATUSES:
        logger.info(f"IPN status={status} — نادیده گرفتن (منتظر finished)")
        return web.Response(status=200, text="OK")

    # پیدا کردن payment در دیتابیس
    async with AsyncSessionLocal() as session:
        payment = None
        if order_id:
            payment = await get_payment_by_order_id(session, order_id)
        if payment is None and payment_id:
            payment = await get_payment_by_payment_id(session, payment_id)

    if payment is None:
        logger.warning(f"IPN: پرداخت پیدا نشد | order_id={order_id} payment_id={payment_id}")
        return web.Response(status=200, text="OK")  # 200 برگردون تا NOWPayments retry نکنه

    # جلوگیری از پردازش دوگانه
    if payment.status in ("confirmed", "finished"):
        logger.info(f"IPN: پرداخت {order_id} قبلاً پردازش شده — skip")
        return web.Response(status=200, text="OK")

    # پرداخت کارت به کارت — دست نمی‌زنیم
    if getattr(payment, "payment_method", "crypto") == "card":
        logger.info(f"IPN: پرداخت کارت به کارت — IPN اعمال نمی‌شود")
        return web.Response(status=200, text="OK")

    logger.success(f"IPN: پرداخت تأیید شد — ایجاد اشتراک برای order={order_id}")
    await _activate_subscription(payment, order_id)
    return web.Response(status=200, text="OK")


async def _activate_subscription(payment: Any, order_id: str) -> None:
    """اشتراک ایجاد کن و به کاربر اطلاع بده."""
    from aiogram.types import BufferedInputFile
    from services.activity_log import ActivityLoggingBot

    try:
        async with AsyncSessionLocal() as session:
            # پیدا کردن user
            from database.models import User
            from sqlalchemy import select
            res = await session.execute(
                select(User).where(User.id == payment.user_id)
            )
            user = res.scalar_one_or_none()
            if not user:
                logger.error(f"IPN: کاربر برای payment {order_id} پیدا نشد")
                return

            if order_id.startswith("wallet_"):
                credited = await credit_wallet(session, user.id, float(payment.amount_usdt), currency="usd")
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
                    telegram_id=user.telegram_id,
                    action=action,
                )
                await update_payment_status(session, payment.id, "confirmed", result.subscription.id)
                await grant_referral_commission_for_payment(session, payment)
            else:
                result = await create_new_subscription(
                    session=session,
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    inbound_id=0,
                    plan_id=getattr(payment, "inbound_id", 0),
                )
                await update_payment_status(
                    session, payment.id, "confirmed", result.subscription.id
                )
                await grant_referral_commission_for_payment(session, payment)

        bot_token = await _resolve_bot_token()
        if not bot_token:
            logger.warning("BOT_TOKEN تنظیم نشده — ارسال پیام webhook به کاربر انجام نشد.")
            return

        bot = ActivityLoggingBot(token=bot_token)
        try:
            if order_id.startswith("wallet_"):
                text = (
                    "💼 <b>شارژ کیف پول شما تأیید شد!</b>\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"🔖 سفارش: <code>{order_id}</code>\n"
                    f"💰 مبلغ افزوده‌شده: <b>{float(payment.amount_usdt):.2f} دلار</b>\n"
                    f"💳 موجودی جدید: <b>{float(credited):.2f} دلار</b>"
                )
            else:
                text = (
                    "🎉 <b>پرداخت کریپتو تأیید شد!</b>\n"
                    "━━━━━━━━━━━━━━━\n"
                    f"🔖 سفارش: <code>{order_id}</code>\n\n"
                    f"🔗 <b>لینک اشتراک:</b>\n<code>{result.sub_link}</code>\n\n"
                    "📲 لینک را در اپ VPN خود وارد کنید."
                )
            await bot.send_message(
                chat_id=user.telegram_id,
                text=text,
                parse_mode="HTML",
            )
            if not order_id.startswith("wallet_") and result.qr_bytes:
                from aiogram.types import BufferedInputFile
                await bot.send_photo(
                    chat_id=user.telegram_id,
                    photo=BufferedInputFile(result.qr_bytes, "sub_qr.png"),
                    caption="📷 QR کد اشتراک شما",
                )
        except Exception as e:
            logger.warning(f"ارسال پیام به کاربر {user.telegram_id} ناموفق: {e}")
        finally:
            await bot.session.close()

        if order_id.startswith("wallet_"):
            logger.success(f"IPN: کیف پول کاربر {user.telegram_id} شارژ شد.")
        else:
            logger.success(f"IPN: اشتراک {result.subscription.id} برای کاربر {user.telegram_id} فعال شد")

    except Exception as e:
        logger.exception(f"IPN: خطا در ایجاد اشتراک برای {order_id}: {e}")


# ──────────────────────────────────────────────
# ساخت و اجرای webhook server
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# MaxelPay webhook handler
# ──────────────────────────────────────────────

async def handle_maxelpay_webhook(request: web.Request) -> web.Response:
    """
    POST /webhook/maxelpay

    Official MaxelPay webhook payload:
    {
      "event": "payment.completed",
      "timestamp": "...",
      "data": { "sessionId", "orderId", "status": "paid", "amount", ... }
    }

    Signature verification:
      Header: X-MaxelPay-Signature
      Method: HMAC-SHA256(JSON.stringify(body), MAXELPAY_WEBHOOK_SECRET)
    """
    body_bytes = await request.read()

    # ── Signature verification ────────────────────────────────
    from services.maxelpay import MaxelPayClient, verify_maxelpay_signature
    received_sig = request.headers.get("X-MaxelPay-Signature", "")
    runtime = await get_maxelpay_config()
    webhook_secret = runtime["webhook_secret"] or ""

    if webhook_secret and not verify_maxelpay_signature(body_bytes, received_sig, webhook_secret):
        logger.warning("MaxelPay webhook: invalid signature — rejected")
        return web.Response(status=401, text="Invalid signature")

    # ── Parse body ────────────────────────────────────────────
    try:
        data: Dict[str, Any] = json.loads(body_bytes)
    except Exception:
        return web.Response(status=400, text="Invalid JSON")

    status_obj = MaxelPayClient.parse_webhook(data)

    # ── Filter events (official: completed/failed/expired/processing) ─────────
    if not status_obj.is_paid:
        if status_obj.is_processing:
            # payment.processing = wallet assigned, awaiting transaction — just log
            logger.info(
                f"MaxelPay webhook: processing — wallet assigned | "
                f"order={status_obj.order_id} session={status_obj.session_id}"
            )
        elif status_obj.is_partial:
            # partially_paid — user paid less than required — log, notify admin
            logger.warning(
                f"MaxelPay webhook: PARTIAL PAYMENT | "
                f"order={status_obj.order_id} amount={status_obj.amount} {status_obj.token or ''}"
            )
        elif status_obj.is_failed:
            # payment.failed or payment.expired — mark in DB
            async with AsyncSessionLocal() as db_session:
                payment = await get_payment_by_order_id(db_session, status_obj.order_id)
                if payment and payment.status not in ("confirmed", "finished"):
                    await update_payment_status(db_session, payment.id, status_obj.status)
            logger.info(
                f"MaxelPay webhook: payment failed | "
                f"event={status_obj.event!r} status={status_obj.status!r} "
                f"order={status_obj.order_id}"
            )
        else:
            logger.info(
                f"MaxelPay webhook: event={status_obj.event!r} status={status_obj.status!r} "
                f"— not a paid event, ignoring"
            )
        return web.json_response({"received": True})

    # ── Find payment in DB ────────────────────────────────────
    async with AsyncSessionLocal() as session:
        payment = await get_payment_by_order_id(session, status_obj.order_id)
        if not payment and status_obj.session_id:
            payment = await get_payment_by_payment_id(session, status_obj.session_id)

    if payment is None:
        logger.warning(
            f"MaxelPay webhook: payment not found | "
            f"order={status_obj.order_id} session={status_obj.session_id}"
        )
        return web.Response(status=200, text="OK")

    if payment.status in ("confirmed", "finished"):
        logger.info(f"MaxelPay webhook: already processed — order={status_obj.order_id}")
        return web.Response(status=200, text="OK")

    if getattr(payment, "payment_method", "") != "maxelpay":
        logger.info(f"MaxelPay webhook: payment_method mismatch — skip")
        return web.Response(status=200, text="OK")

    logger.success(
        f"MaxelPay webhook: payment confirmed | "
        f"order={status_obj.order_id} event={status_obj.event!r} "
        f"amount={status_obj.amount} {status_obj.token or status_obj.currency or 'USD'} | "
        f"network={status_obj.network} txHash={status_obj.tx_hash}"
    )
    await _activate_subscription(payment, status_obj.order_id)
    return web.json_response({"received": True})


def create_webhook_app() -> web.Application:
    """ساخت aiohttp application با route های لازم."""
    app = web.Application()
    app.router.add_post("/webhook/nowpayments", handle_nowpayments_ipn)
    app.router.add_post("/webhook/maxelpay",    handle_maxelpay_webhook)
    # health check برای load balancer / Docker healthcheck
    app.router.add_get("/health", lambda r: web.Response(text="OK"))
    try:
        from services.web_admin import setup_web_admin
        setup_web_admin(app)
    except Exception as e:
        logger.warning(f"Web admin panel disabled: {e}")
    return app


async def start_webhook_server() -> web.AppRunner:
    """
    شروع webhook server روی پورت WEBHOOK_PORT.
    این تابع در main.py داخل startup ربات صدا زده می‌شود.

    پیش‌فرض: 9988 — با 3X-UI (54321 / 8443) و nginx (80/443) تداخل ندارد.
    Routes:
      POST /webhook/nowpayments  — IPN از NOWPayments
      POST /webhook/maxelpay     — webhook از MaxelPay
      GET  /health               — health check
    """
    port = getattr(settings, "webhook_port", 9988)
    app = create_webhook_app()
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.success(
            f"✅ Webhook server روی پورت {port} شروع شد\n"
            f"   /webhook/nowpayments  — NOWPayments IPN\n"
            f"   /webhook/maxelpay     — MaxelPay webhook\n"
            f"   /admin                — Web admin panel"
        )
    except OSError as e:
        await runner.cleanup()
        logger.error(
            f"❌ پورت {port} برای webhook در دسترس نیست: {e}\n"
            f"   پورت دیگری در .env تنظیم کنید: WEBHOOK_PORT=7777\n"
            f"   بررسی پورت‌های اشغال: ss -tlnp | grep {port}"
        )
        raise
    return runner
