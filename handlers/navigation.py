"""
handlers/navigation.py — global main-menu navigation guards

These handlers run early so main menu buttons still work even if the user is
stuck inside another FSM state from an unfinished flow.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

router = Router(name="navigation")


_MAIN_MENU_TEXTS = {
    "🛒 خرید کانفیگ",
    "🎁 اشتراک تست",
    "📊 اشتراک‌های من",
    "📥 افزودن اشتراک قدیمی",
    "👤 پروفایل",
    "👥 دعوت دوستان",
    "❓ پشتیبانی",
    "⚙️ پنل مدیریت",
}


@router.message(F.text.in_(_MAIN_MENU_TEXTS))
async def msg_main_menu_navigation(message: Message, state: FSMContext) -> None:
    """Force-clear unfinished flows and route main-menu buttons consistently."""
    if not message.from_user or not message.text:
        return

    await state.clear()

    try:
        from handlers.tickets import _set_active_ticket_id, _set_ticket_session_state

        await _set_ticket_session_state(message.from_user.id, False)
        await _set_active_ticket_id(message.from_user.id, None)
    except Exception:
        pass

    text = message.text
    if text == "🛒 خرید کانفیگ":
        from handlers.shop import msg_buy

        await msg_buy(message)
        return
    if text == "🎁 اشتراک تست":
        from handlers.shop import msg_test_sub

        await msg_test_sub(message)
        return
    if text == "📊 اشتراک‌های من":
        from handlers.user import menu_my_subscriptions

        await menu_my_subscriptions(message)
        return
    if text == "📥 افزودن اشتراک قدیمی":
        from handlers.uuid_import import msg_uuid_entry

        await msg_uuid_entry(message, state)
        return
    if text == "👤 پروفایل":
        from handlers.user import menu_profile

        await menu_profile(message)
        return
    if text == "👥 دعوت دوستان":
        from handlers.referral import menu_referral

        await menu_referral(message)
        return
    if text == "❓ پشتیبانی":
        from handlers.tickets import menu_support

        await menu_support(message)
        return
    if text == "⚙️ پنل مدیریت":
        from handlers.admin import msg_admin_panel

        await msg_admin_panel(message)
