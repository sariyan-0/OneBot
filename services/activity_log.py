"""Activity logging for the web admin live feed."""

from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.methods import SendDocument, SendMessage, SendPhoto
from aiogram.methods.base import TelegramMethod
from loguru import logger
from sqlalchemy import select

from database import AsyncSessionLocal
from database.models import ActivityLog


MAX_ACTIVITY_TEXT = 700


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > MAX_ACTIVITY_TEXT:
        return f"{text[:MAX_ACTIVITY_TEXT - 1]}..."
    return text


async def log_activity(
    *,
    direction: str,
    event_type: str,
    telegram_id: int | None = None,
    username: str | None = None,
    text: str = "",
) -> None:
    """Persist one activity row. Failures are intentionally non-fatal."""
    try:
        async with AsyncSessionLocal() as session:
            session.add(
                ActivityLog(
                    direction=direction,
                    event_type=event_type,
                    telegram_id=telegram_id,
                    username=(username or "").lstrip("@") or None,
                    text=_clean_text(text),
                )
            )
            await session.commit()
    except Exception as exc:
        logger.debug(f"Activity log skipped: {exc}")


async def get_recent_activity_logs(limit: int = 40) -> list[ActivityLog]:
    async with AsyncSessionLocal() as session:
        return (
            await session.execute(
                select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(limit)
            )
        ).scalars().all()


class ActivityLoggingBot(Bot):
    """Bot subclass that records outgoing message/photo/document text in ActivityLog."""

    async def __call__(
        self,
        method: TelegramMethod[Any],
        request_timeout: int | None = None,
    ) -> Any:
        result = await super().__call__(method, request_timeout=request_timeout)
        try:
            text = ""
            event_type = ""
            chat_id: int | None = None
            if isinstance(method, SendMessage):
                text = method.text
                event_type = "send_message"
                chat_id = int(method.chat_id) if str(method.chat_id).lstrip("-").isdigit() else None
            elif isinstance(method, SendPhoto):
                text = method.caption or "[photo]"
                event_type = "send_photo"
                chat_id = int(method.chat_id) if str(method.chat_id).lstrip("-").isdigit() else None
            elif isinstance(method, SendDocument):
                text = method.caption or "[document]"
                event_type = "send_document"
                chat_id = int(method.chat_id) if str(method.chat_id).lstrip("-").isdigit() else None

            if event_type:
                await log_activity(
                    direction="outgoing",
                    event_type=event_type,
                    telegram_id=chat_id,
                    text=text,
                )
        except Exception as exc:
            logger.debug(f"Outgoing activity log skipped: {exc}")
        return result
