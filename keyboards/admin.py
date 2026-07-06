"""
keyboards/admin.py — Inline Keyboard‌های پنل ادمین (بازطراحی‌شده)

چیدمان گروه‌بندی‌شده:
  ردیف ۱: پلن‌ها | اینباندها
  ردیف ۲: تخفیف | کاربران
  ردیف ۳: آمار سریع | آمار پیشرفته
  ردیف ۴: سرور | لاگ Xray
  ردیف ۵: ریستارت | تیکت‌ها
  ردیف ۶: بک‌آپ | بنر
  ردیف ۷: پیام دسته‌جمعی (عرض کامل)
  ردیف ۸: بازگشت (عرض کامل)
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import DiscountCode, Plan

# تیتر جداکننده — دکمه غیرفعال (callback=noop)
_SEP = "noop"


def _sep(label: str, builder: InlineKeyboardBuilder) -> None:
    """یک ردیف تیتر بدون عملکرد (جداکننده بصری)."""
    builder.button(text=label, callback_data=_SEP)
    builder.adjust(1)   # تنظیم می‌شود توسط adjust کلی


def get_admin_main_keyboard() -> InlineKeyboardMarkup:
    from aiogram.types import InlineKeyboardButton
    b = InlineKeyboardBuilder()

    # ── گروه ۱: مدیریت محتوا ─────────────────────────
    b.button(text="📋  پلن‌ها",           callback_data="adm_plans")
    b.button(text="🔌  اینباند تست",     callback_data="adm_inbounds")
    b.button(text="🏷  تخفیف‌ها",    callback_data="adm_discounts")
    b.button(text="👥  کاربران",      callback_data="adm_users")

    # ── گروه ۲: آمار ─────────────────────────────────
    b.button(text="📊  آمار",         callback_data="adm_stats")
    b.button(text="📈  آمار کامل",    callback_data="adm_stats_advanced")

    # ── گروه ۳: سرور ─────────────────────────────────
    b.button(text="🖥  وضعیت سرور",  callback_data="admin_server_status")
    b.button(text="📜  لاگ Xray",    callback_data="admin_xray_logs")
    b.button(text="🔄  ریستارت",     callback_data="admin_restart_xray")
    b.button(text="🎫  تیکت‌ها",     callback_data="admin_tickets")

    # ── گروه ۴: ابزار ────────────────────────────────
    b.button(text="💾  بک‌آپ",           callback_data="adm_backup")
    b.button(text="🖼  بنر ربات",        callback_data="adm_banner")
    b.button(text="🎉  بنر خوش‌آمد",   callback_data="adm_welcome_banner")
    b.button(text="📢  کانال اجباری",   callback_data="adm_join_channel")
    b.button(text="💳  تنظیم کارت",     callback_data="adm_card_settings")
    b.button(text="💰  روش‌های پرداخت", callback_data="adm_payment_methods")

    # ── گروه ۵: مدیریت مالی ──────────────────────────
    b.button(text="💱  نرخ تومان/دلار",    callback_data="adm_card_rate")
    b.button(text="🧾  مدیریت تراکنش‌ها",  callback_data="adm_pending_payments")
    b.button(text="➕  ایجاد اشتراک دستی", callback_data="adm_manual_sub")

    # ── گروه ۶: امنیت ────────────────────────────────
    b.button(text="🔐  تنظیمات امنیتی", callback_data="adm_security")
    b.button(text="🎁  اشتراک تست",     callback_data="adm_test_sub_settings")

    # ── تمام‌عرض ─────────────────────────────────────
    b.button(text="📢  پیام دسته‌جمعی به همه کاربران", callback_data="adm_broadcast")
    b.button(text="🏠  بازگشت به منوی اصلی",           callback_data="back_main")
    b.button(text="👨‍💻  github.com/sariyan-0",            url="https://github.com/sariyan-0")

    b.adjust(2, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 1, 1, 1, 1)
    return b.as_markup()


def get_plans_manage_keyboard(plans: list) -> InlineKeyboardMarkup:
    """لیست پلن‌ها با وضعیت — حجمی و نامحدود جداگانه."""
    b = InlineKeyboardBuilder()

    limited   = [p for p in plans if p.traffic_gb > 0]
    unlimited = [p for p in plans if p.traffic_gb == 0]

    def _row(p) -> str:
        st = "✅" if p.is_active else "⛔"
        pr = f"{p.price_usdt:.0f}" if p.price_usdt == int(p.price_usdt) else f"{p.price_usdt}"
        toman = getattr(p, "price_toman", 0) or 0
        if p.traffic_gb:
            vol = f"{p.traffic_gb}G"
        else:
            vol = f"∞×{p.limit_ip}" if p.limit_ip else "∞"
        return f"{st}  {p.name}  ·  {vol}  ·  {pr}$" + (f"  ·  {toman:,}ت" if toman else "")

    if limited:
        b.button(text="── پلن‌های حجمی ──", callback_data=_SEP)
        for p in limited:
            b.button(text=_row(p), callback_data=f"adm_plan_view:{p.id}")
    if unlimited:
        b.button(text="── پلن‌های نامحدود ──", callback_data=_SEP)
        for p in unlimited:
            b.button(text=_row(p), callback_data=f"adm_plan_view:{p.id}")

    b.button(text="➕  افزودن پلن جدید", callback_data="adm_plan_add")
    b.button(text="🔙  بازگشت",          callback_data="adm_back")
    b.adjust(1)
    return b.as_markup()


def get_plan_edit_keyboard(plan_id: int) -> InlineKeyboardMarkup:
    """کیبورد ویرایش پلن — گروه‌بندی‌شده."""
    b = InlineKeyboardBuilder()
    # ── ویرایش فیلدها (جدید: adm_qedit) ──────────
    b.button(text="✏️ نام",         callback_data=f"adm_qedit:name:{plan_id}")
    b.button(text="💲 قیمت",        callback_data=f"adm_qedit:price:{plan_id}")
    b.button(text="💱 تومان",       callback_data=f"adm_qedit:toman:{plan_id}")
    b.button(text="📦 حجم",         callback_data=f"adm_qedit:traffic:{plan_id}")
    b.button(text="⏱ مدت",         callback_data=f"adm_qedit:days:{plan_id}")
    b.button(text="👤 دستگاه",      callback_data=f"adm_qedit:ip:{plan_id}")
    b.button(text="🔌 اینباندها",   callback_data=f"adm_plan_inbounds:{plan_id}")
    # ── عملیات ─────────────────────────────────────
    b.button(text="🔁 فعال/غیرفعال",      callback_data=f"adm_plan_toggle:{plan_id}")
    b.button(text="📋 کپی این پلن",       callback_data=f"adm_plan_copy:{plan_id}")
    b.button(text="🗑  حذف پلن",          callback_data=f"adm_plan_del:{plan_id}")
    b.button(text="🔙 بازگشت به پلن‌ها",  callback_data="adm_plans")
    # چیدمان: ۳ | ۳ | ۲ | ۱ | ۱
    b.adjust(3, 3, 2, 1, 1)
    return b.as_markup()


def get_plan_quick_actions_keyboard(plan_id: int) -> InlineKeyboardMarkup:
    """دکمه‌های سریع بعد از ویرایش موفق."""
    b = InlineKeyboardBuilder()
    b.button(text="✏️ ویرایش مجدد",   callback_data=f"adm_plan_view:{plan_id}")
    b.button(text="📋 لیست پلن‌ها",   callback_data="adm_plans")
    b.adjust(2)
    return b.as_markup()


def get_discounts_keyboard(codes: list) -> InlineKeyboardMarkup:
    """لیست کدها — کلیک → جزئیات (نه حذف مستقیم)."""
    b = InlineKeyboardBuilder()
    for dc in codes:
        st   = "✅" if dc.is_active else "⛔"
        uses = f"{dc.used_count}/{dc.max_uses}" if dc.max_uses else f"{dc.used_count}/∞"
        b.button(
            text=f"{st}  {dc.code}  ·  {dc.percent}٪  ·  {uses}",
            callback_data=f"adm_disc_view:{dc.id}",
        )
    b.button(text="➕ کد تخفیف جدید", callback_data="adm_disc_add")
    b.button(text="🔙 بازگشت",         callback_data="adm_back")
    b.adjust(1)
    return b.as_markup()


def get_discount_detail_keyboard(dc_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """جزئیات یک کد — تغییر وضعیت + حذف با تأیید."""
    b = InlineKeyboardBuilder()
    toggle_text = "⛔ غیرفعال کردن" if is_active else "✅ فعال کردن"
    b.button(text=toggle_text,          callback_data=f"adm_disc_toggle:{dc_id}")
    b.button(text="🗑 حذف کد",          callback_data=f"adm_disc_del_confirm:{dc_id}")
    b.button(text="🔙 بازگشت به کدها",  callback_data="adm_discounts")
    b.adjust(2, 1)
    return b.as_markup()


def get_discount_delete_confirm_keyboard(dc_id: int) -> InlineKeyboardMarkup:
    """تأیید نهایی حذف."""
    b = InlineKeyboardBuilder()
    b.button(text="✅ بله، حذف کن",     callback_data=f"adm_disc_del:{dc_id}")
    b.button(text="❌ انصراف",           callback_data=f"adm_disc_view:{dc_id}")
    b.adjust(2)
    return b.as_markup()


def get_inbounds_keyboard(
    inbounds: list,
    enabled_ids: list | None = None,
) -> InlineKeyboardMarkup:
    """
    keyboard اینباندها با نشان‌دهی وضعیت فعال برای ساخت کانفیگ.

    ✅ = فعال در پنل + انتخاب‌شده برای ساخت کانفیگ
    🟢 = فعال در پنل ولی انتخاب نشده
    ❌ = غیرفعال در پنل
    """
    builder = InlineKeyboardBuilder()
    enabled_ids = enabled_ids or []

    for ib in inbounds:
        if not ib.enable:
            icon = "❌"
        elif ib.id in enabled_ids:
            icon = "✅"
        else:
            icon = "🟢"
        builder.button(
            text=f"{icon} [{ib.id}] {ib.remark} ({ib.protocol.upper()}:{ib.port})",
            callback_data=f"adm_inbound_toggle:{ib.id}",
        )

    builder.button(text="ℹ️ راهنما", callback_data="adm_inbound_help")
    builder.button(text="🔙 بازگشت", callback_data="adm_back")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_users_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 جستجو با آیدی تلگرام", callback_data="adm_user_search")
    builder.button(text="🔙 بازگشت", callback_data="adm_back")
    builder.adjust(1)
    return builder.as_markup()


def get_backup_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    # ── دریافت بک‌آپ ──────────────────────────────────
    b.button(text="🗄 بک‌آپ دیتابیس ربات",      callback_data="adm_backup_bot")
    b.button(text="🖥 بک‌آپ پنل 3X-UI",         callback_data="adm_backup_panel")
    b.button(text="📦 هر دو با هم",             callback_data="adm_backup_both")
    # ── رستور ─────────────────────────────────────────
    b.button(text="♻️ بازگردانی دیتابیس ربات",  callback_data="adm_restore")
    # ── ریست ──────────────────────────────────────────
    b.button(text="🗑 ریست کامل دیتابیس",       callback_data="adm_db_reset_step1")
    b.button(text="🔙 بازگشت",                  callback_data="adm_back")
    b.adjust(1)
    return b.as_markup()


def get_db_reset_confirm1_keyboard() -> InlineKeyboardMarkup:
    """مرحله اول تأیید ریست — هشدار اولیه."""
    b = InlineKeyboardBuilder()
    b.button(text="⚠️ بله، ادامه بده",  callback_data="adm_db_reset_step2")
    b.button(text="❌ لغو",              callback_data="adm_backup")
    b.adjust(2)
    return b.as_markup()


def get_db_reset_confirm2_keyboard() -> InlineKeyboardMarkup:
    """مرحله دوم تأیید ریست — تأیید نهایی غیرقابل بازگشت."""
    b = InlineKeyboardBuilder()
    b.button(text="🗑 بله، دیتابیس را ریست کن",  callback_data="adm_db_reset_confirm")
    b.button(text="❌ لغو — نگه دار",             callback_data="adm_backup")
    b.adjust(1)
    return b.as_markup()


def get_user_detail_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📦 اشتراک‌ها",         callback_data=f"adm_user_subs:{telegram_id}")
    b.button(text="❌ لغو ادمین",         callback_data=f"adm_user_ban:{telegram_id}")
    b.button(text="🔙 بازگشت",           callback_data="adm_users")
    b.adjust(2, 1)
    return b.as_markup()


def get_user_subs_keyboard(telegram_id: int, subs: list) -> InlineKeyboardMarkup:
    """لیست اشتراک‌های یک کاربر — هر اشتراک یک دکمه برای مدیریت."""
    b = InlineKeyboardBuilder()
    for s in subs:
        status_icon = {"active": "✅", "expired": "⏰", "depleted": "📭"}.get(s.status, "⚪")
        limit = f"{s.traffic_limit_gb}G" if s.traffic_limit_gb else "∞"
        b.button(
            text=f"{status_icon} {s.email}  [{limit}]",
            callback_data=f"adm_sub_view:{s.id}",
        )
    b.button(text="🔙 بازگشت به کاربر", callback_data=f"adm_user_info:{telegram_id}")
    b.adjust(1)
    return b.as_markup()


def get_sub_manage_keyboard(sub_id: int, telegram_id: int) -> InlineKeyboardMarkup:
    """کیبورد مدیریت یک اشتراک — تمدید، حجم، ویرایش ایمیل، حذف."""
    b = InlineKeyboardBuilder()
    # ── ویرایش ─────────────────────────────────────────────
    b.button(text="📅 تمدید (روز+)",     callback_data=f"adm_sub_edit:days:{sub_id}")
    b.button(text="📦 تغییر حجم (GB)",   callback_data=f"adm_sub_edit:traffic:{sub_id}")
    b.button(text="✏️ تغییر ایمیل",      callback_data=f"adm_sub_edit:email:{sub_id}")
    b.button(text="🔄 ریست ترافیک",      callback_data=f"adm_sub_reset:{sub_id}")
    # ── وضعیت ──────────────────────────────────────────────
    b.button(text="⏸ غیرفعال/فعال",     callback_data=f"adm_sub_toggle:{sub_id}")
    b.button(text="🗑 حذف اشتراک",       callback_data=f"adm_sub_del_confirm:{sub_id}")
    # ── ناوبری ─────────────────────────────────────────────
    b.button(text="🔙 بازگشت",          callback_data=f"adm_user_subs:{telegram_id}")
    b.adjust(2, 2, 1, 1, 1)
    return b.as_markup()


def get_sub_del_confirm_keyboard(sub_id: int, telegram_id: int) -> InlineKeyboardMarkup:
    """تأیید حذف اشتراک."""
    b = InlineKeyboardBuilder()
    b.button(text="✅ بله، حذف کن",     callback_data=f"adm_sub_del:{sub_id}:{telegram_id}")
    b.button(text="❌ انصراف",           callback_data=f"adm_sub_view:{sub_id}")
    b.adjust(2)
    return b.as_markup()


def get_transactions_keyboard(has_sub_id: bool = False, sub_id: int = 0,
                               tg_id: int = 0) -> InlineKeyboardMarkup:
    """کیبورد صفحه جزئیات یک تراکنش."""
    b = InlineKeyboardBuilder()
    if has_sub_id and sub_id:
        b.button(text="📦 مدیریت اشتراک", callback_data=f"adm_sub_view:{sub_id}")
    b.button(text="👤 پروفایل کاربر",    callback_data=f"adm_user_info:{tg_id}" if tg_id else "noop")
    b.button(text="🔙 بازگشت",           callback_data="adm_pending_payments")
    b.adjust(1)
    return b.as_markup()


def get_payments_filter_keyboard() -> InlineKeyboardMarkup:
    """کیبورد فیلتر لیست تراکنش‌ها."""
    b = InlineKeyboardBuilder()
    b.button(text="⏳ در صف (کارت)",    callback_data="adm_tx_filter:pending_card")
    b.button(text="⏳ در صف (کریپتو)", callback_data="adm_tx_filter:pending_crypto")
    b.button(text="✅ موفق (کارت)",     callback_data="adm_tx_filter:confirmed_card")
    b.button(text="✅ موفق (کریپتو)",  callback_data="adm_tx_filter:confirmed_crypto")
    b.button(text="❌ ناموفق (کارت)",   callback_data="adm_tx_filter:failed_card")
    b.button(text="❌ ناموفق (کریپتو)",callback_data="adm_tx_filter:failed_crypto")
    b.button(text="🔍 جستجو order_id",  callback_data="adm_tx_search")
    b.button(text="🔙 بازگشت",          callback_data="adm_back")
    b.adjust(2, 2, 2, 1, 1)
    return b.as_markup()


def get_payment_methods_keyboard(
    crypto_on: bool,
    card_on: bool,
    crypto_invoice: bool = False,
    crypto_gateway: str = "nowpayments",   # "nowpayments" | "maxelpay"
) -> InlineKeyboardMarkup:
    """وضعیت روش‌های پرداخت با دکمه toggle + انتخاب درگاه کریپتو."""
    b = InlineKeyboardBuilder()
    crypto_icon  = "✅" if crypto_on else "⛔"
    card_icon    = "✅" if card_on   else "⛔"
    invoice_icon = "🌐" if crypto_invoice else "🪙"

    # ── کریپتو on/off ──────────────────────────────────────────────────
    b.button(
        text=f"{crypto_icon}  کریپتو — {'فعال' if crypto_on else 'غیرفعال'}",
        callback_data="adm_pm_toggle:crypto",
    )

    if crypto_on:
        # ── انتخاب درگاه کریپتو ────────────────────────────────────────
        gw_label = "MaxelPay 💜" if crypto_gateway == "maxelpay" else "NOWPayments 🔵"
        b.button(
            text=f"🏦  درگاه: {gw_label}",
            callback_data="adm_pm_toggle:crypto_gateway",
        )
        # حالت Invoice فقط برای NOWPayments
        if crypto_gateway == "nowpayments":
            b.button(
                text=f"{invoice_icon}  حالت: {'صفحه انتخاب ارز 🌐' if crypto_invoice else 'پرداخت مستقیم 🪙'}",
                callback_data="adm_pm_toggle:crypto_invoice",
            )

    # ── کارت به کارت ───────────────────────────────────────────────────
    b.button(
        text=f"{card_icon}  کارت به کارت — {'فعال' if card_on else 'غیرفعال'}",
        callback_data="adm_pm_toggle:card",
    )
    b.button(text="🔙 بازگشت", callback_data="adm_back")
    b.adjust(1)
    return b.as_markup()


def get_card_settings_keyboard() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✏️ تغییر شماره کارت", callback_data="adm_card_edit")
    b.button(text="🔙 بازگشت",           callback_data="adm_back")
    b.adjust(1)
    return b.as_markup()


def get_security_keyboard(current_command: str = "admin_secret") -> InlineKeyboardMarkup:
    """کیبورد تنظیمات امنیتی."""
    b = InlineKeyboardBuilder()
    b.button(
        text=f"🔑 تغییر دستور ورود (فعلی: /{current_command})",
        callback_data="adm_sec_change_cmd",
    )
    b.button(text="🔙 بازگشت", callback_data="adm_back")
    b.adjust(1)
    return b.as_markup()


def get_pending_payment_keyboard(order_id: str) -> InlineKeyboardMarkup:
    """دکمه‌های تأیید/رد تراکنش کارت‌به‌کارت در صف."""
    b = InlineKeyboardBuilder()
    b.button(text="✅ تأیید و فعال‌سازی", callback_data=f"card_approve:{order_id}")
    b.button(text="❌ رد پرداخت",          callback_data=f"card_reject:{order_id}")
    b.button(text="🔙 بازگشت به صف",       callback_data="adm_pending_payments")
    b.adjust(2, 1)
    return b.as_markup()


def get_crypto_pending_keyboard(order_id: str, tg_id: int = 0) -> InlineKeyboardMarkup:
    """کیبورد تراکنش کریپتوی در صف — بدون تأیید/رد (کریپتو خودکار است).
    
    دکمه «منقضی» برای تراکنش‌هایی که زمانشان گذشته ولی status هنوز waiting است.
    """
    b = InlineKeyboardBuilder()
    b.button(text="⏰ علامت‌گذاری منقضی",  callback_data=f"crypto_expire:{order_id}")
    if tg_id:
        b.button(text="👤 پروفایل کاربر", callback_data=f"adm_user_info:{tg_id}")
    b.button(text="🔙 بازگشت",             callback_data="adm_pending_payments")
    b.adjust(1)
    return b.as_markup()


def get_manual_sub_plans_keyboard(plans: list) -> InlineKeyboardMarkup:
    """لیست پلن‌ها + پلن دلخواه برای ایجاد اشتراک دستی."""
    b = InlineKeyboardBuilder()
    for plan in plans:
        b.button(
            text=f"{'♾' if plan.traffic_gb == 0 else f'{plan.traffic_gb}G'} {plan.name}",
            callback_data=f"adm_msub_plan:{plan.id}",
        )
    b.button(text="⚙️ پلن دلخواه", callback_data="adm_msub_custom")
    b.button(text="🔙 بازگشت",     callback_data="adm_back")
    b.adjust(1)
    return b.as_markup()


def get_manual_sub_inbounds_keyboard(inbounds: list, selected: list[int]) -> InlineKeyboardMarkup:
    """انتخاب اینباند برای پلن دلخواه — چند انتخابی."""
    b = InlineKeyboardBuilder()
    for inb in inbounds:
        checked = "✅" if inb["id"] in selected else "⬜"
        b.button(
            text=f"{checked} {inb.get('remark', inb['id'])}",
            callback_data=f"adm_msub_inb:{inb['id']}",
        )
    b.button(text="◀️ ادامه", callback_data="adm_msub_inb_done")
    b.button(text="🔙 لغو",   callback_data="adm_msub_cancel")
    b.adjust(1)
    return b.as_markup()


def get_plan_inbounds_keyboard(
    plan_id: int,
    inbounds: list,
    plan_inbound_ids: list[int],
) -> InlineKeyboardMarkup:
    """
    keyboard انتخاب اینباندهای اختصاصی برای یک پلن.

    ✅ = انتخاب‌شده برای این پلن
    🟢 = فعال در پنل ولی انتخاب نشده
    ❌ = غیرفعال در پنل
    """
    b = InlineKeyboardBuilder()
    for ib in inbounds:
        if not ib.enable:
            icon = "❌"
        elif ib.id in plan_inbound_ids:
            icon = "✅"
        else:
            icon = "🟢"
        b.button(
            text=f"{icon} [{ib.id}] {ib.remark} ({ib.protocol.upper()}:{ib.port})",
            callback_data=f"adm_plan_inb_toggle:{plan_id}:{ib.id}",
        )
    b.button(text="🔙 بازگشت به ویرایش پلن", callback_data=f"adm_plan_view:{plan_id}")
    b.adjust(1)
    return b.as_markup()


def get_banner_keyboard(has_banner: bool = False) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📤 آپلود عکس جدید", callback_data="adm_banner_set")
    if has_banner:
        b.button(text="🗑 حذف بنر فعلی", callback_data="adm_banner_clear")
    b.button(text="🔙 بازگشت",          callback_data="adm_back")
    b.adjust(1)
    return b.as_markup()


def get_welcome_banner_keyboard(has_banner: bool = False) -> InlineKeyboardMarkup:
    """keyboard مدیریت بنر خوش‌آمدگویی."""
    b = InlineKeyboardBuilder()
    b.button(text="📤 آپلود عکس بنر",      callback_data="adm_welcome_set_photo")
    b.button(text="✏️ ویرایش کپشن",        callback_data="adm_welcome_set_caption")
    if has_banner:
        b.button(text="🗑 حذف بنر",         callback_data="adm_welcome_clear")
    b.button(text="🔙 بازگشت",              callback_data="adm_back")
    b.adjust(1)
    return b.as_markup()


def get_join_channel_keyboard(has_channel: bool = False) -> InlineKeyboardMarkup:
    """keyboard مدیریت کانال اجباری."""
    b = InlineKeyboardBuilder()
    b.button(text="➕ تنظیم کانال جدید",    callback_data="adm_channel_set")
    if has_channel:
        b.button(text="🗑 حذف کانال",       callback_data="adm_channel_clear")
    b.button(text="🔙 بازگشت",              callback_data="adm_back")
    b.adjust(1)
    return b.as_markup()
