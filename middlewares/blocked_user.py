"""Middleware that prevents blocked users from using the bot."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from loguru import logger

from database import AsyncSessionLocal
from services.blocked_users import BLOCKED_MESSAGE_FA, is_user_blocked


class BlockedUserMiddleware(BaseMiddleware):
    def __init__(self, admin_ids: list[int] | None = None) -> None:
        self._admin_ids = set(admin_ids or [])

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        if not user or user.id in self._admin_ids:
            return await handler(event, data)

        async with AsyncSessionLocal() as session:
            blocked = await is_user_blocked(session, user.id)

        if not blocked:
            return await handler(event, data)

        logger.warning(f"Blocked user {user.id} attempted to use the bot.")
        if isinstance(event, Message):
            await event.answer(BLOCKED_MESSAGE_FA)
        elif isinstance(event, CallbackQuery):
            await event.answer(BLOCKED_MESSAGE_FA, show_alert=True)
        return None
