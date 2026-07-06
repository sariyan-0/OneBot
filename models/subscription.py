"""
models/subscription.py — مدل Pydantic اشتراک
جدول واقعی دیتابیس در database/models.py تعریف شده است.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"           # فعال
    EXPIRED = "expired"         # منقضی‌شده
    DEPLETED = "depleted"       # ترافیک تمام‌شده
    DISABLED = "disabled"       # غیرفعال دستی
    PENDING = "pending"         # در انتظار پرداخت


class SubscriptionBase(BaseModel):
    user_id: int = Field(..., description="کلید خارجی به جدول User")
    email: str = Field(..., description="ایمیل منحصر به فرد در پنل 3X-UI")
    client_uuid: str = Field(..., description="UUID کلاینت در پنل")
    sub_id: str = Field(..., description="شناسه subscription برای لینک")
    inbound_id: int = Field(..., description="شناسه inbound در پنل")
    traffic_limit_gb: int = Field(default=0, description="محدودیت ترافیک GB (0=نامحدود)")
    expiry_date: Optional[datetime] = Field(None, description="تاریخ انقضا")
    status: SubscriptionStatus = Field(default=SubscriptionStatus.ACTIVE)


class SubscriptionCreate(SubscriptionBase):
    """داده مورد نیاز برای ذخیره اشتراک جدید."""
    pass


class SubscriptionRead(SubscriptionBase):
    """مدل برگشتی از دیتابیس با فیلدهای اضافه."""
    id: int
    used_traffic_bytes: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @property
    def used_traffic_gb(self) -> float:
        """ترافیک مصرف‌شده به گیگابایت."""
        return round(self.used_traffic_bytes / 1024 ** 3, 2)

    @property
    def remaining_traffic_gb(self) -> Optional[float]:
        """ترافیک باقی‌مانده (None اگر نامحدود)."""
        if self.traffic_limit_gb == 0:
            return None
        return max(0.0, self.traffic_limit_gb - self.used_traffic_gb)

    @property
    def is_expired(self) -> bool:
        """بررسی انقضا."""
        if self.expiry_date is None:
            return False
        from datetime import timezone
        return datetime.now(timezone.utc) > self.expiry_date


class SubscriptionUpdate(BaseModel):
    """داده‌های قابل به‌روزرسانی اشتراک."""
    status: Optional[SubscriptionStatus] = None
    used_traffic_bytes: Optional[int] = None
    expiry_date: Optional[datetime] = None
    traffic_limit_gb: Optional[int] = None
