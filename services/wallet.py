"""
services/wallet.py — عملیات کیف پول کاربر
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import credit_user_wallet, debit_user_wallet, get_user_wallet_balance


async def credit_wallet(session: AsyncSession, user_id: int, amount_usdt: float) -> float:
    """افزایش موجودی کیف پول و برگرداندن موجودی جدید."""
    return await credit_user_wallet(session, user_id, amount_usdt)


async def wallet_balance(session: AsyncSession, user_id: int) -> float:
    """خواندن موجودی کیف پول."""
    return await get_user_wallet_balance(session, user_id)


async def debit_wallet(session: AsyncSession, user_id: int, amount_usdt: float) -> float:
    """کاهش موجودی کیف پول و برگرداندن موجودی جدید."""
    return await debit_user_wallet(session, user_id, amount_usdt)
