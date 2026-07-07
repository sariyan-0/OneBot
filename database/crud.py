"""
database/crud.py — توابع CRUD برای User و Subscription
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AdminSetting, DiscountCode, Payment, Plan, Referral,
    ReferralCommission, Subscription, TestSubscriptionRecord, Ticket, TicketMessage, User,
)


# ──────────────────────────────────────────────
# User CRUD
# ──────────────────────────────────────────────

async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> Optional[User]:
    """دریافت کاربر بر اساس telegram_id."""
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


async def get_user_wallet_balances(session: AsyncSession, user_id: int) -> tuple[float, int]:
    """خواندن موجودی کیف پول کاربر به تفکیک دلار و تومان."""
    result = await session.execute(
        select(User.wallet_balance_usdt, User.wallet_balance_toman).where(User.id == user_id)
    )
    row = result.one_or_none()
    if not row:
        return 0.0, 0
    return float(row[0] or 0.0), int(row[1] or 0)


async def get_user_wallet_balance(session: AsyncSession, user_id: int) -> float:
    """خواندن موجودی دلار کیف پول کاربر."""
    usd, _ = await get_user_wallet_balances(session, user_id)
    return usd


async def get_user_wallet_balance_toman(session: AsyncSession, user_id: int) -> int:
    """خواندن موجودی تومان کیف پول کاربر."""
    _, toman = await get_user_wallet_balances(session, user_id)
    return toman


async def credit_user_wallet(
    session: AsyncSession,
    user_id: int,
    amount: float,
    currency: str = "usd",
) -> float:
    """افزایش موجودی کیف پول کاربر و برگرداندن موجودی جدید."""
    if amount <= 0:
        raise ValueError("wallet credit amount must be positive")
    currency = str(currency or "usd").lower()
    if currency not in {"usd", "toman"}:
        raise ValueError("unsupported wallet currency")
    if currency == "toman":
        await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                wallet_balance_toman=User.wallet_balance_toman + int(round(amount)),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
        return float(await get_user_wallet_balance_toman(session, user_id))
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            wallet_balance_usdt=User.wallet_balance_usdt + amount,
            updated_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    return await get_user_wallet_balance(session, user_id)


async def debit_user_wallet(
    session: AsyncSession,
    user_id: int,
    amount: float,
    currency: str = "usd",
) -> float:
    """کسر موجودی کیف پول کاربر و برگرداندن موجودی جدید."""
    if amount <= 0:
        raise ValueError("wallet debit amount must be positive")
    currency = str(currency or "usd").lower()
    if currency not in {"usd", "toman"}:
        raise ValueError("unsupported wallet currency")

    if currency == "toman":
        amount_int = int(round(amount))
        result = await session.execute(
            update(User)
            .where(User.id == user_id, User.wallet_balance_toman >= amount_int)
            .values(
                wallet_balance_toman=User.wallet_balance_toman - amount_int,
                updated_at=datetime.now(timezone.utc),
            )
        )
        if getattr(result, "rowcount", 0) == 0:
            await session.rollback()
            raise ValueError("insufficient wallet balance")
        await session.commit()
        return float(await get_user_wallet_balance_toman(session, user_id))

    result = await session.execute(
        update(User)
        .where(User.id == user_id, User.wallet_balance_usdt >= amount)
        .values(
            wallet_balance_usdt=User.wallet_balance_usdt - amount,
            updated_at=datetime.now(timezone.utc),
        )
    )
    if getattr(result, "rowcount", 0) == 0:
        await session.rollback()
        raise ValueError("insufficient wallet balance")
    await session.commit()
    return await get_user_wallet_balance(session, user_id)


async def set_user_wallet_balance(
    session: AsyncSession,
    user_id: int,
    amount: float,
    currency: str = "usd",
) -> None:
    """تنظیم مستقیم موجودی کیف پول کاربر."""
    currency = str(currency or "usd").lower()
    if currency not in {"usd", "toman"}:
        raise ValueError("unsupported wallet currency")
    values = {"updated_at": datetime.now(timezone.utc)}
    if currency == "toman":
        values["wallet_balance_toman"] = max(int(round(amount)), 0)
    else:
        values["wallet_balance_usdt"] = max(amount, 0.0)
    await session.execute(
        update(User).where(User.id == user_id).values(**values)
    )
    await session.commit()


async def get_user_by_username(session: AsyncSession, username: str) -> Optional[User]:
    """دریافت کاربر بر اساس username — case-insensitive (بدون @)."""
    from sqlalchemy import func as _func
    clean = username.lstrip("@").strip()
    result = await session.execute(
        select(User).where(_func.lower(User.username) == clean.lower())
    )
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    is_admin: bool = False,
) -> User:
    """ایجاد کاربر جدید."""
    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        is_admin=is_admin,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    logger.info(f"کاربر جدید ثبت شد: telegram_id={telegram_id}")
    return user


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    admin_ids: Optional[List[int]] = None,
) -> tuple[User, bool]:
    """
    دریافت کاربر موجود یا ایجاد کاربر جدید.
    در صورت UNIQUE constraint (race condition یا دیتابیس قدیمی)،
    کاربر موجود را برمی‌گرداند بدون crash.

    Returns:
        (user, created) — created=True اگر کاربر تازه ساخته شده
    """
    from sqlalchemy.exc import IntegrityError

    user = await get_user_by_telegram_id(session, telegram_id)
    if user:
        if user.username != username or user.first_name != first_name:
            user.username = username
            user.first_name = first_name
            user.updated_at = datetime.now(timezone.utc)
            await session.commit()
        return user, False

    is_admin = bool(admin_ids and telegram_id in admin_ids)
    try:
        user = await create_user(session, telegram_id, username, first_name, is_admin)
        return user, True
    except IntegrityError:
        # کاربر همزمان توسط درخواست دیگری ساخته شده — rollback و دوباره بگیر
        await session.rollback()
        user = await get_user_by_telegram_id(session, telegram_id)
        if user:
            return user, False
        raise


# ──────────────────────────────────────────────
# Subscription CRUD
# ──────────────────────────────────────────────

async def create_subscription(
    session: AsyncSession,
    user_id: int,
    email: str,
    client_uuid: str,
    sub_id: str,
    inbound_id: int,
    traffic_limit_gb: int = 0,
    expiry_date: Optional[datetime] = None,
    limit_ip: int = 0,
    plan_id: Optional[int] = None,
) -> Subscription:
    """ذخیره اشتراک جدید در دیتابیس."""
    sub = Subscription(
        user_id=user_id,
        email=email,
        client_uuid=client_uuid,
        sub_id=sub_id,
        plan_id=plan_id,
        inbound_id=inbound_id,
        traffic_limit_gb=traffic_limit_gb,
        expiry_date=expiry_date,
        limit_ip=limit_ip,
        status="active",
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    logger.info(f"اشتراک جدید ذخیره شد: email={email}, user_id={user_id}")
    return sub


async def get_user_subscriptions(
    session: AsyncSession,
    user_id: int,
    active_only: bool = False,
) -> List[Subscription]:
    """دریافت تمام اشتراک‌های یک کاربر."""
    query = select(Subscription).where(Subscription.user_id == user_id)
    if active_only:
        query = query.where(Subscription.status == "active")
    query = query.order_by(Subscription.created_at.desc())

    result = await session.execute(query)
    return list(result.scalars().all())


async def get_subscription_by_email(
    session: AsyncSession,
    email: str,
) -> Optional[Subscription]:
    """دریافت اشتراک بر اساس ایمیل."""
    result = await session.execute(
        select(Subscription).where(Subscription.email == email)
    )
    return result.scalar_one_or_none()


async def get_subscription_by_sub_id(
    session: AsyncSession,
    sub_id: str,
) -> Optional[Subscription]:
    """دریافت اشتراک بر اساس sub_id."""
    result = await session.execute(
        select(Subscription).where(Subscription.sub_id == sub_id)
    )
    return result.scalar_one_or_none()


async def update_subscription_traffic(
    session: AsyncSession,
    subscription_id: int,
    used_bytes: int,
) -> None:
    """به‌روزرسانی ترافیک مصرف‌شده."""
    await session.execute(
        update(Subscription)
        .where(Subscription.id == subscription_id)
        .values(
            used_traffic_bytes=used_bytes,
            updated_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()


async def update_subscription_status(
    session: AsyncSession,
    subscription_id: int,
    status: str,
) -> None:
    """به‌روزرسانی وضعیت اشتراک."""
    await session.execute(
        update(Subscription)
        .where(Subscription.id == subscription_id)
        .values(
            status=status,
            updated_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    logger.info(f"وضعیت اشتراک {subscription_id} به '{status}' تغییر کرد.")


# ──────────────────────────────────────────────
# Payment CRUD
# ──────────────────────────────────────────────

async def create_payment(
    session: AsyncSession,
    user_id: int,
    order_id: str,
    amount_usdt: float,
    inbound_id: int,
    payment_id: Optional[str] = None,
    pay_address: Optional[str] = None,
    pay_currency: str = "usdttrc20",
    expires_at: Optional[datetime] = None,
    payment_method: str = "crypto",
    amount_rial: Optional[int] = None,
) -> Payment:
    """ذخیره invoice پرداخت جدید."""
    if payment_method == "card":
        init_status = "awaiting_review"
    elif payment_method == "wallet":
        init_status = "confirmed"
    else:
        init_status = "waiting"
    payment = Payment(
        user_id=user_id,
        order_id=order_id,
        payment_id=payment_id,
        amount_usdt=amount_usdt,
        pay_currency=pay_currency,
        pay_address=pay_address,
        inbound_id=inbound_id,
        payment_method=payment_method,
        amount_rial=amount_rial,
        status=init_status,
        expires_at=expires_at,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    logger.info(f"پرداخت جدید ثبت شد: order_id={order_id}, user_id={user_id}")
    return payment


async def get_payment_by_order_id(
    session: AsyncSession,
    order_id: str,
) -> Optional[Payment]:
    """دریافت پرداخت بر اساس order_id."""
    result = await session.execute(
        select(Payment).where(Payment.order_id == order_id)
    )
    return result.scalar_one_or_none()


async def get_pending_payments(
    session: AsyncSession,
    limit: int = 30,
) -> List[Payment]:
    """تراکنش‌های در انتظار تأیید (کارت به کارت)."""
    from sqlalchemy import select
    result = await session.execute(
        select(Payment)
        .where(Payment.status == "awaiting_review")
        .order_by(Payment.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_payment_by_payment_id(
    session: AsyncSession,
    payment_id: str,
) -> Optional[Payment]:
    """دریافت پرداخت بر اساس payment_id نوپیمنتس."""
    result = await session.execute(
        select(Payment).where(Payment.payment_id == payment_id)
    )
    return result.scalar_one_or_none()


async def update_payment_status(
    session: AsyncSession,
    payment_id_db: int,
    status: str,
    subscription_id: Optional[int] = None,
) -> None:
    """به‌روزرسانی وضعیت پرداخت."""
    values: dict = {
        "status": status,
        "updated_at": datetime.now(timezone.utc),
    }
    if subscription_id is not None:
        values["subscription_id"] = subscription_id

    await session.execute(
        update(Payment)
        .where(Payment.id == payment_id_db)
        .values(**values)
    )
    await session.commit()
    logger.info(f"وضعیت پرداخت {payment_id_db} به '{status}' تغییر کرد.")


async def get_payments_filtered(
    session: AsyncSession,
    status_filter: Optional[str] = None,
    limit: int = 20,
    order_id_search: Optional[str] = None,
) -> List[Payment]:
    """
    دریافت تراکنش‌ها با فیلتر وضعیت یا جستجو order_id.

    status_filter مقادیر مجاز:
      'pending'         — همه تراکنش‌های در صف (کارت + کریپتو)
      'pending_card'    — فقط کارت‌به‌کارت در انتظار تأیید
      'pending_crypto'  — فقط کریپتو در انتظار تأیید شبکه
      'confirmed'       — همه موفق
      'confirmed_card'  — موفق کارت‌به‌کارت
      'confirmed_crypto'— موفق کریپتو
      'failed'          — همه ناموفق
      'failed_card'     — ناموفق کارت‌به‌کارت
      'failed_crypto'   — ناموفق کریپتو (expired/partially_paid هم اینجا)
      None              — همه تراکنش‌ها
    """
    # روش‌های پرداخت کریپتو — شامل MaxelPay و NOWPayments و حالت قدیمی "crypto"
    _CRYPTO_METHODS = ["crypto", "maxelpay", "nowpayments"]

    q = select(Payment)
    if order_id_search:
        q = q.where(Payment.order_id.ilike(f"%{order_id_search}%"))
    elif status_filter == "pending":
        q = q.where(Payment.status.in_(["awaiting_review", "waiting", "confirming", "pending"]))
    elif status_filter == "pending_card":
        q = q.where(
            Payment.payment_method == "card",
            Payment.status.in_(["awaiting_review", "pending", "waiting"]),
        )
    elif status_filter == "pending_crypto":
        # شامل maxelpay + nowpayments + crypto (legacy)
        q = q.where(
            Payment.payment_method.in_(_CRYPTO_METHODS),
            Payment.status.in_(["waiting", "confirming", "pending"]),
        )
    elif status_filter == "confirmed":
        q = q.where(Payment.status.in_(["confirmed", "finished"]))
    elif status_filter == "confirmed_card":
        q = q.where(
            Payment.payment_method == "card",
            Payment.status.in_(["confirmed", "finished"]),
        )
    elif status_filter == "confirmed_crypto":
        q = q.where(
            Payment.payment_method.in_(_CRYPTO_METHODS),
            Payment.status.in_(["confirmed", "finished"]),
        )
    elif status_filter == "failed":
        q = q.where(Payment.status.in_(["failed", "expired", "partially_paid"]))
    elif status_filter == "failed_card":
        q = q.where(
            Payment.payment_method == "card",
            Payment.status.in_(["failed"]),
        )
    elif status_filter == "failed_crypto":
        q = q.where(
            Payment.payment_method.in_(_CRYPTO_METHODS),
            Payment.status.in_(["failed", "expired", "partially_paid"]),
        )
    result = await session.execute(
        q.order_by(Payment.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def get_total_revenue(session: AsyncSession) -> float:
    """محاسبه کل درآمد از پرداخت‌های موفق."""
    from sqlalchemy import func as sql_func
    result = await session.execute(
        select(sql_func.sum(Payment.amount_usdt)).where(
            Payment.status.in_(["confirmed", "finished"])
        )
    )
    total = result.scalar()
    return float(total) if total else 0.0


async def get_stats(session: AsyncSession) -> dict:
    """آمار کامل برای پنل ادمین."""
    from sqlalchemy import func as sql_func

    # ── کاربران ──────────────────────────────────────────
    users_count = (await session.execute(
        select(sql_func.count(User.id))
    )).scalar() or 0

    # کاربران جدید امروز
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    users_today = (await session.execute(
        select(sql_func.count(User.id)).where(User.created_at >= today_start)
    )).scalar() or 0

    # ── اشتراک‌ها ─────────────────────────────────────────
    subs_active = (await session.execute(
        select(sql_func.count(Subscription.id)).where(Subscription.status == "active")
    )).scalar() or 0

    subs_expired = (await session.execute(
        select(sql_func.count(Subscription.id)).where(Subscription.status == "expired")
    )).scalar() or 0

    subs_total = (await session.execute(
        select(sql_func.count(Subscription.id))
    )).scalar() or 0

    # اشتراک‌هایی که ظرف ۷ روز منقضی می‌شوند
    from datetime import timedelta
    week_later = datetime.now(timezone.utc) + timedelta(days=7)
    subs_expiring_soon = (await session.execute(
        select(sql_func.count(Subscription.id)).where(
            Subscription.status == "active",
            Subscription.expiry_date != None,
            Subscription.expiry_date <= week_later,
        )
    )).scalar() or 0

    # ── پرداخت‌ها ─────────────────────────────────────────
    revenue_usdt = await get_total_revenue(session)

    # درآمد امروز
    revenue_today = (await session.execute(
        select(sql_func.sum(Payment.amount_usdt)).where(
            Payment.status.in_(["confirmed", "finished"]),
            Payment.created_at >= today_start,
        )
    )).scalar() or 0.0

    # تراکنش‌های موفق
    payments_confirmed = (await session.execute(
        select(sql_func.count(Payment.id)).where(
            Payment.status.in_(["confirmed", "finished"])
        )
    )).scalar() or 0

    # تراکنش‌های در صف (منتظر تأیید ادمین)
    payments_pending = (await session.execute(
        select(sql_func.count(Payment.id)).where(
            Payment.status.in_(["awaiting_review", "waiting", "confirming"])
        )
    )).scalar() or 0

    # تراکنش‌های ناموفق
    payments_failed = (await session.execute(
        select(sql_func.count(Payment.id)).where(
            Payment.status.in_(["failed", "expired", "partially_paid"])
        )
    )).scalar() or 0

    # ── تیکت‌ها ──────────────────────────────────────────
    tickets_open = (await session.execute(
        select(sql_func.count(Ticket.id)).where(Ticket.status == "open")
    )).scalar() or 0

    tickets_in_progress = (await session.execute(
        select(sql_func.count(Ticket.id)).where(Ticket.status == "in_progress")
    )).scalar() or 0

    return {
        # کاربران
        "total_users": users_count,
        "users_today": users_today,
        # اشتراک‌ها
        "active_subscriptions": subs_active,
        "expired_subscriptions": subs_expired,
        "total_subscriptions": subs_total,
        "expiring_soon": subs_expiring_soon,
        # مالی
        "total_revenue_usdt": float(revenue_usdt),
        "revenue_today_usdt": float(revenue_today),
        "payments_confirmed": payments_confirmed,
        "payments_pending": payments_pending,
        "payments_failed": payments_failed,
        # تیکت‌ها
        "open_tickets": tickets_open,
        "inprogress_tickets": tickets_in_progress,
    }


# ──────────────────────────────────────────────
# Ticket CRUD
# ──────────────────────────────────────────────

async def create_ticket(
    session: AsyncSession,
    user_id: int,
    subject: str,
    first_message: str,
    sender_id: int,
) -> Ticket:
    """ایجاد تیکت جدید با اولین پیام."""
    ticket = Ticket(user_id=user_id, subject=subject, status="open")
    session.add(ticket)
    await session.flush()  # id بگیریم بدون commit

    msg = TicketMessage(
        ticket_id=ticket.id,
        sender_id=sender_id,
        body=first_message,
        is_admin_reply=False,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(ticket)
    logger.info(f"تیکت جدید #{ticket.id} از user_id={user_id}")
    return ticket


async def get_ticket(session: AsyncSession, ticket_id: int) -> Optional[Ticket]:
    """دریافت یک تیکت با پیام‌هایش."""
    from sqlalchemy.orm import selectinload
    result = await session.execute(
        select(Ticket)
        .where(Ticket.id == ticket_id)
        .options(selectinload(Ticket.messages))
    )
    return result.scalar_one_or_none()


async def get_user_tickets(
    session: AsyncSession, user_id: int, limit: int = 10
) -> List[Ticket]:
    """دریافت تیکت‌های یک کاربر."""
    result = await session.execute(
        select(Ticket)
        .where(Ticket.user_id == user_id)
        .order_by(Ticket.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_open_tickets(session: AsyncSession, limit: int = 20) -> List[Ticket]:
    """دریافت تیکت‌های باز برای ادمین."""
    result = await session.execute(
        select(Ticket)
        .where(Ticket.status.in_(["open", "in_progress"]))
        .order_by(Ticket.updated_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def add_ticket_message(
    session: AsyncSession,
    ticket_id: int,
    sender_id: int,
    body: str,
    is_admin_reply: bool = False,
) -> TicketMessage:
    """اضافه کردن پیام به تیکت."""
    msg = TicketMessage(
        ticket_id=ticket_id,
        sender_id=sender_id,
        body=body,
        is_admin_reply=is_admin_reply,
    )
    session.add(msg)
    # بروزرسانی updated_at تیکت
    await session.execute(
        update(Ticket)
        .where(Ticket.id == ticket_id)
        .values(
            status="in_progress" if is_admin_reply else "open",
            updated_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    await session.refresh(msg)
    return msg


async def close_ticket(session: AsyncSession, ticket_id: int) -> None:
    """بستن تیکت."""
    await session.execute(
        update(Ticket)
        .where(Ticket.id == ticket_id)
        .values(status="closed", closed_at=datetime.now(timezone.utc))
    )
    await session.commit()
    logger.info(f"تیکت #{ticket_id} بسته شد.")


# ──────────────────────────────────────────────
# Referral CRUD
# ──────────────────────────────────────────────

async def get_user_by_referral_code(
    session: AsyncSession, code: str
) -> Optional[User]:
    """یافتن کاربر با کد referral."""
    result = await session.execute(
        select(User).where(User.referral_code == code)
    )
    return result.scalar_one_or_none()


async def set_referral_code(
    session: AsyncSession, user_id: int, code: str
) -> None:
    """ذخیره کد referral برای کاربر."""
    await session.execute(
        update(User).where(User.id == user_id).values(referral_code=code)
    )
    await session.commit()


async def create_referral(
    session: AsyncSession,
    referrer_id: int,
    referred_id: int,
    reward_days: int = 3,
) -> Referral:
    """ثبت رابطه referral."""
    ref = Referral(
        referrer_id=referrer_id,
        referred_id=referred_id,
        reward_days=reward_days,
    )
    session.add(ref)
    # ذخیره referred_by در کاربر جدید
    await session.execute(
        update(User).where(User.id == referred_id).values(referred_by=referrer_id)
    )
    await session.commit()
    await session.refresh(ref)
    logger.info(f"Referral ثبت شد: referrer={referrer_id}, referred={referred_id}")
    return ref


async def get_referral_stats(
    session: AsyncSession, user_id: int
) -> dict:
    """آمار referral یک کاربر."""
    from sqlalchemy import func as sql_func

    total = (await session.execute(
        select(sql_func.count(Referral.id)).where(Referral.referrer_id == user_id)
    )).scalar() or 0

    rewarded = (await session.execute(
        select(sql_func.count(Referral.id))
        .where(Referral.referrer_id == user_id, Referral.reward_granted == True)
    )).scalar() or 0

    total_days = (await session.execute(
        select(sql_func.sum(Referral.reward_days))
        .where(Referral.referrer_id == user_id, Referral.reward_granted == True)
    )).scalar() or 0

    total_commission_usdt = (await session.execute(
        select(sql_func.sum(ReferralCommission.amount_usdt))
        .where(ReferralCommission.referrer_id == user_id)
    )).scalar() or 0

    total_commission_toman = (await session.execute(
        select(sql_func.sum(ReferralCommission.amount_toman))
        .where(ReferralCommission.referrer_id == user_id)
    )).scalar() or 0

    return {
        "total_referrals": total,
        "rewarded_referrals": rewarded,
        "total_reward_days": int(total_days),
        "total_commission_usdt": float(total_commission_usdt or 0),
        "total_commission_toman": int(total_commission_toman or 0),
    }


async def mark_referral_rewarded(
    session: AsyncSession, referral_id: int
) -> None:
    """علامت‌گذاری پاداش referral به عنوان پرداخت‌شده."""
    await session.execute(
        update(Referral).where(Referral.id == referral_id).values(reward_granted=True)
    )
    await session.commit()


async def get_referral_by_referred_id(
    session: AsyncSession,
    referred_id: int,
) -> Optional[Referral]:
    result = await session.execute(
        select(Referral).where(Referral.referred_id == referred_id)
    )
    return result.scalar_one_or_none()


async def get_referral_commission_by_payment_id(
    session: AsyncSession,
    payment_id: int,
) -> Optional[ReferralCommission]:
    result = await session.execute(
        select(ReferralCommission).where(ReferralCommission.payment_id == payment_id)
    )
    return result.scalar_one_or_none()


async def create_referral_commission(
    session: AsyncSession,
    referrer_id: int,
    referred_id: int,
    payment_id: int,
    percent: float,
    amount_usdt: float,
    amount_toman: int,
) -> ReferralCommission:
    commission = ReferralCommission(
        referrer_id=referrer_id,
        referred_id=referred_id,
        payment_id=payment_id,
        percent=percent,
        amount_usdt=amount_usdt,
        amount_toman=amount_toman,
    )
    session.add(commission)
    await session.commit()
    await session.refresh(commission)
    return commission


# ──────────────────────────────────────────────
# Plan CRUD
# ──────────────────────────────────────────────

async def get_active_plans(session: AsyncSession) -> List[Plan]:
    """دریافت پلن‌های فعال مرتب‌شده."""
    result = await session.execute(
        select(Plan).where(Plan.is_active == True).order_by(Plan.sort_order, Plan.id)
    )
    return list(result.scalars().all())


async def get_all_plans(session: AsyncSession) -> List[Plan]:
    result = await session.execute(select(Plan).order_by(Plan.sort_order, Plan.id))
    return list(result.scalars().all())


async def get_plan(session: AsyncSession, plan_id: int) -> Optional[Plan]:
    result = await session.execute(select(Plan).where(Plan.id == plan_id))
    return result.scalar_one_or_none()


async def create_plan(
    session: AsyncSession,
    name: str,
    traffic_gb: int,
    duration_days: int,
    price_usdt: float,
    price_toman: int = 0,
    limit_ip: int = 0,
    inbound_ids: str = "",
    sort_order: int = 0,
) -> Plan:
    plan = Plan(
        name=name, traffic_gb=traffic_gb, duration_days=duration_days,
        price_usdt=price_usdt, price_toman=price_toman, limit_ip=limit_ip,
        inbound_ids=inbound_ids, sort_order=sort_order, is_active=True,
    )
    session.add(plan)
    await session.commit()
    await session.refresh(plan)
    return plan


async def update_plan(session: AsyncSession, plan_id: int, **kwargs) -> None:
    from datetime import datetime, timezone
    kwargs["updated_at"] = datetime.now(timezone.utc)
    await session.execute(update(Plan).where(Plan.id == plan_id).values(**kwargs))
    await session.commit()


async def delete_plan(session: AsyncSession, plan_id: int) -> None:
    from sqlalchemy import delete
    await session.execute(delete(Plan).where(Plan.id == plan_id))
    await session.commit()


# ──────────────────────────────────────────────
# DiscountCode CRUD
# ──────────────────────────────────────────────

async def get_discount_code(session: AsyncSession, code: str) -> Optional[DiscountCode]:
    result = await session.execute(
        select(DiscountCode).where(DiscountCode.code == code.upper())
    )
    return result.scalar_one_or_none()


async def get_all_discount_codes(session: AsyncSession) -> List[DiscountCode]:
    result = await session.execute(
        select(DiscountCode).order_by(DiscountCode.created_at.desc())
    )
    return list(result.scalars().all())


async def create_discount_code(
    session: AsyncSession,
    code: str,
    percent: int,
    max_uses: Optional[int] = None,
    expires_at=None,
) -> DiscountCode:
    dc = DiscountCode(
        code=code.upper(), percent=percent,
        max_uses=max_uses, expires_at=expires_at, is_active=True,
    )
    session.add(dc)
    await session.commit()
    await session.refresh(dc)
    return dc


async def use_discount_code(session: AsyncSession, code_id: int) -> None:
    """یک استفاده به used_count اضافه می‌کند."""
    await session.execute(
        update(DiscountCode)
        .where(DiscountCode.id == code_id)
        .values(used_count=DiscountCode.used_count + 1)
    )
    await session.commit()


async def delete_discount_code(session: AsyncSession, code_id: int) -> None:
    from sqlalchemy import delete
    await session.execute(delete(DiscountCode).where(DiscountCode.id == code_id))
    await session.commit()


def validate_discount(dc: DiscountCode) -> tuple[bool, str]:
    """
    بررسی اعتبار کد تخفیف.
    Returns: (valid, message)
    """
    from datetime import datetime, timezone
    if not dc.is_active:
        return False, "این کد تخفیف غیرفعال است."
    if dc.expires_at and dc.expires_at < datetime.now(timezone.utc):
        return False, "این کد تخفیف منقضی شده است."
    if dc.max_uses is not None and dc.used_count >= dc.max_uses:
        return False, "ظرفیت این کد تخفیف تمام شده است."
    return True, ""


# ──────────────────────────────────────────────
# TestSubscriptionRecord CRUD
# ──────────────────────────────────────────────

async def has_used_test_subscription(session: AsyncSession, telegram_id: int) -> bool:
    result = await session.execute(
        select(TestSubscriptionRecord).where(
            TestSubscriptionRecord.telegram_id == telegram_id
        )
    )
    return result.scalar_one_or_none() is not None


async def record_test_subscription(session: AsyncSession, telegram_id: int) -> None:
    rec = TestSubscriptionRecord(telegram_id=telegram_id)
    session.add(rec)
    await session.commit()


# ──────────────────────────────────────────────
# AdminSetting CRUD (key-value store)
# ──────────────────────────────────────────────

async def get_setting(session: AsyncSession, key: str, default: str = "") -> str:
    result = await session.execute(
        select(AdminSetting).where(AdminSetting.key == key)
    )
    row = result.scalar_one_or_none()
    return row.value if row else default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    # upsert: اگر وجود داشت update کن، نداشت insert کن
    existing = await get_setting(session, key, "__NOT_SET__")
    if existing == "__NOT_SET__":
        session.add(AdminSetting(key=key, value=value))
    else:
        await session.execute(
            update(AdminSetting).where(AdminSetting.key == key).values(value=value)
        )
    await session.commit()


# ──────────────────────────────────────────────
# مدیریت اینباندهای فعال برای ساخت کانفیگ
# ──────────────────────────────────────────────

_ENABLED_INBOUNDS_KEY = "enabled_inbound_ids"


async def get_enabled_inbound_ids(session: AsyncSession) -> list[int]:
    """لیست ID اینباندهایی که برای ساخت کانفیگ فعال هستن."""
    raw = await get_setting(session, _ENABLED_INBOUNDS_KEY, "")
    if not raw.strip():
        return []
    try:
        return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
    except Exception:
        return []


async def set_enabled_inbound_ids(session: AsyncSession, ids: list[int]) -> None:
    """ذخیره لیست اینباندهای فعال."""
    value = ",".join(str(i) for i in sorted(set(ids)))
    await set_setting(session, _ENABLED_INBOUNDS_KEY, value)


async def toggle_inbound_enabled(session: AsyncSession, inbound_id: int) -> bool:
    """
    Toggle وضعیت فعال/غیرفعال یک اینباند.
    Returns: True اگه الان فعال شد، False اگه غیرفعال شد
    """
    current = await get_enabled_inbound_ids(session)
    if inbound_id in current:
        current.remove(inbound_id)
        enabled = False
    else:
        current.append(inbound_id)
        enabled = True
    await set_enabled_inbound_ids(session, current)
    return enabled


async def get_next_inbound_id(session: AsyncSession) -> int:
    """
    انتخاب اینباند بعدی به صورت round-robin از اینباندهای فعال.
    اگه هیچ اینباندی فعال نبود، اینباند اول پنل رو برمیگردونه (fallback).
    """
    enabled = await get_enabled_inbound_ids(session)
    if not enabled:
        return 1  # fallback

    # شمارنده round-robin
    counter_raw = await get_setting(session, "inbound_rr_counter", "0")
    counter = int(counter_raw) if counter_raw.isdigit() else 0
    idx = counter % len(enabled)
    chosen = enabled[idx]
    await set_setting(session, "inbound_rr_counter", str(counter + 1))
    return chosen
