"""
database/engine.py — ایجاد engine و session factory برای SQLAlchemy Async
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import inspect, text
from loguru import logger

from config import settings
from .models import Base

# ساخت engine غیر‌همزمان
engine = create_async_engine(
    settings.db_url,
    echo=False,          # برای debug: True
    pool_pre_ping=True,  # بررسی connection قبل از استفاده
)

# factory برای ساخت session
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_db() -> None:
    """ایجاد تمام جداول اگر وجود نداشته باشند (برای توسعه)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        def _ensure_subscription_plan_id(sync_conn):
            inspector = inspect(sync_conn)
            columns = {col["name"] for col in inspector.get_columns("subscriptions")}
            if "plan_id" not in columns:
                sync_conn.execute(text("ALTER TABLE subscriptions ADD COLUMN plan_id INTEGER"))
        def _ensure_user_wallet_balance(sync_conn):
            inspector = inspect(sync_conn)
            columns = {col["name"] for col in inspector.get_columns("users")}
            if "wallet_balance_usdt" not in columns:
                sync_conn.execute(text("ALTER TABLE users ADD COLUMN wallet_balance_usdt FLOAT NOT NULL DEFAULT 0"))
            if "wallet_balance_toman" not in columns:
                sync_conn.execute(text("ALTER TABLE users ADD COLUMN wallet_balance_toman INTEGER NOT NULL DEFAULT 0"))
        def _ensure_plan_price_toman(sync_conn):
            inspector = inspect(sync_conn)
            columns = {col["name"] for col in inspector.get_columns("plans")}
            if "price_toman" not in columns:
                sync_conn.execute(text("ALTER TABLE plans ADD COLUMN price_toman INTEGER NOT NULL DEFAULT 0"))
        def _ensure_referral_commissions_table(sync_conn):
            inspector = inspect(sync_conn)
            tables = set(inspector.get_table_names())
            if "referral_commissions" not in tables:
                sync_conn.execute(text("""
                    CREATE TABLE referral_commissions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        referrer_id INTEGER NOT NULL,
                        referred_id INTEGER NOT NULL,
                        payment_id INTEGER NOT NULL UNIQUE,
                        percent FLOAT NOT NULL DEFAULT 0,
                        amount_usdt FLOAT NOT NULL DEFAULT 0,
                        amount_toman INTEGER NOT NULL DEFAULT 0,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(referrer_id) REFERENCES users(id),
                        FOREIGN KEY(referred_id) REFERENCES users(id),
                        FOREIGN KEY(payment_id) REFERENCES payments(id)
                    )
                """))
        await conn.run_sync(_ensure_subscription_plan_id)
        await conn.run_sync(_ensure_user_wallet_balance)
        await conn.run_sync(_ensure_plan_price_toman)
        await conn.run_sync(_ensure_referral_commissions_table)
    await _backfill_wallet_currency_split()


async def _backfill_wallet_currency_split() -> None:
    """یک‌بار، creditهای قدیمی card/toman را از USD به Toman منتقل می‌کند."""
    from sqlalchemy import text as sql_text

    from .crud import get_setting, set_setting

    marker_key = "wallet_currency_split_migrated_v1"
    async with AsyncSessionLocal() as session:
        marker = await get_setting(session, marker_key, "")
        if str(marker).strip() == "1":
            return

        card_wallet_rows = await session.execute(sql_text("""
            SELECT
                user_id,
                COALESCE(SUM(CASE
                    WHEN amount_rial IS NOT NULL AND amount_rial > 0 THEN CAST(amount_rial / 10 AS INTEGER)
                    ELSE 0
                END), 0) AS toman_total,
                COALESCE(SUM(amount_usdt), 0) AS usd_total
            FROM payments
            WHERE payment_method = 'card'
              AND order_id LIKE 'wallet_card_%'
              AND status IN ('confirmed', 'finished')
            GROUP BY user_id
        """))
        commission_rows = await session.execute(sql_text("""
            SELECT
                rc.referrer_id AS user_id,
                COALESCE(SUM(rc.amount_toman), 0) AS toman_total,
                COALESCE(SUM(rc.amount_usdt), 0) AS usd_total
            FROM referral_commissions rc
            INNER JOIN payments p ON p.id = rc.payment_id
            WHERE LOWER(COALESCE(p.payment_method, '')) = 'card'
            GROUP BY rc.referrer_id
        """))

        totals: dict[int, dict[str, float]] = {}
        for row in card_wallet_rows.mappings():
            user_id = int(row["user_id"] or 0)
            if user_id <= 0:
                continue
            bucket = totals.setdefault(user_id, {"usd": 0.0, "toman": 0.0})
            bucket["usd"] += float(row["usd_total"] or 0.0)
            bucket["toman"] += float(row["toman_total"] or 0.0)
        for row in commission_rows.mappings():
            user_id = int(row["user_id"] or 0)
            if user_id <= 0:
                continue
            bucket = totals.setdefault(user_id, {"usd": 0.0, "toman": 0.0})
            bucket["usd"] += float(row["usd_total"] or 0.0)
            bucket["toman"] += float(row["toman_total"] or 0.0)

        if totals:
            for user_id, bucket in totals.items():
                current = await session.execute(
                    sql_text(
                        "SELECT wallet_balance_usdt, COALESCE(wallet_balance_toman, 0) AS wallet_balance_toman "
                        "FROM users WHERE id = :user_id"
                    ),
                    {"user_id": user_id},
                )
                row = current.mappings().one_or_none()
                if not row:
                    continue
                current_usd = float(row["wallet_balance_usdt"] or 0.0)
                usd_move = min(current_usd, float(bucket["usd"] or 0.0))
                toman_move = int(round(bucket["toman"] or 0.0))
                await session.execute(
                    sql_text(
                        "UPDATE users SET wallet_balance_usdt = :usd, wallet_balance_toman = wallet_balance_toman + :toman, updated_at = CURRENT_TIMESTAMP "
                        "WHERE id = :user_id"
                    ),
                    {
                        "usd": max(current_usd - usd_move, 0.0),
                        "toman": max(toman_move, 0),
                        "user_id": user_id,
                    },
                )
                if usd_move < float(bucket["usd"] or 0.0):
                    logger.warning(
                        f"Wallet backfill clipped USD move for user_id={user_id}: "
                        f"moved={usd_move:.8f} expected={float(bucket['usd'] or 0.0):.8f}"
                    )
            await session.commit()

        await set_setting(session, marker_key, "1")
