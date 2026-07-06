"""
services/card_payment.py — مدیریت پرداخت کارت به کارت

قابلیت‌ها:
  • ذخیره/بازیابی شماره کارت + نام صاحب کارت در AdminSetting
  • نرخ تبدیل دلار ← تومان (ادمین تنظیم می‌کند)
  • محاسبه مبلغ ریالی برای نمایش به کاربر
"""
from __future__ import annotations
from typing import Optional

CARD_NUMBER_KEY = "card_number"
CARD_HOLDER_KEY = "card_holder"
USDT_TO_TOMAN_KEY = "usdt_to_toman_rate"

DEFAULT_RATE = 90_000  # ۹۰,۰۰۰ تومان به ازای هر دلار (پیش‌فرض)


async def get_card_info() -> dict:
    """دریافت شماره کارت و نام صاحب کارت از دیتابیس."""
    from database import AsyncSessionLocal
    from database.crud import get_setting
    async with AsyncSessionLocal() as session:
        number = await get_setting(session, CARD_NUMBER_KEY, "")
        holder = await get_setting(session, CARD_HOLDER_KEY, "")
        rate   = await get_setting(session, USDT_TO_TOMAN_KEY, str(DEFAULT_RATE))
    return {
        "number": number,
        "holder": holder,
        "rate":   int(rate) if rate.isdigit() else DEFAULT_RATE,
    }


async def set_card_info(number: str, holder: str) -> None:
    """ذخیره شماره کارت و نام صاحب کارت."""
    from database import AsyncSessionLocal
    from database.crud import set_setting
    async with AsyncSessionLocal() as session:
        await set_setting(session, CARD_NUMBER_KEY, number.strip())
        await set_setting(session, CARD_HOLDER_KEY, holder.strip())


async def set_usdt_rate(rate: int) -> None:
    """تنظیم نرخ تبدیل دلار به تومان."""
    from database import AsyncSessionLocal
    from database.crud import set_setting
    async with AsyncSessionLocal() as session:
        await set_setting(session, USDT_TO_TOMAN_KEY, str(rate))


def calc_rial_amount(usdt_amount: float, rate_toman: int) -> tuple[int, int]:
    """
    محاسبه مبلغ ریالی و تومانی.
    Returns: (rial, toman)
    """
    toman = int(usdt_amount * rate_toman)
    rial  = toman * 10
    return rial, toman


def fmt_card_number(raw: str) -> str:
    """فرمت‌دهی شماره کارت: XXXX-XXXX-XXXX-XXXX"""
    digits = raw.replace("-", "").replace(" ", "")
    if len(digits) == 16:
        return f"{digits[:4]}-{digits[4:8]}-{digits[8:12]}-{digits[12:]}"
    return raw
