"""Blocked user helpers backed by AdminSetting."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import get_setting, set_setting

BLOCKED_USERS_KEY = "blocked_telegram_ids"

BLOCKED_MESSAGE_FA = (
    "⛔ دسترسی شما به این ربات قطع شده است.\n"
    "برای پیگیری، لطفاً با پشتیبانی تماس بگیرید."
)


def _parse_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.replace("\n", ",").split(","):
        item = part.strip()
        if item.lstrip("-").isdigit():
            ids.add(int(item))
    return ids


def _format_ids(ids: set[int]) -> str:
    return ",".join(str(i) for i in sorted(ids))


async def get_blocked_user_ids(session: AsyncSession) -> set[int]:
    raw = await get_setting(session, BLOCKED_USERS_KEY, "")
    return _parse_ids(raw)


async def is_user_blocked(session: AsyncSession, telegram_id: int) -> bool:
    return telegram_id in await get_blocked_user_ids(session)


async def set_user_blocked(
    session: AsyncSession,
    telegram_id: int,
    blocked: bool,
) -> None:
    ids = await get_blocked_user_ids(session)
    if blocked:
        ids.add(telegram_id)
    else:
        ids.discard(telegram_id)
    await set_setting(session, BLOCKED_USERS_KEY, _format_ids(ids))
