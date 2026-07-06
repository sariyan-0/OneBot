"""
models/user.py — مدل Pydantic کاربر (برای انتقال داده بین لایه‌ها)
جدول واقعی دیتابیس در database/models.py تعریف شده است.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UserBase(BaseModel):
    telegram_id: int = Field(..., description="آی‌دی عددی تلگرام کاربر")
    username: Optional[str] = Field(None, description="نام کاربری تلگرام (بدون @)")
    first_name: Optional[str] = Field(None, description="نام کاربر در تلگرام")
    is_admin: bool = Field(default=False, description="آیا کاربر ادمین است؟")


class UserCreate(UserBase):
    """داده مورد نیاز برای ایجاد کاربر جدید."""
    pass


class UserRead(UserBase):
    """مدل برگشتی از دیتابیس."""
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    """داده‌های قابل به‌روزرسانی کاربر."""
    username: Optional[str] = None
    first_name: Optional[str] = None
    is_admin: Optional[bool] = None
