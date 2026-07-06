"""Middleware that records incoming user text for the web admin live feed."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from services.activity_log import log_activity


async def _admin_command_name() -> str:
    try:
        from database import AsyncSessionLocal
        from database.crud import get_setting

        async with AsyncSessionLocal() as session:
            return await get_setting(session, "admin_login_command", "admin_secret")
    except Exception:
        return "admin_secret"


class ActivityLogMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            text = event.text or event.caption or ""
            if text:
                command = await _admin_command_name()
                visible_text = text
                if text.startswith("/") and text.split()[0].lstrip("/").split("@")[0] == command:
                    visible_text = f"/{command} [redacted]"
                user = event.from_user
                await log_activity(
                    direction="incoming",
                    event_type="message",
                    telegram_id=user.id if user else event.chat.id,
                    username=user.username if user else None,
                    text=visible_text,
                )
        return await handler(event, data)
