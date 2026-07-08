"""
database/engine.py — ایجاد engine و session factory برای SQLAlchemy Async
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import inspect, text
from loguru import logger

from config import settings
from .models import Base


def _sqlite_columns(sync_conn, table_name: str) -> set[str]:
    inspector = inspect(sync_conn)
    try:
        return {col["name"] for col in inspector.get_columns(table_name)}
    except Exception:
        return set()


def _ensure_sqlite_column(sync_conn, table_name: str, column_name: str, ddl: str) -> None:
    columns = _sqlite_columns(sync_conn, table_name)
    if column_name not in columns:
        sync_conn.execute(text(ddl))


def _bootstrap_sqlite_schema(sync_conn) -> None:
    sync_conn.execute(text("""
        CREATE TABLE IF NOT EXISTS admin_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
    """))
    sync_conn.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id BIGINT NOT NULL UNIQUE,
            username VARCHAR(64),
            first_name VARCHAR(128),
            is_admin BOOLEAN NOT NULL DEFAULT 0,
            wallet_balance_usdt FLOAT NOT NULL DEFAULT 0,
            wallet_balance_toman INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            referral_code VARCHAR(16) UNIQUE,
            referred_by INTEGER,
            FOREIGN KEY(referred_by) REFERENCES users(id)
        )
    """))
    sync_conn.execute(text("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(64) NOT NULL,
            traffic_gb INTEGER NOT NULL DEFAULT 0,
            duration_days INTEGER NOT NULL DEFAULT 30,
            price_usdt FLOAT NOT NULL,
            price_toman INTEGER NOT NULL DEFAULT 0,
            limit_ip INTEGER NOT NULL DEFAULT 0,
            inbound_ids VARCHAR(256) NOT NULL DEFAULT '',
            is_active BOOLEAN NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    sync_conn.execute(text("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            order_id VARCHAR(64) NOT NULL UNIQUE,
            payment_id VARCHAR(64),
            amount_usdt FLOAT NOT NULL,
            pay_currency VARCHAR(20) NOT NULL DEFAULT 'usdttrc20',
            pay_address VARCHAR(128),
            inbound_id INTEGER NOT NULL,
            payment_method VARCHAR(10) NOT NULL DEFAULT 'crypto',
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            expires_at DATETIME,
            amount_rial INTEGER,
            receipt_file_id VARCHAR(256),
            receipt_type VARCHAR(10),
            subscription_id INTEGER,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(subscription_id) REFERENCES subscriptions(id)
        )
    """))
    sync_conn.execute(text("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            email VARCHAR(128) NOT NULL UNIQUE,
            client_uuid VARCHAR(36) NOT NULL,
            sub_id VARCHAR(32) NOT NULL,
            plan_id INTEGER,
            inbound_id INTEGER NOT NULL,
            traffic_limit_gb INTEGER NOT NULL DEFAULT 0,
            used_traffic_bytes BIGINT NOT NULL DEFAULT 0,
            expiry_date DATETIME,
            limit_ip INTEGER NOT NULL DEFAULT 0,
            warned_7d BOOLEAN NOT NULL DEFAULT 0,
            warned_3d BOOLEAN NOT NULL DEFAULT 0,
            warned_1d BOOLEAN NOT NULL DEFAULT 0,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(plan_id) REFERENCES plans(id)
        )
    """))
    sync_conn.execute(text("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject VARCHAR(256) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'open',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            closed_at DATETIME,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """))
    sync_conn.execute(text("""
        CREATE TABLE IF NOT EXISTS ticket_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            is_admin_reply BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(ticket_id) REFERENCES tickets(id),
            FOREIGN KEY(sender_id) REFERENCES users(id)
        )
    """))
    sync_conn.execute(text("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL UNIQUE,
            reward_days INTEGER NOT NULL DEFAULT 0,
            reward_granted BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(referrer_id) REFERENCES users(id),
            FOREIGN KEY(referred_id) REFERENCES users(id)
        )
    """))
    sync_conn.execute(text("""
        CREATE TABLE IF NOT EXISTS referral_commissions (
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
    sync_conn.execute(text("""
        CREATE TABLE IF NOT EXISTS discount_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code VARCHAR(32) NOT NULL UNIQUE,
            percent INTEGER NOT NULL,
            max_uses INTEGER,
            used_count INTEGER NOT NULL DEFAULT 0,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            expires_at DATETIME,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    sync_conn.execute(text("""
        CREATE TABLE IF NOT EXISTS test_subscription_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id BIGINT NOT NULL UNIQUE,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    sync_conn.execute(text("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            direction VARCHAR(8) NOT NULL,
            event_type VARCHAR(32) NOT NULL,
            telegram_id BIGINT,
            username VARCHAR(64),
            text TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))

    _ensure_sqlite_column(sync_conn, "subscriptions", "plan_id", "ALTER TABLE subscriptions ADD COLUMN plan_id INTEGER")
    _ensure_sqlite_column(sync_conn, "users", "wallet_balance_usdt", "ALTER TABLE users ADD COLUMN wallet_balance_usdt FLOAT NOT NULL DEFAULT 0")
    _ensure_sqlite_column(sync_conn, "users", "wallet_balance_toman", "ALTER TABLE users ADD COLUMN wallet_balance_toman INTEGER NOT NULL DEFAULT 0")
    _ensure_sqlite_column(sync_conn, "plans", "price_toman", "ALTER TABLE plans ADD COLUMN price_toman INTEGER NOT NULL DEFAULT 0")
    _ensure_sqlite_column(sync_conn, "plans", "limit_ip", "ALTER TABLE plans ADD COLUMN limit_ip INTEGER NOT NULL DEFAULT 0")
    _ensure_sqlite_column(sync_conn, "plans", "inbound_ids", "ALTER TABLE plans ADD COLUMN inbound_ids VARCHAR(256) NOT NULL DEFAULT ''")
    _ensure_sqlite_column(sync_conn, "plans", "sort_order", "ALTER TABLE plans ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
    _ensure_sqlite_column(sync_conn, "referral_commissions", "amount_toman", "ALTER TABLE referral_commissions ADD COLUMN amount_toman INTEGER NOT NULL DEFAULT 0")
    for stmt in (
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_plan_id ON subscriptions(plan_id)",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status)",
        "CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)",
        "CREATE INDEX IF NOT EXISTS idx_tickets_status_updated ON tickets(status, updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_test_subscription_records_created_at ON test_subscription_records(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_activity_logs_created_at ON activity_logs(created_at)",
    ):
        sync_conn.execute(text(stmt))


async def _bootstrap_pg_schema() -> None:
    stmts = [
        """
        CREATE TABLE IF NOT EXISTS admin_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL UNIQUE,
            username VARCHAR(64),
            first_name VARCHAR(128),
            is_admin BOOLEAN NOT NULL DEFAULT FALSE,
            wallet_balance_usdt DOUBLE PRECISION NOT NULL DEFAULT 0,
            wallet_balance_toman INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            referral_code VARCHAR(16) UNIQUE,
            referred_by INTEGER REFERENCES users(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS plans (
            id SERIAL PRIMARY KEY,
            name VARCHAR(64) NOT NULL,
            traffic_gb INTEGER NOT NULL DEFAULT 0,
            duration_days INTEGER NOT NULL DEFAULT 30,
            price_usdt DOUBLE PRECISION NOT NULL,
            price_toman INTEGER NOT NULL DEFAULT 0,
            limit_ip INTEGER NOT NULL DEFAULT 0,
            inbound_ids VARCHAR(256) NOT NULL DEFAULT '',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            email VARCHAR(128) NOT NULL UNIQUE,
            client_uuid VARCHAR(36) NOT NULL,
            sub_id VARCHAR(32) NOT NULL,
            plan_id INTEGER REFERENCES plans(id),
            inbound_id INTEGER NOT NULL,
            traffic_limit_gb INTEGER NOT NULL DEFAULT 0,
            used_traffic_bytes BIGINT NOT NULL DEFAULT 0,
            expiry_date TIMESTAMPTZ,
            limit_ip INTEGER NOT NULL DEFAULT 0,
            warned_7d BOOLEAN NOT NULL DEFAULT FALSE,
            warned_3d BOOLEAN NOT NULL DEFAULT FALSE,
            warned_1d BOOLEAN NOT NULL DEFAULT FALSE,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            order_id VARCHAR(64) NOT NULL UNIQUE,
            payment_id VARCHAR(64),
            amount_usdt DOUBLE PRECISION NOT NULL,
            pay_currency VARCHAR(20) NOT NULL DEFAULT 'usdttrc20',
            pay_address VARCHAR(128),
            inbound_id INTEGER NOT NULL,
            payment_method VARCHAR(10) NOT NULL DEFAULT 'crypto',
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            expires_at TIMESTAMPTZ,
            amount_rial INTEGER,
            receipt_file_id VARCHAR(256),
            receipt_type VARCHAR(10),
            subscription_id INTEGER REFERENCES subscriptions(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            subject VARCHAR(256) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'open',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMPTZ
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ticket_messages (
            id SERIAL PRIMARY KEY,
            ticket_id INTEGER NOT NULL REFERENCES tickets(id),
            sender_id INTEGER NOT NULL REFERENCES users(id),
            body TEXT NOT NULL,
            is_admin_reply BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS referrals (
            id SERIAL PRIMARY KEY,
            referrer_id INTEGER NOT NULL REFERENCES users(id),
            referred_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
            reward_days INTEGER NOT NULL DEFAULT 0,
            reward_granted BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS referral_commissions (
            id SERIAL PRIMARY KEY,
            referrer_id INTEGER NOT NULL REFERENCES users(id),
            referred_id INTEGER NOT NULL REFERENCES users(id),
            payment_id INTEGER NOT NULL UNIQUE REFERENCES payments(id),
            percent DOUBLE PRECISION NOT NULL DEFAULT 0,
            amount_usdt DOUBLE PRECISION NOT NULL DEFAULT 0,
            amount_toman INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS discount_codes (
            id SERIAL PRIMARY KEY,
            code VARCHAR(32) NOT NULL UNIQUE,
            percent INTEGER NOT NULL,
            max_uses INTEGER,
            used_count INTEGER NOT NULL DEFAULT 0,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS test_subscription_records (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS activity_logs (
            id SERIAL PRIMARY KEY,
            direction VARCHAR(8) NOT NULL,
            event_type VARCHAR(32) NOT NULL,
            telegram_id BIGINT,
            username VARCHAR(64),
            text TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ]

    pool = getPgPool()
    for stmt in stmts:
        await pool.query(stmt)

    for stmt in (
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS plan_id INTEGER",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_balance_usdt DOUBLE PRECISION NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_balance_toman INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS price_toman INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS limit_ip INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS inbound_ids VARCHAR(256) NOT NULL DEFAULT ''",
        "ALTER TABLE plans ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE referral_commissions ADD COLUMN IF NOT EXISTS amount_toman INTEGER NOT NULL DEFAULT 0",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_plan_id ON subscriptions(plan_id)",
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status)",
        "CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status)",
        "CREATE INDEX IF NOT EXISTS idx_tickets_status_updated ON tickets(status, updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_test_subscription_records_created_at ON test_subscription_records(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_activity_logs_created_at ON activity_logs(created_at)",
    ):
        await pool.query(stmt)

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
        def _ensure_plan_columns(sync_conn):
            inspector = inspect(sync_conn)
            columns = {col["name"] for col in inspector.get_columns("plans")}
            if "price_toman" not in columns:
                sync_conn.execute(text("ALTER TABLE plans ADD COLUMN price_toman INTEGER NOT NULL DEFAULT 0"))
            if "limit_ip" not in columns:
                sync_conn.execute(text("ALTER TABLE plans ADD COLUMN limit_ip INTEGER NOT NULL DEFAULT 0"))
            if "inbound_ids" not in columns:
                sync_conn.execute(text("ALTER TABLE plans ADD COLUMN inbound_ids VARCHAR(256) NOT NULL DEFAULT ''"))
            if "sort_order" not in columns:
                sync_conn.execute(text("ALTER TABLE plans ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"))
        def _ensure_referral_commissions_table(sync_conn):
            inspector = inspect(sync_conn)
            tables = set(inspector.get_table_names())
            if "referral_commissions" not in tables:
                if settings.db_url.startswith("postgres"):
                    sync_conn.execute(text("""
                        CREATE TABLE referral_commissions (
                            id SERIAL PRIMARY KEY,
                            referrer_id INTEGER NOT NULL REFERENCES users(id),
                            referred_id INTEGER NOT NULL REFERENCES users(id),
                            payment_id INTEGER NOT NULL UNIQUE REFERENCES payments(id),
                            percent DOUBLE PRECISION NOT NULL DEFAULT 0,
                            amount_usdt DOUBLE PRECISION NOT NULL DEFAULT 0,
                            amount_toman INTEGER NOT NULL DEFAULT 0,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                    """))
                else:
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
            else:
                columns = {col["name"] for col in inspector.get_columns("referral_commissions")}
                if "amount_toman" not in columns:
                    sync_conn.execute(text("ALTER TABLE referral_commissions ADD COLUMN amount_toman INTEGER NOT NULL DEFAULT 0"))
        if settings.db_url.startswith("postgres"):
            await conn.run_sync(_ensure_subscription_plan_id)
            await conn.run_sync(_ensure_user_wallet_balance)
            await conn.run_sync(_ensure_plan_columns)
            await conn.run_sync(_ensure_referral_commissions_table)
        else:
            await conn.run_sync(_bootstrap_sqlite_schema)
            await conn.run_sync(_ensure_subscription_plan_id)
            await conn.run_sync(_ensure_user_wallet_balance)
            await conn.run_sync(_ensure_plan_columns)
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
