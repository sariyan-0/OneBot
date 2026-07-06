"""
services/referral.py — منطق سیستم دعوت و referral
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.crud import (
    create_referral,
    get_referral_stats,
    get_user_by_referral_code,
    get_user_by_telegram_id,
    mark_referral_rewarded,
    set_referral_code,
)
from database.models import User


# پاداش پیش‌فرض به ازای هر دعوت (روز)
DEFAULT_REWARD_DAYS = 3


@dataclass
class ReferralStats:
    total_referrals: int
    rewarded_referrals: int
    total_reward_days: int
    referral_link: str
    referral_code: str


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
    # اگر کاربر از قبل referral داشته باشد
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
        referral_link=link,
        referral_code=code,
    )
