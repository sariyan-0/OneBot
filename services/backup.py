"""
services/backup.py — پشتیبان‌گیری از دیتابیس ربات و پنل 3X-UI

قابلیت‌ها:
  • backup_bot_db  — صادر کردن فایل SQLite ربات و ارسال به ادمین
  • backup_panel_db — دریافت فایل DB پنل (GET /panel/api/server/getDb) و ارسال
  • send_daily_backups — job روزانه که هر دو را ارسال می‌کند
"""

from __future__ import annotations

import io
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, List

from loguru import logger

from config import settings
from services.xui_api import XUIClient, XUIError

if TYPE_CHECKING:
    from aiogram import Bot


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────

def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")


def _admin_ids() -> List[int]:
    return list(settings.admin_ids)


async def _send_document_to_admins(
    bot: "Bot",
    file_bytes: bytes,
    filename: str,
    caption: str,
) -> int:
    """ارسال فایل به همه ادمین‌ها (settings + DB)."""
    from aiogram.types import BufferedInputFile

    # ادمین‌ها را از هر دو منبع جمع کن
    admin_list = list(_admin_ids())
    try:
        from database import AsyncSessionLocal as _Session
        from database.models import User
        from sqlalchemy import select
        async with _Session() as _s:
            res = await _s.execute(select(User).where(User.is_admin == True))  # noqa: E712
            for u in res.scalars().all():
                if u.telegram_id not in admin_list:
                    admin_list.append(u.telegram_id)
    except Exception:
        pass

    if not admin_list:
        logger.warning("backup: هیچ ادمینی تعریف نشده — فایل ارسال نشد")
        return 0

    sent = 0
    for admin_id in admin_list:
        try:
            await bot.send_document(
                chat_id=admin_id,
                document=BufferedInputFile(file_bytes, filename=filename),
                caption=caption,
                parse_mode="HTML",
            )
            sent += 1
        except Exception as e:
            logger.warning(f"ارسال backup به ادمین {admin_id} ناموفق: {e}")
    return sent


# ──────────────────────────────────────────────
# backup دیتابیس ربات
# ──────────────────────────────────────────────

