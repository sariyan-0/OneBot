"""
services/wallet.py — عملیات کیف پول کاربر
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import (
    credit_user_wallet,
    debit_user_wallet,
    get_user_wallet_balance,
    get_user_wallet_balance_toman,
    get_user_wallet_balances,
)


async def credit_wallet(
    session: AsyncSession,
    user_id: int,
    amount: float,
    currency: str = "usd",
) -> float:
    """افزایش موجودی کیف پول و برگرداندن موجودی جدید."""
    return await credit_user_wallet(session, user_id, amount, currency=currency)


async def wallet_balance(session: AsyncSession, user_id: int) -> float:
    """خواندن موجودی کیف پول."""
    return await get_user_wallet_balance(session, user_id)


async def wallet_balance_toman(session: AsyncSession, user_id: int) -> int:
    """خواندن موجودی تومان کیف پول."""
    return await get_user_wallet_balance_toman(session, user_id)


async def wallet_balances(session: AsyncSession, user_id: int) -> tuple[float, int]:
    """خواندن هر دو موجودی کیف پول."""
    return await get_user_wallet_balances(session, user_id)


async def debit_wallet(
    session: AsyncSession,
    user_id: int,
    amount: float,
    currency: str = "usd",
) -> float:
    """کاهش موجودی کیف پول و برگرداندن موجودی جدید."""
    return await debit_user_wallet(session, user_id, amount, currency=currency)
