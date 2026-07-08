"""
services/payments.py — سرویس پرداخت کریپتویی با NOWPayments
تمرکز روی USDT TRC-20 (شبکه TRON)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx
from loguru import logger

from config import settings
from services.payment_config import get_nowpayments_config


# ──────────────────────────────────────────────
# IPN signature helper
# ──────────────────────────────────────────────

def _sort_keys_recursive(obj: Any) -> Any:
    """
    مرتب‌سازی recursive کلیدهای dict — طبق مستندات NOWPayments.
    معادل JavaScript: JSON.stringify(params, Object.keys(params).sort())
    """
    if isinstance(obj, dict):
        return {k: _sort_keys_recursive(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_sort_keys_recursive(i) for i in obj]
    return obj


# ──────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────

class PaymentError(Exception):
    """خطای پایه سرویس پرداخت."""


class PaymentAPIError(PaymentError):
    """خطای ارتباط با API نوپیمنتس."""


class PaymentNotFoundError(PaymentError):
    """invoice پیدا نشد."""


class PaymentExpiredError(PaymentError):
    """invoice منقضی شده."""


# ──────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────

@dataclass
class InvoiceResult:
    payment_id: str           # شناسه پرداخت در NOWPayments
    order_id: str             # شناسه سفارش داخلی ما
    pay_address: str          # آدرس والت USDT TRC-20
    pay_amount: float         # مقدار USDT باید پرداخت شود
    pay_currency: str         # ارز پرداخت (usdttrc20)
    price_amount: float       # مبلغ اصلی (USD)
    price_currency: str       # ارز اصلی
    expiration_time: datetime # زمان انقضای invoice
    status: str               # وضعیت: waiting | confirming | confirmed | failed
    qr_data: str              # داده برای تولید QR (آدرس والت)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InvoicePageResult:
    """نتیجه ساخت Invoice — کاربر به invoice_url هدایت می‌شه و خودش ارز انتخاب می‌کنه."""
    invoice_id: str           # شناسه invoice در NOWPayments
    order_id: str             # شناسه سفارش داخلی ما
    invoice_url: str          # لینکی که کاربر باید باز کنه
    price_amount: float       # مبلغ اصلی
    price_currency: str       # ارز اصلی (usd)
    expiration_time: datetime # زمان انقضا


@dataclass
class PaymentStatus:
    payment_id: str
    order_id: str
    status: str               # waiting | confirming | confirmed | partially_paid | failed | expired | refunded
    pay_amount: float
    actually_paid: float
    pay_currency: str
    outcome_amount: Optional[float] = None
    updated_at: Optional[datetime] = None


# ──────────────────────────────────────────────
# وضعیت‌های موفق و ناموفق
# ──────────────────────────────────────────────

PAID_STATUSES = {"confirmed", "finished"}
FAILED_STATUSES = {"failed", "expired", "refunded"}
PENDING_STATUSES = {"waiting", "confirming", "partially_paid", "sending"}


# ──────────────────────────────────────────────
# CryptoPaymentService
# ──────────────────────────────────────────────

class CryptoPaymentService:
    """
    سرویس پرداخت کریپتویی با NOWPayments API.

    اگر NOWPAYMENTS_API_KEY خالی باشد، در حالت sandbox/placeholder کار می‌کند.
    """

    BASE_URL = "https://api.nowpayments.io/v1"

    def __init__(self) -> None:
        self._api_key = settings.nowpayments_api_key
        self._sandbox = not bool(self._api_key)

    @staticmethod
    def _http_error_message(prefix: str, exc: httpx.HTTPStatusError) -> str:
        response = exc.response
        detail = ""
        try:
            detail = response.text.strip()
        except Exception:
            detail = ""
        if len(detail) > 500:
            detail = detail[:500] + "..."
        suffix = f" — response: {detail}" if detail else ""
        return f"{prefix}: HTTP {response.status_code} {response.reason_phrase}{suffix}"

    # ── headers ──────────────────────────────

    @staticmethod
    def _headers(api_key: str) -> Dict[str, str]:
        return {
            "x-api-key": api_key or "sandbox",
            "Content-Type": "application/json",
        }

    # ── ایجاد invoice ─────────────────────────

    async def create_invoice(
        self,
        amount_usdt: float,
        order_id: str,
        inbound_id: int,
        expire_minutes: int = 30,
    ) -> InvoiceResult:
        """
        ایجاد invoice پرداخت USDT TRC-20.

        Args:
            amount_usdt: مبلغ به دلار/USDT
            order_id: شناسه سفارش داخلی
            inbound_id: برای callback شناسایی پلن
            expire_minutes: زمان انقضا به دقیقه (پیش‌فرض ۳۰)

        Returns:
            InvoiceResult با آدرس والت و اطلاعات پرداخت
        """
        runtime = await get_nowpayments_config()
        api_key = runtime["api_key"]
        pay_currency = runtime["pay_currency"] or "usdttrc20"
        ipn_url = runtime["ipn_url"] or settings.nowpayments_ipn_callback_url()
        if not api_key:
            return self._make_sandbox_invoice(amount_usdt, order_id, expire_minutes)

        payload = {
            "price_amount": amount_usdt,
            "price_currency": "usd",
            "pay_currency": pay_currency,
            "order_id": order_id,
            "order_description": f"VPN Subscription - inbound {inbound_id}",
            "ipn_callback_url": ipn_url,
            "success_url": "",
            "cancel_url": "",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/payment",
                    headers=self._headers(api_key),
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise PaymentAPIError(self._http_error_message("خطای HTTP از NOWPayments", exc)) from exc
        except httpx.TransportError as exc:
            raise PaymentAPIError(f"خطای اتصال به NOWPayments: {exc}") from exc

        expiry = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
        return InvoiceResult(
            payment_id=str(data["payment_id"]),
            order_id=order_id,
            pay_address=data.get("pay_address", ""),
            pay_amount=float(data.get("pay_amount", amount_usdt)),
            pay_currency=data.get("pay_currency", pay_currency),
            price_amount=amount_usdt,
            price_currency="usd",
            expiration_time=expiry,
            status=data.get("payment_status", "waiting"),
            qr_data=data.get("pay_address", ""),
            extra=data,
        )

    # ── ساخت Invoice Page (کاربر ارز انتخاب می‌کنه) ──────────────

    async def create_invoice_page(
        self,
        amount_usdt: float,
        order_id: str,
        expire_minutes: int = 30,
        success_url: str = "",
        cancel_url: str = "",
    ) -> InvoicePageResult:
        """
        ساخت Invoice در NOWPayments — کاربر به صفحه NOWPayments هدایت می‌شه
        و از بین ۱۰۰+ ارز انتخاب می‌کنه.

        طبق مستندات: POST /v1/invoice
        بدون pay_currency → کاربر خودش انتخاب می‌کنه

        Returns:
            InvoicePageResult.invoice_url → لینک صفحه انتخاب ارز
        """
        runtime = await get_nowpayments_config()
        api_key = runtime["api_key"]
        ipn_url = runtime["ipn_url"] or settings.nowpayments_ipn_callback_url()
        if not api_key:
            expiry = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
            return InvoicePageResult(
                invoice_id="sandbox_inv_" + uuid.uuid4().hex[:10],
                order_id=order_id,
                invoice_url="https://nowpayments.io/payment/?iid=SANDBOX_TEST",
                price_amount=amount_usdt,
                price_currency="usd",
                expiration_time=expiry,
            )

        payload = {
            "price_amount": amount_usdt,
            "price_currency": "usd",
            "order_id": order_id,
            "order_description": f"VPN Subscription",
            "ipn_callback_url": ipn_url,
        }
        if success_url:
            payload["success_url"] = success_url
        if cancel_url:
            payload["cancel_url"] = cancel_url

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self.BASE_URL}/invoice",
                    headers=self._headers(api_key),
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise PaymentAPIError(self._http_error_message("خطای HTTP از NOWPayments Invoice", exc)) from exc
        except httpx.TransportError as exc:
            raise PaymentAPIError(f"خطای اتصال به NOWPayments: {exc}") from exc

        expiry = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
        return InvoicePageResult(
            invoice_id=str(data.get("id", "")),
            order_id=order_id,
            invoice_url=data.get("invoice_url", ""),
            price_amount=float(data.get("price_amount", amount_usdt)),
            price_currency=str(data.get("price_currency", "usd")),
            expiration_time=expiry,
        )

    # ── بررسی وضعیت پرداخت ──────────────────

    async def get_payment_status(self, payment_id: str) -> PaymentStatus:
        """
        بررسی وضعیت یک invoice از NOWPayments.

        Args:
            payment_id: شناسه پرداخت از create_invoice

        Returns:
            PaymentStatus با وضعیت جدید
        """
        runtime = await get_nowpayments_config()
        pay_currency = runtime["pay_currency"] or settings.nowpayments_pay_currency
        api_key = runtime["api_key"]
        if not api_key:
            return PaymentStatus(
                payment_id=payment_id,
                order_id="sandbox",
                status="waiting",
                pay_amount=1.0,
                actually_paid=0.0,
                pay_currency=pay_currency,
            )

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/payment/{payment_id}",
                    headers=self._headers(api_key),
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise PaymentNotFoundError(f"پرداخت {payment_id} پیدا نشد") from exc
            raise PaymentAPIError(self._http_error_message("خطای HTTP از NOWPayments status", exc)) from exc
        except httpx.TransportError as exc:
            raise PaymentAPIError(f"خطای اتصال: {exc}") from exc

        return PaymentStatus(
            payment_id=str(data["payment_id"]),
            order_id=str(data.get("order_id", "")),
            status=data.get("payment_status", "unknown"),
            pay_amount=float(data.get("pay_amount", 0)),
            actually_paid=float(data.get("actually_paid", 0)),
            pay_currency=data.get("pay_currency", ""),
            outcome_amount=float(data["outcome_amount"]) if data.get("outcome_amount") else None,
        )

    # ── تأیید webhook ────────────────────────

    async def verify_ipn_signature(
        self,
        headers: Dict[str, str],
        body: bytes,
    ) -> bool:
        """
        تأیید امضای IPN webhook از NOWPayments.
        از HMAC-SHA512 با ipn_secret_key استفاده می‌کند.
        """
        import hashlib
        import hmac
        import json

        runtime = await get_nowpayments_config()
        ipn_secret = runtime["ipn_secret"]
        if not ipn_secret:
            logger.warning("IPN secret key تنظیم نشده — تأیید signature رد می‌شود.")
            return True  # در حالت توسعه قبول می‌کنیم

        received_sig = headers.get("x-nowpayments-sig", "")
        if not received_sig:
            return False

        try:
            body_json   = json.loads(body)
            sorted_body = _sort_keys_recursive(body_json)
            sorted_str  = json.dumps(sorted_body, separators=(",", ":"), sort_keys=False)
        except Exception:
            return False

        expected_sig = hmac.new(
            key=ipn_secret.encode("utf-8"),
            msg=sorted_str.encode("utf-8"),
            digestmod=hashlib.sha512,
        ).hexdigest()

        return hmac.compare_digest(expected_sig, received_sig)

    # ── sandbox helper ───────────────────────

    def _make_sandbox_invoice(
        self,
        amount_usdt: float,
        order_id: str,
        expire_minutes: int,
    ) -> InvoiceResult:
        """ایجاد invoice ساختگی برای محیط توسعه."""
        fake_address = "TRX_SANDBOX_" + uuid.uuid4().hex[:20].upper()
        expiry = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
        return InvoiceResult(
            payment_id="sandbox_" + uuid.uuid4().hex[:12],
            order_id=order_id,
            pay_address=fake_address,
            pay_amount=amount_usdt,
            pay_currency="usdttrc20",
            price_amount=amount_usdt,
            price_currency="usd",
            expiration_time=expiry,
            status="waiting",
            qr_data=fake_address,
        )

    def is_paid(self, status: str) -> bool:
        """بررسی موفق بودن پرداخت."""
        return status in PAID_STATUSES

    def is_failed(self, status: str) -> bool:
        """بررسی ناموفق بودن پرداخت."""
        return status in FAILED_STATUSES

    def is_pending(self, status: str) -> bool:
        """بررسی در انتظار بودن پرداخت."""
        return status in PENDING_STATUSES


# singleton برای استفاده در هندلرها
crypto_payment_service = CryptoPaymentService()
