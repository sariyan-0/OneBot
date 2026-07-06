"""
config.py — تنظیمات مرکزی ربات
از Pydantic v2 + pydantic-settings برای خواندن .env استفاده می‌شود.
"""

from __future__ import annotations

from typing import List
from urllib.parse import urlparse

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """تمام تنظیمات ربات از فایل .env خوانده می‌شود."""

    model_config = SettingsConfigDict(
        # در Docker: مقادیر از env_file در docker-compose به env vars تبدیل می‌شوند
        # پس env_file اینجا فقط برای توسعه محلی (بدون Docker) استفاده می‌شود
        # برای اعمال تغییرات .env: docker compose up -d  (نه restart)
        env_file=".env",
        env_file_encoding="utf-8",
        env_file_override=False,   # env vars واقعی container > فایل .env محلی
        extra="ignore",
    )

    # ===== ربات تلگرام =====
    bot_token: str = Field(default="", description="توکن ربات از BotFather")

    # ===== رمز ورود ادمین =====
    # کاربر این رمز را به ربات می‌فرستد تا وارد حالت ادمین شود
    # مثال: /admin_secret MySecretPass123
    admin_secret: str = Field(default="", description="رمز مخفی ورود به حالت ادمین")

    # ===== پنل 3X-UI =====
    panel_url: str = Field(..., description="آدرس پنل بدون اسلش آخر (مثلاً https://example.com:54321/webpath)")
    panel_username: str = Field(default="", description="نام کاربری ادمین پنل")
    panel_password: str = Field(default="", description="رمز عبور ادمین پنل")
    panel_api_token: str = Field(default="", description="API Token پنل 3X-UI برای Authorization: Bearer")
    panel_api_path: str = Field(default="/panel/api", description="مسیر پایه API پنل")

    # پورت اختصاصی لینک ساب (اختیاری)
    # اگر خالی باشد، از همان پورت PANEL_URL استفاده می‌شود.
    # مثال: اگر پنل روی 8443 است ولی ساب روی 2096 سرو می‌شود:
    #   PANEL_URL=https://host:8443/webpath
    #   SUB_PORT=2096
    # نتیجه لینک ساب: https://host:2096/sub/xxx
    sub_port: int = Field(default=0, description="پورت اختصاصی لینک ساب (0 = همان پورت پنل)")

    # ===== دیتابیس =====
    db_url: str = Field(
        default="sqlite+aiosqlite:///./bot_data.db",
        description="آدرس اتصال دیتابیس (SQLite یا PostgreSQL)",
    )

    # ===== ادمین‌ها =====
    admin_ids: List[int] = Field(default_factory=list, description="لیست آی‌دی عددی تلگرام ادمین‌ها")

    # ===== اشتراک =====
    default_subscription_days: int = Field(default=30, description="مدت پیش‌فرض اشتراک (روز)")
    default_traffic_gb: int = Field(default=0, description="ترافیک پیش‌فرض اشتراک (GB، صفر = نامحدود)")

    # ===== اشتراک تست =====
    test_subscription_enabled: bool = Field(default=True, description="فعال/غیرفعال بودن اشتراک تست رایگان")
    test_traffic_gb: int = Field(default=1, description="حجم اشتراک تست (GB)")
    test_duration_days: int = Field(default=1, description="مدت اشتراک تست (روز)")

    # ===== پرداخت کریپتو (NOWPayments) =====
    nowpayments_api_key: str = Field(default="", description="کلید API نوپیمنتس (خالی = sandbox)")
    nowpayments_ipn_secret: str = Field(default="", description="کلید مخفی برای تأیید IPN webhook")
    nowpayments_ipn_url: str = Field(default="", description="آدرس webhook برای دریافت تأیید پرداخت")
    nowpayments_pay_currency: str = Field(default="usdttrc20", description="ارز پرداخت (پیش‌فرض USDT TRC-20)")
    invoice_expire_minutes: int = Field(default=30, description="مدت اعتبار invoice (دقیقه)")

    # ===== پرداخت کریپتو (MaxelPay) =====
    maxelpay_api_key: str = Field(default="", description="کلید API MaxelPay (خالی = غیرفعال)")
    # آدرس عمومی webhook برای MaxelPay — باید HTTPS و قابل دسترس از اینترنت باشد
    # مثال: https://your-domain.com:9988/webhook/maxelpay
    maxelpay_webhook_url: str = Field(default="", description="آدرس webhook MaxelPay (HTTPS)")
    # کلید مخفی برای verify امضای webhook MaxelPay (X-MaxelPay-Signature)
    # از داشبورد MaxelPay → API Keys → Webhook Secret بگیرید
    # اختیاری: اگه خالی باشد، امضا verify نمی‌شود (توصیه نمی‌شود در production)
    maxelpay_webhook_secret: str = Field(default="", description="Webhook secret برای verify امضای MaxelPay")
    # نام کاربری ربات برای ساخت لینک بازگشت (اختیاری)
    bot_username: str = Field(default="", description="یوزرنیم ربات (بدون @) برای لینک بازگشت")

    # ===== Webhook Server =====
    # پورت HTTP برای دریافت IPN از NOWPayments
    # پیش‌فرض: 9988 — پورتی که معمولاً با 3X-UI / Nginx / سایر سرویس‌ها تداخل ندارد
    # اگه این پورت هم اشغال بود، هر عدد آزاد دیگه‌ای بذارید (مثلاً 7777 یا 9090)
    # مثال: NOWPAYMENTS_IPN_URL=https://your-domain.com:9988/webhook/nowpayments
    webhook_port: int = Field(default=9988, description="پورت HTTP برای دریافت IPN webhook از NOWPayments")

    # ===== Web Admin Panel =====
    web_admin_enabled: bool = Field(default=True, description="فعال/غیرفعال بودن پنل مدیریت وب")
    web_admin_username: str = Field(default="admin", description="نام کاربری پنل مدیریت وب")
    web_admin_password: str = Field(default="", description="رمز عبور پنل مدیریت وب")
    web_admin_cookie_secret: str = Field(default="", description="کلید امضای کوکی پنل مدیریت وب")

    # ===== قیمت‌گذاری پلن‌ها =====
    plan_price_usdt: float = Field(default=5.0, description="قیمت پیش‌فرض یک ماه اشتراک (USDT)")

    # ===== لاگ =====
    log_level: str = Field(default="INFO", description="سطح لاگ: DEBUG / INFO / WARNING / ERROR")
    log_file: str = Field(default="logs/bot.log", description="مسیر فایل لاگ")

    @field_validator("panel_url", mode="before")
    @classmethod
    def clean_panel_url(cls, v: object) -> str:
        """
        اصلاح خودکار PANEL_URL — trailing slash حذف، /panel/api اضافی برش:

        فرمت صحیح:
          https://host:8443/webBasePath       (webBasePath رندوم مثل ebHlkqXkBbjm2bI260)
          https://host:54321                  (بدون webBasePath)

        اصلاح خودکار اشتباهات رایج:
          https://host:8443/webpath/panel/api  →  https://host:8443/webpath
          https://host:8443/webpath/panel      →  https://host:8443/webpath
          https://host:8443/webpath/           →  https://host:8443/webpath

        توجه: webBasePath (مثل /ebHlkqXkBbjm2bI260) حفظ می‌شود چون بخشی از URL پنل است.
        """
        if not isinstance(v, str):
            return str(v)
        url = v.rstrip("/")
        # اگر کاربر /panel/api را اضافه کرده برش بزن
        if url.endswith("/panel/api"):
            url = url[: -len("/panel/api")]
        # اگر فقط /panel اضافه شده
        elif url.endswith("/panel"):
            url = url[: -len("/panel")]
        return url.rstrip("/")

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v: object) -> List[int]:
        """
        رشته ADMIN_IDS را به لیست int تبدیل می‌کند.
        همه فرمت‌های رایج پشتیبانی می‌شوند:
          5797901827
          5797901827,9876543210
          [5797901827,9876543210]
          ["5797901827","9876543210"]
        """
        if isinstance(v, list):
            return [int(x) for x in v if str(x).strip()]
        if not isinstance(v, str):
            return []
        v = v.strip()
        # فرمت JSON آرایه: [1,2,3]
        if v.startswith("["):
            import json
            try:
                parsed = json.loads(v)
                return [int(x) for x in parsed]
            except Exception:
                v = v.strip("[]")
        # فرمت ساده ویرگول‌جدا: 123,456,789
        return [int(x.strip()) for x in v.split(",") if x.strip().lstrip("-").isdigit()]

    @property
    def panel_base_url(self) -> str:
        """URL کامل API پنل — برای سازگاری با کد قبلی."""
        return f"{self.panel_url}{self.panel_api_path}"

    def panel_origin(self) -> str:
        """
        Origin عمومی پنل بدون path.

        مثال:
          https://host:8443/webpath   -> https://host:8443
          https://host:54321          -> https://host:54321
        """
        parsed = urlparse(self.panel_url.rstrip("/"))
        if not parsed.scheme or not parsed.hostname:
            return self.panel_url.rstrip("/")
        host = parsed.hostname
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"

    def nowpayments_ipn_callback_url(self) -> str:
        """
        URL webhook پیش‌فرض NOWPayments.

        اگر NOWPAYMENTS_IPN_URL خالی باشد، از دامنه/پورت پنل فعلی استفاده می‌کند.
        """
        if self.nowpayments_ipn_url.strip():
            return self.nowpayments_ipn_url.strip()
        return f"{self.panel_origin()}:{self.webhook_port}/webhook/nowpayments"

    def maxelpay_webhook_callback_url(self) -> str:
        """URL webhook پیش‌فرض MaxelPay بر اساس دامنه پنل فعلی."""
        if self.maxelpay_webhook_url.strip():
            return self.maxelpay_webhook_url.strip()
        return f"{self.panel_origin()}:{self.webhook_port}/webhook/maxelpay"

    def is_admin(self, user_id: int) -> bool:
        """بررسی ادمین بودن کاربر."""
        return user_id in self.admin_ids


def get_settings() -> Settings:
    """دریافت تنظیمات — هربار از فایل .env خوانده می‌شود."""
    return Settings()


# نمونه‌ی آماده برای import مستقیم
# این نمونه هنگام startup ربات یک بار ساخته می‌شود
# برای اعمال تغییرات .env: docker compose up -d (نه restart)
settings = get_settings()
