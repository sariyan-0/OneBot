"""
database/engine.py — ایجاد engine و session factory برای SQLAlchemy Async
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import inspect, text

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
        def _ensure_plan_price_toman(sync_conn):
            inspector = inspect(sync_conn)
            columns = {col["name"] for col in inspector.get_columns("plans")}
            if "price_toman" not in columns:
                sync_conn.execute(text("ALTER TABLE plans ADD COLUMN price_toman INTEGER NOT NULL DEFAULT 0"))
        await conn.run_sync(_ensure_subscription_plan_id)
        await conn.run_sync(_ensure_user_wallet_balance)
        await conn.run_sync(_ensure_plan_price_toman)