async def backup_bot_db(bot: "Bot") -> bool:
    """
    پشتیبان‌گیری از دیتابیس ربات.
    - SQLite: فایل .db به صورت zip فشرده و ارسال می‌شود.
    - PostgreSQL: تمام جداول مهم با asyncpg به صورت JSON/CSV ارسال می‌شود.
    """
    db_url = settings.db_url
    ts = _timestamp()

    # ── PostgreSQL: export جداول کلیدی ──────────────────────────
    if "postgresql" in db_url or "postgres" in db_url:
        return await _backup_postgres(bot, ts)

    # ── SQLite: backup ایمن با sqlite3 API ───────────────────────
    db_path_str = db_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
    db_path = Path(db_path_str)

    # اگر مسیر نسبی بود، نسبت به پوشه bot/ حل کن
    if not db_path.is_absolute():
        base_dir = Path(__file__).parent.parent  # bot/
        db_path = (base_dir / db_path).resolve()

    if not db_path.exists():
        logger.warning(f"فایل دیتابیس پیدا نشد: {db_path}")
        return False

    try:
        import sqlite3
        tmp_path = db_path.parent / f"_backup_tmp_{ts}.db"
        # backup ایمن — هر دو connection صریحاً بسته می‌شوند
        src_conn = sqlite3.connect(str(db_path))
        dst_conn = sqlite3.connect(str(tmp_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()

        db_bytes = tmp_path.read_bytes()
        tmp_path.unlink(missing_ok=True)

        # فشرده‌سازی با zip
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"bot_data_{ts}.db", db_bytes)
        zip_bytes = zip_buffer.getvalue()

        # شمارش ردیف‌های جداول + خواندن تنظیمات کلیدی
        row_summary = ""
        settings_summary = ""
        try:
            import sqlite3 as _sq3
            _conn = _sq3.connect(str(db_path))
            _cur  = _conn.cursor()

            # شمارش جداول
            counts: dict = {}
            for tbl in ("users", "subscriptions", "payments", "plans",
                        "tickets", "discount_codes", "admin_settings"):
                try:
                    _cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                    counts[tbl] = _cur.fetchone()[0]
                except Exception:
                    pass

            # خواندن تنظیمات کلیدی از admin_settings
            _cur.execute("SELECT key, value FROM admin_settings")
            s = dict(_cur.fetchall())
            _conn.close()

            row_summary = (
                f"👥 کاربران: {counts.get('users',0)}  "
                f"📦 اشتراک: {counts.get('subscriptions',0)}  "
                f"💰 پرداخت: {counts.get('payments',0)}\n"
                f"📋 پلن: {counts.get('plans',0)}  "
                f"🎫 تیکت: {counts.get('tickets',0)}  "
                f"🏷 تخفیف: {counts.get('discount_codes',0)}\n"
                f"⚙️ تنظیمات: {counts.get('admin_settings',0)} مورد\n"
            )

            # خلاصه تنظیمات مهم
            gateway   = s.get("crypto_gateway", "nowpayments")
            card_on   = s.get("payment_card_enabled", "0") == "1"
            crypto_on = s.get("payment_crypto_enabled", "1") == "1"
            card_num  = s.get("card_number", "")
            rate      = s.get("usdt_to_toman_rate", "")
            settings_summary = (
                f"💱 درگاه کریپتو: {gateway}\n"
                f"{'✅' if crypto_on else '⛔'} کریپتو  "
                f"{'✅' if card_on else '⛔'} کارت به کارت"
                + (f"\n💳 کارت: {card_num[:4]}****" if card_num else "")
                + (f"\n💹 نرخ: {int(rate):,} تومان" if rate and rate.isdigit() else "")
                + "\n"
            )
        except Exception:
            pass

        size_kb = len(zip_bytes) / 1024
        caption = (
            f"🗄 <b>بک‌آپ دیتابیس ربات</b>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📅 تاریخ: <code>{ts}</code>\n"
            f"📦 حجم: <code>{size_kb:.1f} KB</code>\n\n"
            f"{row_summary}"
            f"\n{settings_summary}"
            f"✅ شامل: کاربران، اشتراک‌ها، پرداخت‌ها، تنظیمات، تیکت‌ها\n\n"
            f"♻️ <b>روش restore:</b>\n"
            f"پنل ادمین ← بک‌آپ ← بازگردانی ← ارسال این فایل"
        )
        sent = await _send_document_to_admins(
            bot, zip_bytes, f"bot_backup_{ts}.zip", caption
        )
        logger.success(f"بک‌آپ ربات ارسال شد به {sent} ادمین")
        return sent > 0

    except Exception as e:
        logger.error(f"خطا در backup دیتابیس ربات: {e}")
        return False


async def _backup_postgres(bot: "Bot", ts: str) -> bool:
    """
    بک‌آپ PostgreSQL — تمام جداول کلیدی را به صورت CSV درون یک zip صادر می‌کند.
    از asyncpg مستقیم (بدون ORM) استفاده می‌شود تا blocking نشود.
    """
    try:
        import asyncpg
        from config import settings as _s

        # اتصال مستقیم با asyncpg
        conn = await asyncpg.connect(_s.db_url.replace("postgresql+asyncpg", "postgresql"))

        tables = ["users", "subscriptions", "payments", "tickets",
                  "ticket_messages", "plans", "discount_codes",
                  "admin_settings", "referrals"]

        zip_buffer = io.BytesIO()
        exported = []
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for table in tables:
                try:
                    rows = await conn.fetch(f"SELECT * FROM {table}")
                    if not rows:
                        zf.writestr(f"{table}.csv", "empty\n")
                        continue
                    # تبدیل به CSV
                    cols = list(rows[0].keys())
                    lines = [",".join(str(c) for c in cols)]
                    for row in rows:
                        lines.append(",".join(
                            '"' + str(v).replace('"', '""') + '"' if v is not None else ""
                            for v in row.values()
                        ))
                    zf.writestr(f"{table}.csv", "\n".join(lines))
                    exported.append(f"{table}({len(rows)})")
                except Exception as e:
                    logger.warning(f"backup جدول {table} ناموفق: {e}")

        await conn.close()
        zip_bytes = zip_buffer.getvalue()
        size_kb = len(zip_bytes) / 1024

        caption = (
            f"🗄 <b>بک‌آپ PostgreSQL ربات</b>\n\n"
            f"📅 تاریخ: <code>{ts}</code>\n"
            f"📦 حجم: <code>{size_kb:.1f} KB</code>\n"
            f"📋 جداول: {', '.join(exported)}\n\n"
            "⚠️ این فایل CSV است — برای restore از ابزار psql استفاده کنید."
        )
        sent = await _send_document_to_admins(
            bot, zip_bytes, f"bot_pg_backup_{ts}.zip", caption
        )
        logger.success(f"بک‌آپ PostgreSQL ارسال شد به {sent} ادمین")
        return sent > 0

    except ImportError:
        # asyncpg نصب نیست — پیام اطلاع‌رسانی
        msg = (
            "ℹ️ <b>بک‌آپ PostgreSQL</b>\n\n"
            "برای بک‌آپ خودکار PostgreSQL باید <code>asyncpg</code> نصب باشد.\n"
            "در محیط Docker این بسته موجود است.\n\n"
            f"🕐 {ts}"
        )
        for admin_id in _admin_ids():
            try:
                await bot.send_message(admin_id, msg, parse_mode="HTML")
            except Exception:
                pass
        return True
    except Exception as e:
        logger.error(f"خطا در backup PostgreSQL: {e}")
        return False


# ──────────────────────────────────────────────
# backup پنل 3X-UI
# ──────────────────────────────────────────────

async def backup_panel_db(bot: "Bot") -> bool:
    """
    دریافت فایل دیتابیس پنل 3X-UI از طریق API
    endpoint: GET /panel/api/server/getDb
    و ارسال به همه ادمین‌ها.
    """
    ts = _timestamp()
    try:
        async with XUIClient(
            panel_url=settings.panel_url,
            username=settings.panel_username,
            password=settings.panel_password,
            api_path=settings.panel_api_path,
            sub_port=settings.sub_port,
        ) as xui:
            db_bytes = await xui.download_panel_db()

        if not db_bytes:
            logger.warning("backup پنل: فایل دریافت‌شده خالی است")
            return False

        # فشرده‌سازی
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"x-ui_{ts}.db", db_bytes)
        zip_bytes = zip_buffer.getvalue()

        size_kb = len(zip_bytes) / 1024
        caption = (
            f"🖥 <b>بک‌آپ پنل 3X-UI</b>\n\n"
            f"📅 تاریخ: <code>{ts}</code>\n"
            f"📦 حجم: <code>{size_kb:.1f} KB</code>\n\n"
            f"⚠️ این فایل مربوط به <b>پنل 3X-UI</b> است، نه ربات.\n"
            f"برای بازیابی، این فایل را از داخل رابط وب پنل 3X-UI وارد کنید.\n"
            f"(Settings → Import DB)"
        )
        sent = await _send_document_to_admins(
            bot, zip_bytes, f"xui_backup_{ts}.zip", caption
        )
        logger.success(f"بک‌آپ پنل ارسال شد به {sent} ادمین")
        return sent > 0

    except XUIError as e:
        logger.error(f"خطا در دریافت backup پنل: {e}")
        return False
    except Exception as e:
        logger.error(f"خطای ناشناخته در backup پنل: {e}")
        return False


# ──────────────────────────────────────────────
# job روزانه — هر دو backup
# ──────────────────────────────────────────────

async def send_daily_backups(bot: "Bot") -> None:
    """Job روزانه — بک‌آپ هر دو دیتابیس و ارسال به ادمین‌ها."""
    logger.info("📦 شروع backup روزانه...")
    bot_ok = await backup_bot_db(bot)
    panel_ok = await backup_panel_db(bot)
    logger.success(
        f"backup روزانه تمام شد — ربات: {'✅' if bot_ok else '❌'} | "
        f"پنل: {'✅' if panel_ok else '❌'}"
    )
