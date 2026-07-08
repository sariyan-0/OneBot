"""
services/subscription.py — منطق کسب‌وکار ایجاد اشتراک
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.crud import create_subscription, get_plan, get_user_subscriptions
from database.models import Plan, Subscription, User
from services.xui_api import XUIClient, XUIError
from utils.qrcode_gen import generate_qr_code


# ──────────────────────────────────────────────
# نتیجه ایجاد اشتراک
# ──────────────────────────────────────────────

@dataclass
class NewSubscriptionResult:
    subscription: Subscription       # رکورد ذخیره‌شده در دیتابیس
    sub_link: str                    # لینک subscription (برای import همه کانفیگ‌ها)
    qr_bytes: bytes                  # QR Code از sub_link
    client_uuid: str                 # UUID کلاینت در پنل
    email: str                       # ایمیل منحصر به فرد در پنل
    config_links: list[str]          # لیست کانفیگ‌های کامل (vless://... vmess://...)
    limit_ip: int = 0                # محدودیت IP همزمان (0 = نامحدود)


# ──────────────────────────────────────────────
# تولید ایمیل منحصر به فرد
# ──────────────────────────────────────────────

_COUNTER_KEY_CLIENT = "sub_counter_client"
_COUNTER_KEY_GIFT   = "sub_counter_gift"


async def _next_email(session: AsyncSession, is_gift: bool) -> str:
    """
    شمارنده اتمی از DB می‌خواند و email منحصربه‌فرد برمی‌گرداند.
    خرید عادی  → client-1, client-2, ...
    اشتراک تست → Gift-1, Gift-2, ...
    """
    from database.crud import get_setting, set_setting
    key = _COUNTER_KEY_GIFT if is_gift else _COUNTER_KEY_CLIENT
    raw = await get_setting(session, key, "0")
    n = int(raw) + 1
    await set_setting(session, key, str(n))
    prefix = "Gift" if is_gift else "client"
    return f"{prefix}-{n}"


async def _sync_counter_with_panel(session: AsyncSession, xui: "XUIClient") -> None:
    """
    شمارنده‌های DB را با وضعیت واقعی پنل همگام می‌کند.
    اگر در پنل client-50 وجود داشت، counter_client را به ≥50 می‌برد.
    این تابع یک‌بار در ابتدای ایجاد اشتراک صدا زده می‌شود.
    """
    from database.crud import get_setting, set_setting
    try:
        all_clients = await xui.get_all_clients()
        max_client = 0
        max_gift = 0
        for c in all_clients:
            email = c.email.strip()
            if email.startswith("client-"):
                try:
                    n = int(email[len("client-"):])
                    max_client = max(max_client, n)
                except ValueError:
                    pass
            elif email.lower().startswith("gift-"):
                try:
                    n = int(email[5:])
                    max_gift = max(max_gift, n)
                except ValueError:
                    pass

        # فقط اگر پنل شماره بالاتری داشت counter را به‌روز کن
        if max_client > 0:
            current = int(await get_setting(session, _COUNTER_KEY_CLIENT, "0"))
            if max_client >= current:
                await set_setting(session, _COUNTER_KEY_CLIENT, str(max_client))
                logger.info(f"counter_client همگام شد با پنل: {max_client}")

        if max_gift > 0:
            current_g = int(await get_setting(session, _COUNTER_KEY_GIFT, "0"))
            if max_gift >= current_g:
                await set_setting(session, _COUNTER_KEY_GIFT, str(max_gift))
                logger.info(f"counter_gift همگام شد با پنل: {max_gift}")
    except Exception as e:
        logger.warning(f"همگام‌سازی counter با پنل ناموفق (ادامه می‌دهیم): {e}")


def _expiry_from_plan(plan_days: int, base: datetime | None = None) -> Optional[datetime]:
    if plan_days <= 0:
        return None
    base_dt = _ensure_aware_dt(base or datetime.now(timezone.utc))
    return base_dt + timedelta(days=plan_days)


def _ensure_aware_dt(value: datetime | None) -> datetime:
    """Normalize SQLite datetimes so aware/naive comparisons never fail."""
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _resolve_usable_inbound_ids(
    xui: XUIClient,
    requested_inbound_ids: list[int],
) -> list[int]:
    """
    Resolve inbound ids conservatively.

    If the panel exposes inbound listing endpoints, keep only enabled inbounds.
    If this panel build does not expose those endpoints, fall back to the
    requested ids directly so purchase flows can still proceed.
    """
    requested = [int(i) for i in requested_inbound_ids if int(i) > 0]

    try:
        all_inbounds = await xui.get_inbounds()
    except XUIError as exc:
        logger.warning(
            f"دریافت لیست اینباندهای پنل ناموفق بود؛ از اینباندهای تنظیم‌شده استفاده می‌کنیم: {exc}"
        )
        return requested or [1]

    enabled_inbound_ids = [ib.id for ib in all_inbounds if ib.enable]
    if not enabled_inbound_ids:
        logger.warning("هیچ اینباند فعالی از API پنل برنگشت؛ از اینباندهای تنظیم‌شده استفاده می‌کنیم.")
        return requested or [1]

    if not requested:
        logger.info(
            f"هیچ اینباند اختصاصی برای پلن انتخاب نشده؛ همه اینباندهای فعال مجازند: {enabled_inbound_ids}"
        )
        return enabled_inbound_ids

    valid_target_ids = [iid for iid in requested if iid in enabled_inbound_ids]
    if valid_target_ids:
        return valid_target_ids

    logger.warning(
        f"اینباندهای درخواستی {requested} در لیست فعال پنل نبودند؛ از اولین اینباند فعال {enabled_inbound_ids[0]} استفاده می‌کنیم."
    )
    return [enabled_inbound_ids[0]]


async def _create_subscription_on_panel(
    session: AsyncSession,
    xui: XUIClient,
    user_id: int,
    telegram_id: int,
    plan: Plan | None,
    traffic_gb: int,
    expire_days: int,
    is_gift: bool,
    email: str | None = None,
    sub_id: str | None = None,
) -> tuple[Subscription, str, str, list[str], int]:
    """
    ایجاد/بازسازی یک اشتراک روی پنل با امکان حفظ email و sub_id.
    خروجی: subscription, sub_link, email, config_links, first_inbound_id
    """
    from database.crud import get_enabled_inbound_ids

    if plan:
        target_inbound_ids = plan.get_inbound_ids()
    else:
        enabled_ids = await get_enabled_inbound_ids(session)
        target_inbound_ids = enabled_ids or [1]

    if email is None:
        email = await _next_email(session, is_gift=is_gift)
    if sub_id is None:
        sub_id = uuid.uuid4().hex[:16]

    target_inbound_ids = await _resolve_usable_inbound_ids(xui, target_inbound_ids)

    first_inbound_id = target_inbound_ids[0]
    client_info = None
    MAX_RETRY = 10
    for attempt in range(MAX_RETRY):
        try:
            client_info = await xui.add_client(
                inbound_id=first_inbound_id,
                email=email,
                traffic_gb=traffic_gb,
                expire_days=expire_days,
                sub_id=sub_id,
                tg_id=telegram_id,
                limit_ip=plan.limit_ip if plan else 0,
            )
            break
        except XUIError as e:
            if "already in use" in str(e).lower() and attempt < MAX_RETRY - 1:
                email = await _next_email(session, is_gift=is_gift)
                continue
            raise
    if client_info is None:
        raise XUIError(f"پس از {MAX_RETRY} تلاش نتوانستیم email آزادی پیدا کنیم.")

    for extra_iid in target_inbound_ids[1:]:
        try:
            ib = await xui.get_inbound(extra_iid)
            if not ib.enable:
                continue
            await xui.add_client(
                inbound_id=extra_iid,
                email=email,
                traffic_gb=traffic_gb,
                expire_days=expire_days,
                tg_id=telegram_id,
                sub_id=sub_id,
                limit_ip=plan.limit_ip if plan else 0,
            )
        except Exception as e:
            logger.warning(f"اضافه کردن به اینباند {extra_iid} ناموفق: {e}")

    sub_link = xui.build_sub_link(sub_id)
    config_links = await xui.get_client_links(email)
    if not config_links:
        config_links = await xui.get_sub_links(sub_id)

    expiry_date = _expiry_from_plan(expire_days)
    db_sub = await create_subscription(
        session=session,
        user_id=user_id,
        email=email,
        client_uuid=client_info.uuid or client_info.sub_id,
        sub_id=sub_id,
        plan_id=plan.id if plan else None,
        inbound_id=first_inbound_id,
        traffic_limit_gb=traffic_gb,
        expiry_date=expiry_date,
        limit_ip=plan.limit_ip if plan else 0,
    )
    return db_sub, sub_link, email, config_links, first_inbound_id


# ──────────────────────────────────────────────
# سرویس اصلی ایجاد اشتراک
# ──────────────────────────────────────────────

async def create_new_subscription(
    session: AsyncSession,
    user_id: int,
    telegram_id: int,
    inbound_id: int,
    traffic_gb: int = 0,
    expire_days: int = 0,
    is_gift: bool = False,
    plan_id: int = 0,
) -> NewSubscriptionResult:
    """
    ایجاد اشتراک جدید — flow کامل:
      1. دریافت inbound از پنل
      2. تولید email منحصر به فرد
      3. ایجاد client در پنل از طریق XUIClient
      4. ذخیره در دیتابیس
      5. تولید subscription link + QR Code

    Args:
        session: AsyncSession دیتابیس
        user_id: کلید اولیه User در دیتابیس
        telegram_id: آی‌دی تلگرام (برای ایمیل)
        inbound_id: شناسه inbound در پنل (0 = انتخاب خودکار)
        traffic_gb: محدودیت ترافیک (0=نامحدود)
        expire_days: مدت اعتبار روز (0=پیش‌فرض از config)
        plan_id: شناسه پلن در دیتابیس (اگر داده شود اینباندهای اختصاصی پلن اولویت دارند)

    Returns:
        NewSubscriptionResult

    Raises:
        XUIError: در صورت خطا از پنل
    """
    # استفاده از مقادیر پیش‌فرض config در صورت نبودن
    if traffic_gb == 0:
        traffic_gb = settings.default_traffic_gb
    if expire_days == 0:
        expire_days = settings.default_subscription_days
    plan_obj = await get_plan(session, plan_id) if plan_id else None
    if plan_obj:
        logger.info(f"اینباندهای اختصاصی پلن {plan_id}: {plan_obj.get_inbound_ids()}")

    # ── ایجاد client در پنل — در همه اینباندهای فعال ──
    async with XUIClient(
        panel_url=settings.panel_url,
        username=settings.panel_username,
        password=settings.panel_password,
        api_path=settings.panel_api_path,
        sub_port=settings.sub_port,
    ) as xui:
        # همگام‌سازی شمارنده با پنل (از تکراری بودن email جلوگیری می‌کند)
        await _sync_counter_with_panel(session, xui)
        db_sub, sub_link, email, config_links, _ = await _create_subscription_on_panel(
            session=session,
            xui=xui,
            user_id=user_id,
            telegram_id=telegram_id,
            plan=plan_obj,
            traffic_gb=traffic_gb,
            expire_days=expire_days,
            is_gift=is_gift,
        )

    # ── تولید QR Code ────────────────────────
    qr_bytes = await generate_qr_code(sub_link)
    logger.success(f"اشتراک ایجاد شد: email={email}, link={sub_link}")

    return NewSubscriptionResult(
        subscription=db_sub,
        sub_link=sub_link,
        qr_bytes=qr_bytes,
        client_uuid=db_sub.client_uuid,
        email=email,
        config_links=config_links,
        limit_ip=plan_obj.limit_ip if plan_obj else 0,
    )


async def rotate_subscription_link(
    session: AsyncSession,
    subscription_id: int,
) -> NewSubscriptionResult:
    """
    بازسازی هویت یک اشتراک:
      - subId جدید
      - UUID تازه از پنل
      - همان email و همان محدودیت‌ها

    این کار برای زمانی است که کاربر/ادمین بخواهد لینک ساب را عوض کند
    بدون تغییر پلن یا تاریخ انقضا.
    """
    sub = await session.get(Subscription, subscription_id)
    if not sub:
        raise XUIError("اشتراک پیدا نشد.")

    user = await session.get(User, sub.user_id) if getattr(sub, "user_id", None) else None
    telegram_id = getattr(user, "telegram_id", 0) if user else 0

    now = datetime.now(timezone.utc)
    expiry_dt = _ensure_aware_dt(sub.expiry_date) if sub.expiry_date else None
    expiry_time_ms = int(expiry_dt.timestamp() * 1000) if expiry_dt and expiry_dt > now else 0

    async with XUIClient(
        panel_url=settings.panel_url,
        username=settings.panel_username,
        password=settings.panel_password,
        api_path=settings.panel_api_path,
        sub_port=settings.sub_port,
    ) as xui:
        current_client = await xui.get_client(sub.email)
        if not current_client:
            raise XUIError("کلاینت فعلی در پنل پیدا نشد.")

        target_inbound_ids = list(current_client.inbound_ids or [])
        if not target_inbound_ids:
            target_inbound_ids = [sub.inbound_id]

        target_inbound_ids = await _resolve_usable_inbound_ids(xui, target_inbound_ids)

        new_sub_id = uuid.uuid4().hex[:16]
        try:
            await xui.delete_client(sub.email, keep_traffic=True)
        except Exception as exc:
            logger.warning(f"حذف کلاینت قبلی هنگام تغییر لینک ناموفق بود: {exc}")

        first_inbound_id = target_inbound_ids[0]
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                await xui.add_client(
                    inbound_id=first_inbound_id,
                    email=sub.email,
                    traffic_gb=sub.traffic_limit_gb,
                    expiry_time_ms=expiry_time_ms,
                    sub_id=new_sub_id,
                    tg_id=telegram_id,
                    limit_ip=sub.limit_ip,
                )
                last_exc = None
                break
            except XUIError as exc:
                last_exc = exc
                if attempt < 2 and any(needle in str(exc).lower() for needle in ("already in use", "duplicate", "found")):
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        for extra_iid in target_inbound_ids[1:]:
            try:
                await xui.add_client(
                    inbound_id=extra_iid,
                    email=sub.email,
                    traffic_gb=sub.traffic_limit_gb,
                    expiry_time_ms=expiry_time_ms,
                    sub_id=new_sub_id,
                    tg_id=telegram_id,
                    limit_ip=sub.limit_ip,
                )
            except Exception as exc:
                logger.warning(f"اضافه کردن کلاینت به inbound {extra_iid} هنگام تغییر لینک ناموفق بود: {exc}")

        refreshed = await xui.get_client(sub.email)
        if not refreshed:
            raise XUIError("پس از بازسازی، کلاینت جدید از پنل برنگشت.")

        sub.sub_id = refreshed.sub_id or new_sub_id
        sub.client_uuid = refreshed.uuid or refreshed.sub_id or sub.client_uuid
        sub.inbound_id = first_inbound_id
        sub.updated_at = datetime.now(timezone.utc)
        await session.commit()

        sub_link = xui.build_sub_link(sub.sub_id)
        config_links = await xui.get_client_links(sub.email)
        if not config_links:
            config_links = await xui.get_sub_links(sub.sub_id)

    qr_bytes = await generate_qr_code(sub_link)
    logger.success(f"لینک اشتراک {subscription_id} بازسازی شد: sub={sub.sub_id}")
    return NewSubscriptionResult(
        subscription=sub,
        sub_link=sub_link,
        qr_bytes=qr_bytes,
        client_uuid=sub.client_uuid,
        email=sub.email,
        config_links=config_links,
        limit_ip=sub.limit_ip,
    )


async def apply_paid_plan_to_subscription(
    session: AsyncSession,
    subscription_id: int,
    plan_id: int,
    telegram_id: int,
    action: str = "renew",
) -> NewSubscriptionResult:
    """
    Renew or change an existing subscription after payment.
    action:
      - renew: keep the same subscription identity and extend it
      - change: rebuild the panel client on the new plan
    """
    plan = await get_plan(session, plan_id)
    if not plan:
        raise XUIError("پلن پیدا نشد.")

    sub = await session.get(Subscription, subscription_id)
    if not sub:
        raise XUIError("اشتراک پیدا نشد.")

    now = datetime.now(timezone.utc)
    base_expiry = _ensure_aware_dt(sub.expiry_date) if sub.expiry_date and _ensure_aware_dt(sub.expiry_date) > now else now
    new_expiry = _expiry_from_plan(plan.duration_days, base_expiry)
    target_traffic = plan.traffic_gb
    target_limit_ip = plan.limit_ip or 0

    async with XUIClient(
        panel_url=settings.panel_url,
        username=settings.panel_username,
        password=settings.panel_password,
        api_path=settings.panel_api_path,
        sub_port=settings.sub_port,
    ) as xui:
        if action == "change":
            try:
                await xui.delete_client(sub.email)
            except Exception as exc:
                logger.warning(f"حذف کلاینت قبلی هنگام تغییر پلن ناموفق بود: {exc}")
            target_inbound_ids = plan.get_inbound_ids()
            target_inbound_ids = await _resolve_usable_inbound_ids(xui, target_inbound_ids)
            first_inbound_id = target_inbound_ids[0]
            await xui.add_client(
                inbound_id=first_inbound_id,
                email=sub.email,
                traffic_gb=target_traffic,
                expire_days=plan.duration_days,
                sub_id=sub.sub_id,
                tg_id=telegram_id,
                limit_ip=target_limit_ip,
            )
            for extra_iid in target_inbound_ids[1:]:
                try:
                    await xui.add_client(
                        inbound_id=extra_iid,
                        email=sub.email,
                        traffic_gb=target_traffic,
                        expire_days=plan.duration_days,
                        tg_id=telegram_id,
                        sub_id=sub.sub_id,
                        limit_ip=target_limit_ip,
                    )
                except Exception as exc:
                    logger.warning(f"اضافه کردن کلاینت به inbound {extra_iid} ناموفق: {exc}")
            sub_link = xui.build_sub_link(sub.sub_id)
            email = sub.email
            config_links = await xui.get_client_links(email)
            if not config_links:
                config_links = await xui.get_sub_links(sub.sub_id)
        else:
            await xui.update_client(
                email=sub.email,
                traffic_gb=target_traffic,
                expire_days=plan.duration_days,
                enable=True,
                tg_id=telegram_id,
                limit_ip=target_limit_ip,
            )
            sub_link = xui.build_sub_link(sub.sub_id)
            email = sub.email
            config_links = await xui.get_client_links(email)
            if not config_links:
                config_links = await xui.get_sub_links(sub.sub_id)
            try:
                all_inbounds = await xui.get_inbounds()
                first_inbound_id = next((ib.id for ib in all_inbounds if ib.enable), sub.inbound_id)
            except XUIError:
                first_inbound_id = sub.inbound_id

    qr_bytes = await generate_qr_code(sub_link)
    await session.execute(
        update(Subscription)
        .where(Subscription.id == subscription_id)
        .values(
            plan_id=plan.id,
            inbound_id=first_inbound_id,
            traffic_limit_gb=target_traffic,
            used_traffic_bytes=0,
            expiry_date=new_expiry,
            limit_ip=target_limit_ip,
            status="active",
            updated_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
    sub.plan_id = plan.id
    sub.inbound_id = first_inbound_id
    sub.traffic_limit_gb = target_traffic
    sub.used_traffic_bytes = 0
    sub.expiry_date = new_expiry
    sub.limit_ip = target_limit_ip
    sub.status = "active"

    logger.success(f"اشتراک {subscription_id} با پلن {plan.id} به‌روزرسانی شد ({action}).")
    return NewSubscriptionResult(
        subscription=sub,
        sub_link=sub_link,
        qr_bytes=qr_bytes,
        client_uuid=sub.client_uuid,
        email=email,
        config_links=config_links,
        limit_ip=target_limit_ip,
    )


async def get_subscriptions_status(
    session: AsyncSession,
    user_id: int,
) -> list[Subscription]:
    """دریافت لیست اشتراک‌های فعال کاربر."""
    return await get_user_subscriptions(session, user_id, active_only=True)


def _is_missing_panel_client_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        needle in msg
        for needle in (
            "found",
            "not found",
            "client not found",
            "already removed",
            "no such client",
            "deleted",
        )
    )


async def delete_subscription_completely(
    session: AsyncSession,
    sub: Subscription,
) -> None:
    """
    حذف کامل اشتراک:
      1. حذف کلاینت از پنل 3X-UI
      2. حذف رکورد از دیتابیس ربات

    اگر کلاینت قبلاً از پنل حذف شده باشد، حذف دیتابیس ادامه پیدا می‌کند.
    """
    try:
        async with XUIClient(
            panel_url=settings.panel_url,
            username=settings.panel_username,
            password=settings.panel_password,
            api_path=settings.panel_api_path,
            sub_port=settings.sub_port,
        ) as xui:
            await xui.delete_client(sub.email)
    except Exception as exc:
        logger.warning(
            f"حذف کلاینت '{sub.email}' از پنل ناموفق بود، اما رکورد ربات حذف می‌شود: {exc}"
        )

    await session.delete(sub)
    await session.commit()
