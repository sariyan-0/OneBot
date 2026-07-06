"""
services/payment_methods.py — فعال/غیرفعال کردن روش‌های پرداخت

کلیدها در AdminSetting:
  payment_crypto_enabled  → "1" / "0"
  payment_card_enabled    → "1" / "0"
  payment_crypto_invoice  → "1" / "0"   (فقط برای NOWPayments)
  crypto_gateway          → "nowpayments" | "maxelpay"
"""
from __future__ import annotations

CRYPTO_KEY         = "payment_crypto_enabled"
CARD_KEY           = "payment_card_enabled"
CRYPTO_INVOICE_KEY = "payment_crypto_invoice"   # "1" = Invoice | "0" = USDT مستقیم
CRYPTO_GATEWAY_KEY = "crypto_gateway"           # "nowpayments" | "maxelpay"


async def is_crypto_enabled() -> bool:
    from database import AsyncSessionLocal
    from database.crud import get_setting
    async with AsyncSessionLocal() as s:
        return await get_setting(s, CRYPTO_KEY, "1") == "1"


async def is_card_enabled() -> bool:
    from database import AsyncSessionLocal
    from database.crud import get_setting
    async with AsyncSessionLocal() as s:
        return await get_setting(s, CARD_KEY, "0") == "1"


async def set_crypto_enabled(val: bool) -> None:
    from database import AsyncSessionLocal
    from database.crud import set_setting
    async with AsyncSessionLocal() as s:
        await set_setting(s, CRYPTO_KEY, "1" if val else "0")


async def set_card_enabled(val: bool) -> None:
    from database import AsyncSessionLocal
    from database.crud import set_setting
    async with AsyncSessionLocal() as s:
        await set_setting(s, CARD_KEY, "1" if val else "0")


async def is_crypto_invoice() -> bool:
    """True = حالت Invoice (انتخاب آزاد ارز) | False = USDT TRC-20 مستقیم."""
    from database import AsyncSessionLocal
    from database.crud import get_setting
    async with AsyncSessionLocal() as s:
        return await get_setting(s, CRYPTO_INVOICE_KEY, "0") == "1"


async def set_crypto_invoice(val: bool) -> None:
    from database import AsyncSessionLocal
    from database.crud import set_setting
    async with AsyncSessionLocal() as s:
        await set_setting(s, CRYPTO_INVOICE_KEY, "1" if val else "0")


async def get_crypto_gateway() -> str:
    """درگاه فعال کریپتو: 'nowpayments' یا 'maxelpay'."""
    from database import AsyncSessionLocal
    from database.crud import get_setting
    async with AsyncSessionLocal() as s:
        return await get_setting(s, CRYPTO_GATEWAY_KEY, "nowpayments")


async def set_crypto_gateway(gateway: str) -> None:
    """تنظیم درگاه کریپتو: 'nowpayments' یا 'maxelpay'."""
    if gateway not in ("nowpayments", "maxelpay"):
        raise ValueError(f"درگاه نامعتبر: {gateway}")
    from database import AsyncSessionLocal
    from database.crud import set_setting
    async with AsyncSessionLocal() as s:
        await set_setting(s, CRYPTO_GATEWAY_KEY, gateway)


async def get_payment_status() -> dict:
    """وضعیت همه روش‌های پرداخت به صورت dict."""
    crypto         = await is_crypto_enabled()
    card           = await is_card_enabled()
    crypto_invoice = await is_crypto_invoice()
    crypto_gateway = await get_crypto_gateway()
    return {
        "crypto":         crypto,
        "card":           card,
        "crypto_invoice": crypto_invoice,
        "crypto_gateway": crypto_gateway,
    }
