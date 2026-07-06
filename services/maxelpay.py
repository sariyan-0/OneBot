"""
services/maxelpay.py — MaxelPay payment gateway client

API Reference: https://docs.maxelpay.com
Base URL: https://api.maxelpay.com/api/v1
Auth: X-API-KEY header

Webhook payload (from official docs — verified 2026-07):
{
  "event": "payment.completed",
  "timestamp": "2024-12-15T12:25:00Z",
  "data": {
    "sessionId": "ps_abc123...",
    "orderId":   "order_123",
    "amount":    99.99,
    "currency":  "USD",
    "status":    "paid",
    "txHash":    "0x123...",
    "paidAt":    "2024-12-15T12:25:00Z",
    "network":   "BSC",
    "token":     "USDT"       ← field name is "token" not "tokenSymbol"
  }
}

Webhook events (official list):
  payment.completed  — confirmed, activate subscription
  payment.failed     — failed/rejected
  payment.expired    — session expired without payment
  payment.processing — wallet assigned, waiting for payment (do NOT activate yet)

Signature: "coming soon" per docs — X-MaxelPay-Signature / HMAC-SHA256 (optional for now)
Response expected: JSON { "received": true }
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from loguru import logger


# ──────────────────────────────────────────────
# Constants — aligned with official docs
# ──────────────────────────────────────────────

BASE_URL = "https://api.maxelpay.com/api/v1"

# Webhook events (official list from docs)
PAID_EVENTS       = {"payment.completed"}                            # activate subscription
FAILED_EVENTS     = {"payment.failed", "payment.expired"}            # mark as failed
PROCESSING_EVENTS = {"payment.processing"}                           # wallet assigned — wait

# Session status values (from official docs — Get Session Status)
#   pending, processing, paid, partially_paid, overpaid, expired, cancelled
PAID_STATUSES   = {"paid", "overpaid"}           # fully paid or overpaid → activate
PARTIAL_STATUSES = {"partially_paid"}            # partial — wait, do not activate yet
FAILED_STATUSES = {"expired", "cancelled", "failed", "refunded"}


# ──────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────

class MaxelPayError(Exception):
    """General MaxelPay error."""

class MaxelPayAPIError(MaxelPayError):
    """HTTP error from MaxelPay API."""
    def __init__(self, status: int, message: str):
        self.status  = status
        self.message = message
        super().__init__(f"MaxelPay API {status}: {message}")


# ──────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────

@dataclass
class MaxelPaySession:
    session_id:   str
    checkout_url: str
    order_id:     str
    amount:       float
    currency:     str
    status:       str
    created_at:   datetime


@dataclass
class MaxelPayStatus:
    session_id:  str
    order_id:    str
    status:      str    # raw value from API/webhook (lowercase) e.g. "paid"
    event:       str = ""  # e.g. "payment.completed"
    amount:      Optional[float] = None
    currency:    Optional[str]   = None
    tx_hash:     Optional[str]   = None
    paid_at:     Optional[str]   = None
    network:     Optional[str]   = None
    token:       Optional[str]   = None  # e.g. "USDT"

    @property
    def is_paid(self) -> bool:
        # event takes priority (most reliable for webhooks)
        if self.event and self.event.lower() in PAID_EVENTS:
            return True
        # fallback: check status field (for polling via get_status)
        return self.status.lower() in PAID_STATUSES

    @property
    def is_partial(self) -> bool:
        """Partial payment received — do NOT activate, notify admin."""
        return self.status.lower() in PARTIAL_STATUSES

    @property
    def is_failed(self) -> bool:
        if self.event and self.event.lower() in FAILED_EVENTS:
            return True
        return self.status.lower() in FAILED_STATUSES

    @property
    def is_processing(self) -> bool:
        """Wallet assigned, waiting for user payment — do NOT activate yet."""
        return self.event.lower() in PROCESSING_EVENTS if self.event else (
            self.status.lower() == "processing"
        )


# ──────────────────────────────────────────────
# Signature verification
# ──────────────────────────────────────────────

def verify_maxelpay_signature(body_bytes: bytes, received_sig: str, secret_key: str) -> bool:
    """
    Verify X-MaxelPay-Signature header.
    HMAC-SHA256(JSON.stringify(body), secret_key) → hex digest
    """
    if not secret_key:
        logger.warning("MAXELPAY_WEBHOOK_SECRET not set — skipping signature check (dev mode)")
        return True
    if not received_sig:
        logger.warning("X-MaxelPay-Signature header missing")
        return False
    try:
        # parse and re-serialize for consistent key order
        body_json = json.loads(body_bytes)
        canonical = json.dumps(body_json, separators=(",", ":"), sort_keys=False)
    except (json.JSONDecodeError, Exception):
        logger.error("MaxelPay webhook: invalid JSON body")
        return False

    expected = hmac.new(
        secret_key.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, received_sig)


# ──────────────────────────────────────────────
# MaxelPay API client
# ──────────────────────────────────────────────

class MaxelPayClient:
    """
    Async client for MaxelPay API.

    Args:
        api_key      — API key from MaxelPay dashboard
        webhook_url  — public HTTPS URL to receive webhook notifications
        success_url  — redirect URL after successful payment
        cancel_url   — redirect URL after cancellation
    """

    def __init__(
        self,
        api_key:     str,
        webhook_url: str,
        success_url: str = "",
        cancel_url:  str = "",
    ):
        self.api_key     = api_key
        self.webhook_url = webhook_url
        self.success_url = success_url or "https://t.me/"
        self.cancel_url  = cancel_url  or "https://t.me/"

    def _headers(self) -> dict:
        return {
            "X-API-KEY":    self.api_key,
            "Content-Type": "application/json",
        }

    async def create_session(
        self,
        order_id:           str,
        amount_usd:         float,
        description:        str = "",
        customer_email:     str = "",
        customer_name:      str = "",
        expiration_minutes: int = 60,
        metadata:           dict | None = None,
    ) -> MaxelPaySession:
        """
        Create a new payment session.
        Returns MaxelPaySession with checkout_url.

        POST /api/v1/payments/sessions
        Required: orderId, amount
        Optional: currency, description, customerEmail, customerName,
                  successUrl, cancelUrl, callbackUrl,
                  expirationMinutes (5-1440), metadata (key-value)
        """
        payload: dict = {
            "orderId":            order_id,
            "amount":             round(amount_usd, 2),
            "currency":           "USD",
            "description":        description or f"VPN Subscription — {order_id}",
            "successUrl":         self.success_url,
            "cancelUrl":          self.cancel_url,
            "callbackUrl":        self.webhook_url,
            "expirationMinutes":  max(5, min(1440, expiration_minutes)),
        }
        if customer_email:
            payload["customerEmail"] = customer_email
        if customer_name:
            payload["customerName"] = customer_name
        if metadata:
            payload["metadata"] = metadata

        async with aiohttp.ClientSession() as http:
            async with http.post(
                f"{BASE_URL}/payments/sessions",
                json=payload,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status not in (200, 201):
                    msg = data.get("message") or data.get("error") or str(data)
                    raise MaxelPayAPIError(resp.status, msg)

        # MaxelPay wraps payload inside data{} key:
        # { "success": true, "message": "...", "data": { "sessionId": "...", "checkoutUrl": "..." } }
        inner = data.get("data") or data

        logger.info(
            f"MaxelPay create_session response | order={order_id} | "
            f"root_keys={list(data.keys()) if isinstance(data, dict) else '?'} | "
            f"inner_keys={list(inner.keys()) if isinstance(inner, dict) else '?'} | "
            f"sessionId={inner.get('sessionId') or inner.get('id') or 'MISSING'} | "
            f"checkoutUrl={inner.get('checkoutUrl') or inner.get('url') or 'MISSING'}"
        )

        checkout_url = (
            inner.get("checkoutUrl")
            or inner.get("checkout_url")
            or inner.get("url")
            or inner.get("payment_url")
            or inner.get("paymentUrl")
            or ""
        )
        session_id = (
            inner.get("sessionId")
            or inner.get("session_id")
            or inner.get("id")
            or ""
        )

        return MaxelPaySession(
            session_id   = session_id,
            checkout_url = checkout_url,
            order_id     = order_id,
            amount       = amount_usd,
            currency     = "USD",
            status       = inner.get("status") or data.get("status") or "pending",
            created_at   = datetime.now(timezone.utc),
        )

    async def get_status(self, session_id: str) -> MaxelPayStatus:
        """
        Get current status of a payment session.

        GET /api/v1/payments/sessions/{sessionId}/status
        Response fields: sessionId, orderId, status, amount, currency, ...
        """
        async with aiohttp.ClientSession() as http:
            async with http.get(
                f"{BASE_URL}/payments/sessions/{session_id}/status",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    msg = data.get("message") or data.get("error") or str(data)
                    raise MaxelPayAPIError(resp.status, msg)

        # status may be nested in data{} or at root depending on endpoint
        inner = data.get("data") or data
        status = (
            inner.get("status")
            or data.get("status")
            or "pending"
        )
        return MaxelPayStatus(
            session_id = session_id,
            order_id   = inner.get("orderId") or inner.get("order_id") or "",
            status     = status.lower(),
            amount     = inner.get("amount"),
            currency   = inner.get("currency"),
            tx_hash    = inner.get("txHash"),
            paid_at    = inner.get("paidAt"),
            network    = inner.get("network"),
            token      = inner.get("token"),            # "USDT" — matches official docs
        )

    @staticmethod
    def parse_webhook(body: dict) -> MaxelPayStatus:
        """
        Parse webhook payload from MaxelPay.

        Official structure:
        {
          "event": "payment.completed",
          "timestamp": "...",
          "data": {
            "sessionId": "...",
            "orderId": "...",
            "status": "paid",
            "amount": 99.99,
            ...
          }
        }
        """
        event = (body.get("event") or "").lower()
        # data is nested under "data" key
        inner = body.get("data") or body

        status = (
            inner.get("status")
            or body.get("status")
            or "unknown"
        ).lower()

        session_id = (
            inner.get("sessionId")
            or inner.get("session_id")
            or inner.get("id")
            or body.get("sessionId")
            or ""
        )
        order_id = (
            inner.get("orderId")
            or inner.get("order_id")
            or body.get("orderId")
            or ""
        )

        logger.info(
            f"MaxelPay webhook parsed | event={event!r} | "
            f"session_id={session_id!r} | order_id={order_id!r} | "
            f"status={status!r} | token={inner.get('token')!r} | "
            f"network={inner.get('network')!r} | txHash={inner.get('txHash')!r}"
        )

        return MaxelPayStatus(
            session_id = session_id,
            order_id   = order_id,
            status     = status,
            event      = event,
            amount     = inner.get("amount"),
            currency   = inner.get("currency"),
            tx_hash    = inner.get("txHash"),
            paid_at    = inner.get("paidAt"),          # "2024-12-15T12:25:00Z"
            network    = inner.get("network"),          # "BSC", "Ethereum", etc.
            token      = inner.get("token"),            # "USDT" — NOT "tokenSymbol"
        )
