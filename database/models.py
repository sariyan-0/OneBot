"""
database/models.py — جداول دیتابیس با SQLAlchemy 2.0 Async
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedColumn, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """کلاس پایه برای تمام مدل‌ها."""
    pass


# ──────────────────────────────────────────────
# جدول کاربران
# ──────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = MappedColumn(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = MappedColumn(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = MappedColumn(String(64), nullable=True)
    first_name: Mapped[Optional[str]] = MappedColumn(String(128), nullable=True)
    is_admin: Mapped[bool] = MappedColumn(Boolean, default=False, nullable=False)
    wallet_balance_usdt: Mapped[float] = MappedColumn(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    # کد دعوت منحصر به فرد برای سیستم referral
    referral_code: Mapped[Optional[str]] = MappedColumn(String(16), unique=True, nullable=True, index=True)
    # کاربری که این کاربر را دعوت کرده
    referred_by: Mapped[Optional[int]] = MappedColumn(Integer, ForeignKey("users.id"), nullable=True)

    # رابطه‌ها
    subscriptions: Mapped[list["Subscription"]] = relationship(
        "Subscription", back_populates="user", cascade="all, delete-orphan"
    )
    payments: Mapped[list["Payment"]] = relationship(
        "Payment", back_populates="user", cascade="all, delete-orphan"
    )
    tickets: Mapped[list["Ticket"]] = relationship(
        "Ticket", back_populates="user", cascade="all, delete-orphan",
        foreign_keys="Ticket.user_id",
    )
    referrals_made: Mapped[list["Referral"]] = relationship(
        "Referral", back_populates="referrer", foreign_keys="Referral.referrer_id",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} telegram_id={self.telegram_id}>"


# ──────────────────────────────────────────────
# جدول اشتراک‌ها
# ──────────────────────────────────────────────

class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = MappedColumn(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = MappedColumn(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # اطلاعات پنل
    email: Mapped[str] = MappedColumn(String(128), unique=True, nullable=False)
    client_uuid: Mapped[str] = MappedColumn(String(36), nullable=False)
    sub_id: Mapped[str] = MappedColumn(String(32), nullable=False)
    plan_id: Mapped[Optional[int]] = MappedColumn(Integer, ForeignKey("plans.id"), nullable=True, index=True)
    inbound_id: Mapped[int] = MappedColumn(Integer, nullable=False)

    # ترافیک و انقضا
    traffic_limit_gb: Mapped[int] = MappedColumn(Integer, default=0, nullable=False)
    used_traffic_bytes: Mapped[int] = MappedColumn(BigInteger, default=0, nullable=False)
    expiry_date: Mapped[Optional[datetime]] = MappedColumn(DateTime(timezone=True), nullable=True)

    # محدودیت IP همزمان (از پلن — 0 = نامحدود)
    limit_ip: Mapped[int] = MappedColumn(Integer, default=0, nullable=False)

    # فلگ‌های هشدار انقضا — جلوگیری از ارسال تکراری
    warned_7d: Mapped[bool] = MappedColumn(Boolean, default=False, nullable=False)
    warned_3d: Mapped[bool] = MappedColumn(Boolean, default=False, nullable=False)
    warned_1d: Mapped[bool] = MappedColumn(Boolean, default=False, nullable=False)

    # وضعیت: active | expired | depleted | disabled | pending
    status: Mapped[str] = MappedColumn(String(16), default="active", nullable=False, index=True)

    created_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    # رابطه‌ها
    user: Mapped["User"] = relationship("User", back_populates="subscriptions")

    def __repr__(self) -> str:
        return f"<Subscription id={self.id} email={self.email} status={self.status}>"


# ──────────────────────────────────────────────
# جدول پرداخت‌ها
# ──────────────────────────────────────────────

class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = MappedColumn(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = MappedColumn(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # شناسه‌های پرداخت
    order_id: Mapped[str] = MappedColumn(String(64), unique=True, nullable=False, index=True)
    payment_id: Mapped[Optional[str]] = MappedColumn(String(64), nullable=True, index=True)  # از NOWPayments

    # اطلاعات مالی
    amount_usdt: Mapped[float] = MappedColumn(Float, nullable=False)
    pay_currency: Mapped[str] = MappedColumn(String(20), default="usdttrc20", nullable=False)
    pay_address: Mapped[Optional[str]] = MappedColumn(String(128), nullable=True)

    # اطلاعات پلن
    inbound_id: Mapped[int] = MappedColumn(Integer, nullable=False)

    # روش پرداخت: crypto | card
    payment_method: Mapped[str] = MappedColumn(String(10), default="crypto", nullable=False)

    # وضعیت: pending | waiting | confirming | confirmed | failed | expired | awaiting_review
    status: Mapped[str] = MappedColumn(String(20), default="pending", nullable=False, index=True)

    # زمان انقضای invoice
    expires_at: Mapped[Optional[datetime]] = MappedColumn(DateTime(timezone=True), nullable=True)

    # اطلاعات کارت به کارت
    amount_rial: Mapped[Optional[int]] = MappedColumn(Integer, nullable=True)   # مبلغ به ریال
    receipt_file_id: Mapped[Optional[str]] = MappedColumn(String(256), nullable=True)  # file_id یا متن رسید
    receipt_type: Mapped[Optional[str]] = MappedColumn(String(10), nullable=True)  # photo | text

    # اشتراک ایجادشده بعد از پرداخت موفق
    subscription_id: Mapped[Optional[int]] = MappedColumn(
        Integer, ForeignKey("subscriptions.id"), nullable=True
    )

    created_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = MappedColumn(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    # رابطه‌ها
    user: Mapped["User"] = relationship("User", back_populates="payments")

    def __repr__(self) -> str:
        return f"<Payment id={self.id} order_id={self.order_id} status={self.status}>"


# ──────────────────────────────────────────────
# جدول تیکت‌های پشتیبانی
# ──────────────────────────────────────────────

class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = MappedColumn(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = MappedColumn(Integer, ForeignKey("users.id"), nullable=False, index=True)

    subject: Mapped[str] = MappedColumn(String(256), nullable=False)
    # وضعیت: open | in_progress | closed
    status: Mapped[str] = MappedColumn(String(16), default="open", nullable=False, index=True)

    created_at: Mapped[datetime] = MappedColumn(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = MappedColumn(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    closed_at: Mapped[Optional[datetime]] = MappedColumn(DateTime(timezone=True), nullable=True)

    # رابطه‌ها
    user: Mapped["User"] = relationship("User", back_populates="tickets", foreign_keys=[user_id])
    messages: Mapped[list["TicketMessage"]] = relationship(
        "TicketMessage", back_populates="ticket", cascade="all, delete-orphan", order_by="TicketMessage.created_at"
    )

    def __repr__(self) -> str:
        return f"<Ticket id={self.id} status={self.status}>"


# ──────────────────────────────────────────────
# جدول پیام‌های تیکت
# ──────────────────────────────────────────────

class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id: Mapped[int] = MappedColumn(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = MappedColumn(Integer, ForeignKey("tickets.id"), nullable=False, index=True)
    sender_id: Mapped[int] = MappedColumn(Integer, ForeignKey("users.id"), nullable=False)

    body: Mapped[str] = MappedColumn(Text, nullable=False)
    is_admin_reply: Mapped[bool] = MappedColumn(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = MappedColumn(DateTime(timezone=True), default=utcnow, nullable=False)

    # رابطه‌ها
    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="messages")

    def __repr__(self) -> str:
        return f"<TicketMessage id={self.id} ticket_id={self.ticket_id}>"


# ──────────────────────────────────────────────
# جدول referral‌ها
# ──────────────────────────────────────────────

class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = MappedColumn(Integer, primary_key=True, autoincrement=True)
    referrer_id: Mapped[int] = MappedColumn(Integer, ForeignKey("users.id"), nullable=False, index=True)
    referred_id: Mapped[int] = MappedColumn(Integer, ForeignKey("users.id"), nullable=False, unique=True)

    # پاداش: تعداد روز رایگان که به referrer تعلق می‌گیرد
    reward_days: Mapped[int] = MappedColumn(Integer, default=0, nullable=False)
    reward_granted: Mapped[bool] = MappedColumn(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = MappedColumn(DateTime(timezone=True), default=utcnow, nullable=False)

    # رابطه‌ها
    referrer: Mapped["User"] = relationship("User", back_populates="referrals_made", foreign_keys=[referrer_id])

    def __repr__(self) -> str:
        return f"<Referral referrer={self.referrer_id} referred={self.referred_id}>"


# ──────────────────────────────────────────────
# جدول پلن‌های VPN (قابل تنظیم توسط ادمین)
# ──────────────────────────────────────────────

class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = MappedColumn(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = MappedColumn(String(64), nullable=False)

    # حجمی یا نامحدود
    traffic_gb: Mapped[int] = MappedColumn(Integer, default=0, nullable=False)  # 0 = نامحدود
    duration_days: Mapped[int] = MappedColumn(Integer, default=30, nullable=False)
    price_usdt: Mapped[float] = MappedColumn(Float, nullable=False)
    price_toman: Mapped[int] = MappedColumn(Integer, default=0, nullable=False)

    # نامحدود با تعداد کاربر — مثلاً 1، 2، 3 دستگاه هم‌زمان
    limit_ip: Mapped[int] = MappedColumn(Integer, default=0, nullable=False)  # 0 = نامحدود

    # اینباندهای مجاز (JSON list رشته‌ای — مثلاً "1,3,5")
    inbound_ids: Mapped[str] = MappedColumn(String(256), default="", nullable=False)

    is_active: Mapped[bool] = MappedColumn(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = MappedColumn(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = MappedColumn(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = MappedColumn(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    def get_inbound_ids(self) -> list[int]:
        """تبدیل رشته به لیست int."""
        if not self.inbound_ids:
            return []
        return [int(x.strip()) for x in self.inbound_ids.split(",") if x.strip().isdigit()]

    def __repr__(self) -> str:
        return f"<Plan id={self.id} name={self.name} price={self.price_usdt}>"


# ──────────────────────────────────────────────
# جدول کدهای تخفیف
# ──────────────────────────────────────────────

class DiscountCode(Base):
    __tablename__ = "discount_codes"

    id: Mapped[int] = MappedColumn(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = MappedColumn(String(32), unique=True, nullable=False, index=True)

    # درصد تخفیف (مثلاً 20 = 20٪)
    percent: Mapped[int] = MappedColumn(Integer, nullable=False)

    # تعداد دفعات استفاده — None = نامحدود
    max_uses: Mapped[Optional[int]] = MappedColumn(Integer, nullable=True)
    used_count: Mapped[int] = MappedColumn(Integer, default=0, nullable=False)

    is_active: Mapped[bool] = MappedColumn(Boolean, default=True, nullable=False)
    expires_at: Mapped[Optional[datetime]] = MappedColumn(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = MappedColumn(DateTime(timezone=True), default=utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<DiscountCode code={self.code} percent={self.percent}>"


# ──────────────────────────────────────────────
# جدول اشتراک‌های تست (یک‌بار هر کاربر)
# ──────────────────────────────────────────────

class TestSubscriptionRecord(Base):
    __tablename__ = "test_subscription_records"

    id: Mapped[int] = MappedColumn(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = MappedColumn(BigInteger, unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = MappedColumn(DateTime(timezone=True), default=utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<TestSubRecord telegram_id={self.telegram_id}>"


# ──────────────────────────────────────────────
# جدول فعالیت‌های زنده پنل
# ──────────────────────────────────────────────

class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = MappedColumn(Integer, primary_key=True, autoincrement=True)
    direction: Mapped[str] = MappedColumn(String(8), nullable=False, index=True)  # incoming | outgoing
    event_type: Mapped[str] = MappedColumn(String(32), nullable=False, index=True)
    telegram_id: Mapped[Optional[int]] = MappedColumn(BigInteger, nullable=True, index=True)
    username: Mapped[Optional[str]] = MappedColumn(String(64), nullable=True)
    text: Mapped[str] = MappedColumn(Text, nullable=False, default="")
    created_at: Mapped[datetime] = MappedColumn(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<ActivityLog id={self.id} direction={self.direction} type={self.event_type}>"


# ──────────────────────────────────────────────
# جدول تنظیمات ادمین (key-value)
# ──────────────────────────────────────────────

class AdminSetting(Base):
    __tablename__ = "admin_settings"

    key: Mapped[str] = MappedColumn(String(64), primary_key=True)
    value: Mapped[str] = MappedColumn(Text, nullable=False, default="")

    def __repr__(self) -> str:
        return f"<AdminSetting {self.key}={self.value[:30]}>"
