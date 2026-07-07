from __future__ import annotations

from config import settings


async def _get_settings(keys: list[str]) -> dict:
    from database import AsyncSessionLocal
    from database.crud import get_setting

    async with AsyncSessionLocal() as session:
        result = {}
        for key in keys:
            result[key] = await get_setting(session, key, "")
        return result


async def get_nowpayments_config() -> dict:
    values = await _get_settings([
        "NOWPAYMENTS_API_KEY",
        "NOWPAYMENTS_IPN_SECRET",
        "NOWPAYMENTS_IPN_URL",
        "NOWPAYMENTS_PAY_CURRENCY",
    ])
    return {
        "api_key": (values["NOWPAYMENTS_API_KEY"] or settings.nowpayments_api_key or "").strip(),
        "ipn_secret": (values["NOWPAYMENTS_IPN_SECRET"] or settings.nowpayments_ipn_secret or "").strip(),
        "ipn_url": (values["NOWPAYMENTS_IPN_URL"] or settings.nowpayments_ipn_url or "").strip(),
        "pay_currency": (values["NOWPAYMENTS_PAY_CURRENCY"] or settings.nowpayments_pay_currency or "usdttrc20").strip(),
    }


async def get_maxelpay_config() -> dict:
    values = await _get_settings([
        "MAXELPAY_API_KEY",
        "MAXELPAY_WEBHOOK_SECRET",
        "MAXELPAY_WEBHOOK_URL",
    ])
    return {
        "api_key": (values["MAXELPAY_API_KEY"] or settings.maxelpay_api_key or "").strip(),
        "webhook_secret": (values["MAXELPAY_WEBHOOK_SECRET"] or settings.maxelpay_webhook_secret or "").strip(),
        "webhook_url": (values["MAXELPAY_WEBHOOK_URL"] or settings.maxelpay_webhook_url or "").strip(),
    }
