"""
keyboards/main_menu.py — کیبورد اصلی ربات (ReplyKeyboard فارسی)
"""

from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def get_main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """
    منوی اصلی ربات.
    در صورت ادمین بودن، دکمه پنل مدیریت نیز نمایش داده می‌شود.
    """
    rows = [
        [
            KeyboardButton(text="🛒 خرید کانفیگ"),
        ],
        [
            KeyboardButton(text="🎁 اشتراک تست"),
            KeyboardButton(text="📊 اشتراک‌های من"),
        ],
        [
            KeyboardButton(text="📥 افزودن اشتراک قدیمی"),
            KeyboardButton(text="👤 پروفایل"),
        ],
        [
            KeyboardButton(text="👥 دعوت دوستان"),
            KeyboardButton(text="❓ پشتیبانی"),
        ],
    ]

    # دکمه ادمین فقط برای ادمین‌ها
    if is_admin:
        rows.append([KeyboardButton(text="⚙️ پنل مدیریت")])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="یک گزینه انتخاب کنید...",
    )
