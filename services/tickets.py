"""
services/tickets.py — منطق کسب‌وکار سیستم تیکت پشتیبانی
"""

from __future__ import annotations

from typing import List, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import (
    add_ticket_message,
    close_ticket,
    create_ticket,
    get_open_tickets,
    get_ticket,
    get_user_tickets,
)
from database.models import Ticket, TicketMessage


async def open_new_ticket(
    session: AsyncSession,
    user_id: int,
    subject: str,
    message: str,
) -> Ticket:
    """
    ایجاد تیکت جدید.

    Args:
        session: AsyncSession دیتابیس
        user_id: کلید اولیه کاربر
        subject: موضوع تیکت
        message: متن اولین پیام

    Returns:
        تیکت ایجادشده
    """
    ticket = await create_ticket(
        session=session,
        user_id=user_id,
        subject=subject,
        first_message=message,
        sender_id=user_id,
    )
    logger.info(f"تیکت #{ticket.id} برای user_id={user_id} باز شد")
    return ticket


async def reply_to_ticket(
    session: AsyncSession,
    ticket_id: int,
    sender_id: int,
    message: str,
    is_admin: bool = False,
    auto_reopen: bool = True,
) -> TicketMessage:
    """
    ارسال پاسخ به تیکت.
    اگر تیکت بسته باشد و auto_reopen=True، تیکت دوباره باز می‌شود
    (وقتی کاربر پاسخ می‌دهد به تیکت بسته).

    Raises:
        ValueError: اگر تیکت پیدا نشود
    """
    ticket = await get_ticket(session, ticket_id)
    if not ticket:
        raise ValueError(f"تیکت #{ticket_id} پیدا نشد")

    # اگر کاربر پاسخ داد به تیکت بسته → دوباره باز کن
    if ticket.status == "closed":
        if auto_reopen and not is_admin:
            await reopen_ticket(session, ticket_id)
        elif not auto_reopen:
            raise ValueError(f"تیکت #{ticket_id} بسته است.")

    msg = await add_ticket_message(
        session=session,
        ticket_id=ticket_id,
        sender_id=sender_id,
        body=message,
        is_admin_reply=is_admin,
    )
    return msg


async def reopen_ticket(session: AsyncSession, ticket_id: int) -> None:
    """بازگشایی تیکت بسته — وضعیت به 'open' برمی‌گردد."""
    from database.crud import update_subscription_status
    from sqlalchemy import update as sa_update
    from database.models import Ticket as TicketModel
    from datetime import datetime, timezone
    await session.execute(
        sa_update(TicketModel)
        .where(TicketModel.id == ticket_id)
        .values(status="open", closed_at=None,
                updated_at=datetime.now(timezone.utc))
    )
    await session.commit()
    logger.info(f"تیکت #{ticket_id} دوباره باز شد.")


async def fetch_user_tickets(
    session: AsyncSession,
    user_id: int,
) -> List[Ticket]:
    """دریافت لیست تیکت‌های کاربر."""
    return await get_user_tickets(session, user_id)


async def fetch_ticket_detail(
    session: AsyncSession,
    ticket_id: int,
    requesting_user_id: int,
    is_admin: bool = False,
) -> Ticket:
    """
    دریافت جزئیات تیکت با بررسی دسترسی.

    Raises:
        PermissionError: اگر کاربر مالک تیکت نباشد و ادمین نباشد
        ValueError: اگر تیکت پیدا نشود
    """
    ticket = await get_ticket(session, ticket_id)
    if not ticket:
        raise ValueError(f"تیکت #{ticket_id} پیدا نشد")
    if not is_admin and ticket.user_id != requesting_user_id:
        raise PermissionError("دسترسی به این تیکت مجاز نیست")
    return ticket


async def close_user_ticket(
    session: AsyncSession,
    ticket_id: int,
) -> None:
    """بستن تیکت."""
    await close_ticket(session, ticket_id)


async def fetch_open_tickets_for_admin(
    session: AsyncSession,
) -> List[Ticket]:
    """دریافت تیکت‌های باز برای پنل ادمین."""
    return await get_open_tickets(session)
