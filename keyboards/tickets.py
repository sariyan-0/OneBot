"""
keyboards/tickets.py — کیبوردهای سیستم تیکت پشتیبانی
"""

from __future__ import annotations

from typing import List

from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from database.models import Ticket


def get_ticket_list_keyboard(tickets: List[Ticket]) -> InlineKeyboardMarkup:
    """لیست تیکت‌های کاربر."""
    builder = InlineKeyboardBuilder()
    for t in tickets:
        status_icon = {"open": "🔴", "in_progress": "🟡", "closed": "✅"}.get(t.status, "⚪")
        label = f"{status_icon} #{t.id} — {t.subject[:30]}"
        builder.button(text=label, callback_data=f"ticket_view:{t.id}")
    builder.button(text="✏️ تیکت جدید", callback_data="ticket_new")
    builder.button(text="🔙 بازگشت", callback_data="back_main")
    builder.adjust(1)
    return builder.as_markup()


def get_ticket_detail_keyboard(ticket_id: int, is_closed: bool = False) -> InlineKeyboardMarkup:
    """کیبورد جزئیات تیکت برای کاربر."""
    builder = InlineKeyboardBuilder()
    if not is_closed:
        builder.button(text="✍️ پاسخ", callback_data=f"ticket_reply:{ticket_id}")
        builder.button(text="🔒 بستن تیکت", callback_data=f"ticket_close:{ticket_id}")
    else:
        # تیکت بسته — کاربر می‌تواند دوباره باز کند
        builder.button(text="🔓 باز کردن مجدد", callback_data=f"ticket_reopen:{ticket_id}")
    builder.button(text="🚪 خروج از گفتگو", callback_data="ticket_exit")
    builder.adjust(2, 1)
    return builder.as_markup()


def get_ticket_mode_keyboard(is_closed: bool = False) -> ReplyKeyboardMarkup:
    """کیبورد بزرگ پایین صفحه برای حالت گفتگوی تیکت."""
    builder = ReplyKeyboardBuilder()
    if not is_closed:
        builder.row(KeyboardButton(text="🔒 بستن تیکت"), KeyboardButton(text="🚪 خروج از گفتگو"))
    else:
        builder.row(KeyboardButton(text="🔓 باز کردن تیکت"))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=False, input_field_placeholder="یک گزینه انتخاب کنید...")


def get_admin_ticket_keyboard(ticket_id: int, is_closed: bool = False) -> InlineKeyboardMarkup:
    """کیبورد مدیریت تیکت برای ادمین."""
    builder = InlineKeyboardBuilder()
    if not is_closed:
        builder.button(text="✍️ پاسخ", callback_data=f"admin_ticket_reply:{ticket_id}")
        builder.button(text="🔒 بستن", callback_data=f"admin_ticket_close:{ticket_id}")
    else:
        builder.button(text="🔓 باز کردن مجدد", callback_data=f"admin_ticket_reopen:{ticket_id}")
    builder.button(text="👁 مشاهده کامل", callback_data=f"admin_ticket_view:{ticket_id}")
    builder.button(text="🔙 لیست تیکت‌ها", callback_data="admin_tickets")
    builder.adjust(2)
    return builder.as_markup()


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """کیبورد لغو عملیات FSM."""
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ انصراف", callback_data="ticket_cancel")
    return builder.as_markup()
