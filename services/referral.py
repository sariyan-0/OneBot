"""
services/referral.py — منطق سیستم دعوت و referral
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import (
    create_referral,
    create_referral_commission,
    get_referral_by_referred_id,
    get_referral_commission_by_payment_id,
    get_referral_stats,
    get_setting,
    get_user_by_referral_code,
    mark_referral_rewarded,
    set_referral_code,
)
from database.models import Payment, User
from services.card_payment import toman_from_usdt
from services.wallet import credit_wallet


# پاداش پیش‌فرض به ازای هر دعوت (روز)
DEFAULT_REWARD_DAYS = 3
DEFAULT_REFERRAL_COMMISSION_PERCENT = 10.0


@dataclass
class ReferralStats:
    total_referrals: int
    rewarded_referrals: int
    total_reward_days: int
    total_commission_usdt: float
    total_commission_toman: int
    referral_link: str
    referral_code: str


@dataclass
class ReferralCommissionResult:
    referrer_id: int
    referred_id: int
    payment_id: int
    amount_usdt: float
    amount_toman: int
    percent: float
    wallet_currency: str
    new_wallet_balance: float


async def generate_referral_code(length: int = 8) -> str:
    """تولید کد referral منحصر به فرد."""
    return uuid.uuid4().hex[:length].upper()


async def get_or_create_referral_code(
    session: AsyncSession,
    user: User,
) -> str:
    """
    برگرداندن کد referral موجود یا ایجاد کد جدید.

    Args:
        session: AsyncSession
        user: شیء User از دیتابیس

    Returns:
        کد referral (8 کاراکتر بزرگ)
    """
    if user.referral_code:
        return user.referral_code

    # تولید کد منحصر به فرد
    for _ in range(5):
        code = await generate_referral_code()
        existing = await get_user_by_referral_code(session, code)
        if not existing:
            await set_referral_code(session, user.id, code)
            logger.info(f"کد referral '{code}' برای user_id={user.id} ایجاد شد")
            return code

    # fallback با شناسه کاربر
    code = f"U{user.id:06d}"
    await set_referral_code(session, user.id, code)
    return code


def build_referral_link(bot_username: str, referral_code: str) -> str:
    """ساخت لینک دعوت تلگرام."""
    return f"https://t.me/{bot_username}?start=ref_{referral_code}"


async def process_referral(
    session: AsyncSession,
    new_user: User,
    referral_code: str,
    bot_username: str = "",
) -> Optional[str]:
    """
    پردازش referral هنگام ثبت‌نام کاربر جدید.

    Args:
        session: AsyncSession
        new_user: کاربر تازه‌وارد
        referral_code: کد دعوت استخراج‌شده از /start
        bot_username: نام کاربری ربات (برای لاگ)

    Returns:
        نام کاربر دعوت‌کننده در صورت موفقیت، None در صورت شکست
    """
    if new_user.referred_by:
        return None

    referrer = await get_user_by_referral_code(session, referral_code)
    if not referrer:
        logger.warning(f"کد referral '{referral_code}' معتبر نیست")
        return None

    if referrer.id == new_user.id:
        logger.warning(f"کاربر {new_user.id} نمی‌تواند خودش را دعوت کند")
        return None

    await create_referral(
        session=session,
        referrer_id=referrer.id,
        referred_id=new_user.id,
        reward_days=DEFAULT_REWARD_DAYS,
    )

    referrer_name = referrer.first_name or referrer.username or f"#{referrer.id}"
    logger.success(f"Referral ثبت شد: {referrer_name} → user_id={new_user.id}")
    return referrer_name


async def get_referral_commission_percent(session: AsyncSession) -> float:
    raw = await get_setting(session, "referral_commission_percent", str(int(DEFAULT_REFERRAL_COMMISSION_PERCENT)))
    try:
        value = float(raw)
    except Exception:
        value = DEFAULT_REFERRAL_COMMISSION_PERCENT
    return max(0.0, min(100.0, value))


async def grant_referral_commission_for_payment(
    session: AsyncSession,
    payment: Payment,
    *,
    explicit_rate_toman: int = 0,
    exact_toman_source: int = 0,
) -> Optional[ReferralCommissionResult]:
    """
    برای هر پرداخت خارجی تأیید‌شده، کمیسیون referral را فقط یک‌بار ثبت و به کیف پول referrer اضافه می‌کند.
    پرداخت‌های داخلی کیف پول شامل کمیسیون نیستند.
    """
    if not payment or getattr(payment, "id", 0) <= 0:
        return None
    if str(getattr(payment, "payment_method", "")).lower().startswith("wallet"):
        return None

    existing = await get_referral_commission_by_payment_id(session, payment.id)
    if existing:
        return None

    referred_user = await session.get(User, payment.user_id)
    if not referred_user or not referred_user.referred_by:
        return None
    if referred_user.referred_by == referred_user.id:
        return None

    referral = await get_referral_by_referred_id(session, referred_user.id)
    if not referral:
        return None

    percent = await get_referral_commission_percent(session)
    if percent <= 0:
        return None

    payment_method = str(getattr(payment, "payment_method", "")).lower()
    amount_usdt = float(getattr(payment, "amount_usdt", 0.0) or 0.0)
    if payment_method == "card":
        source_toman = int(exact_toman_source or 0)
        if source_toman <= 0 and int(getattr(payment, "amount_rial", 0) or 0) > 0:
            source_toman = int(int(getattr(payment, "amount_rial", 0) or 0) / 10)
        commission_toman = round(source_toman * percent / 100.0) if source_toman > 0 else 0
        if commission_toman <= 0:
            return None
        commission_usdt = 0.0
        wallet_currency = "toman"
    else:
        commission_usdt = round(amount_usdt * percent / 100.0, 8)
        if commission_usdt <= 0:
            return None
        rate_toman = int(explicit_rate_toman or 0)
        if rate_toman <= 0:
            raw_rate = await get_setting(session, "usdt_to_toman_rate", "0")
            try:
                rate_toman = int(raw_rate)
            except Exception:
                rate_toman = 0
        commission_toman = toman_from_usdt(commission_usdt, rate_toman) if rate_toman > 0 else 0
        wallet_currency = "usd"

    referrer = await session.get(User, referral.referrer_id)
    if not referrer:
        return None

    if wallet_currency == "usd":
        new_balance = await credit_wallet(session, referral.referrer_id, commission_usdt, currency="usd")
    else:
        new_balance = await credit_wallet(session, referral.referrer_id, commission_toman, currency="toman")

    commission = await create_referral_commission(
        session=session,
        referrer_id=referral.referrer_id,
        referred_id=referral.referred_id,
        payment_id=payment.id,
        percent=percent,
        amount_usdt=commission_usdt,
        amount_toman=commission_toman,
    )
    if not referral.reward_granted:
        await mark_referral_rewarded(session, referral.id)
    await session.refresh(referrer)
    return ReferralCommissionResult(
        referrer_id=commission.referrer_id,
        referred_id=commission.referred_id,
        payment_id=commission.payment_id,
        amount_usdt=commission.amount_usdt,
        amount_toman=commission.amount_toman,
        percent=commission.percent,
        wallet_currency=wallet_currency,
        new_wallet_balance=float(new_balance or 0.0),
    )


async def get_user_referral_stats(
    session: AsyncSession,
    user: User,
    bot_username: str,
) -> ReferralStats:
    """
    دریافت آمار referral کاربر.

    Args:
        session: AsyncSession
        user: شیء User
        bot_username: نام کاربری ربات برای ساخت لینک

    Returns:
        ReferralStats با تمام اطلاعات
    """
    code = await get_or_create_referral_code(session, user)
    link = build_referral_link(bot_username, code)
    stats = await get_referral_stats(session, user.id)

    return ReferralStats(
        total_referrals=stats["total_referrals"],
        rewarded_referrals=stats["rewarded_referrals"],
        total_reward_days=stats["total_reward_days"],
        total_commission_usdt=float(stats["total_commission_usdt"]),
        total_commission_toman=int(stats["total_commission_toman"]),
        referral_link=link,
        referral_code=code,
    )
