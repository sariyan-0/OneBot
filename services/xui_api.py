"""
services/xui_api.py — کلاینت کامل API پنل 3X-UI
هماهنگ‌شده با Postman Collection رسمی سنایی (3X-UI Panel API)

تغییرات مهم نسبت به نسخه قدیمی:
  • add_client     → POST /panel/api/clients/add  (با inboundIds)
  • update_client  → POST /panel/api/clients/update/:email
  • delete_client  → POST /panel/api/clients/del/:email
  • get_client_traffic → GET /panel/api/clients/traffic/:email
  • reset_traffic  → POST /panel/api/clients/resetTraffic/:email
  • get_sub_links  → GET /panel/api/clients/subLinks/:subId
  • get_client_links → GET /panel/api/clients/links/:email
  • get_xray_logs  → POST /panel/api/server/logs/:count  (با body JSON)
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


# ──────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────

class XUIError(Exception):
    """خطای پایه برای API پنل."""


class XUIAuthError(XUIError):
    """خطای احراز هویت."""


class XUIConnectionError(XUIError):
    """خطای اتصال به پنل."""


class XUINotFoundError(XUIError):
    """منبع درخواست‌شده پیدا نشد."""


# ──────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────

@dataclass
class InboundInfo:
    id: int
    remark: str
    protocol: str
    port: int
    enable: bool
    up: int = 0           # بایت آپلود کل inbound
    down: int = 0         # بایت دانلود کل inbound
    total: int = 0        # محدودیت ترافیک (0 = نامحدود)
    expiry_time: int = 0  # timestamp میلی‌ثانیه (0 = نامحدود)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClientInfo:
    """اطلاعات یک کلاینت از API جدید (clients endpoint)."""
    email: str
    sub_id: str
    inbound_ids: List[int] = field(default_factory=list)
    enable: bool = True
    total_gb: int = 0         # bytes — از totalGB پنل
    expiry_time: int = 0      # timestamp ms
    up: int = 0
    down: int = 0
    limit_ip: int = 0
    # فیلد uuid در API جدید فقط در /clients/list نشان می‌دهد
    uuid: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        """سازگاری با کد قدیمی که client.id می‌خواند."""
        return self.uuid

    @property
    def inbound_id(self) -> int:
        """اولین inbound_id برای سازگاری با کد قدیمی."""
        return self.inbound_ids[0] if self.inbound_ids else 0


# ──────────────────────────────────────────────
# Standalone helper — قابل import از هر جا
# ──────────────────────────────────────────────

def build_sub_link_for(panel_url: str, sub_id: str, sub_port: int = 0) -> str:
    """
    ساخت لینک subscription از panel_url و sub_id.
    webBasePath و هر path اضافی حذف می‌شود — فقط scheme+host باقی می‌ماند.

    پارامترها:
      panel_url: آدرس کامل پنل (مثل https://host:8443/webpath)
      sub_id:    شناسه subscription کاربر
      sub_port:  پورت اختصاصی ساب (0 = از همان پورت panel_url استفاده کن)

    مثال‌ها:
      panel_url="https://host:8443/webpath", sub_port=0
        → "https://host:8443/sub/abc123"

      panel_url="https://host:8443/webpath", sub_port=2096
        → "https://host:2096/sub/abc123"

    این تابع standalone است و نیازی به نمونه XUIClient ندارد.
    """
    from urllib.parse import urlparse
    parsed = urlparse(panel_url.rstrip("/"))
    host = parsed.hostname or ""
    scheme = parsed.scheme or "https"
    # پورت: اگر sub_port داده شده از آن استفاده کن، وگرنه پورت panel_url
    if sub_port and sub_port > 0:
        port = sub_port
    else:
        port = parsed.port or (443 if scheme == "https" else 80)
    origin = f"{scheme}://{host}:{port}"
    return f"{origin}/sub/{sub_id}"


# ──────────────────────────────────────────────
# XUIClient
# ──────────────────────────────────────────────

class XUIClient:
    """
    کلاینت Async برای API پنل 3X-UI (نسخه سنایی).

    احراز هویت: cookie-based (POST /login)
    مسیر API:   /panel/api/*

    نمونه استفاده:
        async with XUIClient(panel_url, username, password) as client:
            inbounds = await client.get_inbounds()
    """

    def __init__(
        self,
        panel_url: str,
        username: str,
        password: str,
        api_token: Optional[str] = None,
        api_path: str = "/panel/api",
        timeout: float = 15.0,
        max_retries: int = 3,
        sub_port: int = 0,
    ) -> None:
        # _base همیشه بدون trailing slash ذخیره می‌شود
        # مثال: https://host:8443/ebHlkqXkBbjm2bI260
        self._base = panel_url.rstrip("/")
        # _sub_base = برای لینک ساب — بدون webBasePath، با sub_port اگر تنظیم شده
        # مثال (sub_port=0):    https://host:8443
        # مثال (sub_port=2096): https://host:2096
        from urllib.parse import urlparse
        _parsed = urlparse(self._base)
        _host = _parsed.hostname or ""
        _scheme = _parsed.scheme or "https"
        _port = sub_port if sub_port > 0 else (_parsed.port or (443 if _scheme == "https" else 80))
        self._sub_base = f"{_scheme}://{_host}:{_port}"
        # _api بدون trailing slash: /panel/api
        self._api = api_path.rstrip("/")
        self._username = username
        self._password = password
        if api_token is None:
            try:
                from config import settings as _settings
                api_token = getattr(_settings, "panel_api_token", "")
            except Exception:
                api_token = ""
        self._api_token = (api_token or "").strip()
        self._timeout = timeout
        self._max_retries = max_retries

        self._session: Optional[httpx.AsyncClient] = None
        self._cookies: Dict[str, str] = {}
        self._logged_in: bool = bool(self._api_token)
        self._csrf_token: str = ""   # CSRF token دریافت‌شده بعد از login

    @staticmethod
    def _extract_origin(url: str) -> str:
        """
        از URL کامل پنل، فقط scheme+host+port را برمی‌گرداند.
        webBasePath (مثل /ebHlkqXkBbjm2bI260) حذف می‌شود چون
        لینک subscription کاربر نباید شامل آن باشد.

        مثال‌ها:
          https://host:8443/ebHlkqXkBbjm2bI260  →  https://host:8443
          https://host:54321                     →  https://host:54321
          http://1.2.3.4:2096/mypath             →  http://1.2.3.4:2096
        """
        from urllib.parse import urlparse
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return origin

    # ── context manager ──────────────────────

    async def __aenter__(self) -> "XUIClient":
        await self._init_session()
        if self._api_token:
            logger.info(f"ورود به پنل {self._base}/ با API Token (Bearer)")
        else:
            await self.login()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ── session management ───────────────────

    async def _init_session(self) -> None:
        """
        ساخت httpx client با SSL verify=False برای پنل‌های self-signed.
        cookie_jar خودکار همه cookie‌ها را با path درست نگه می‌دارد
        تا 403 Forbidden برای مسیرهای زیرشاخه رخ ندهد.
        """
        self._session = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            verify=False,
        )

    async def close(self) -> None:
        """بستن session."""
        if self._session:
            await self._session.aclose()
            self._session = None
        self._logged_in = False

    # ── internal request helper ───────────────

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        ارسال درخواست به /panel/api/{path} با retry خودکار.
        در صورت 401، یک بار دوباره login می‌کند.
        """
        if not self._session:
            await self._init_session()

        # URL نهایی: base + /panel/api + /path
        # مثال: https://host:8443/webpath/panel/api/clients/add
        url = f"{self._base}{self._api}{path}"

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type((httpx.TransportError, XUIConnectionError)),
            reraise=True,
        ):
            with attempt:
                try:
                    # CSRF token رو به headers اضافه کن (پنل‌های 3X-UI جدید نیاز دارند)
                    req_kwargs = dict(kwargs)
                    existing_headers = dict(req_kwargs.get("headers", {}))
                    existing_headers.setdefault("Accept", "application/json")
                    if self._api_token:
                        existing_headers.setdefault("Authorization", f"Bearer {self._api_token}")
                    req_kwargs["headers"] = existing_headers
                    if self._csrf_token:
                        existing_headers.setdefault("X-CSRF-Token", self._csrf_token)
                    resp = await self._session.request(method, url, **req_kwargs)
                except httpx.TransportError as exc:
                    raise XUIConnectionError(f"خطای اتصال: {exc}") from exc

                if resp.status_code == 401:
                    if self._api_token:
                        raise XUIAuthError("API Token پنل نامعتبر است یا دسترسی ندارد.")
                    logger.warning("Session منقضی — دوباره login...")
                    self._logged_in = False
                    await self.login()
                    req_kwargs2 = dict(kwargs)
                    h2 = dict(req_kwargs2.get("headers", {}))
                    h2.setdefault("Accept", "application/json")
                    if self._csrf_token:
                        h2.setdefault("X-CSRF-Token", self._csrf_token)
                    req_kwargs2["headers"] = h2
                    resp = await self._session.request(method, url, **req_kwargs2)

                if resp.status_code == 404:
                    raise XUINotFoundError(f"مسیر {url} پیدا نشد")

                resp.raise_for_status()
                data: Dict[str, Any] = resp.json()

                if not data.get("success", False):
                    msg = data.get("msg", "خطای ناشناخته از پنل")
                    raise XUIError(f"پنل خطا برگرداند: {msg}")

                return data

        raise XUIConnectionError("تمام تلاش‌های اتصال ناموفق بود")

    # ── authentication ───────────────────────

    async def login(self) -> None:
        """POST /login — دریافت session cookie."""
        if self._api_token:
            self._logged_in = True
            return
        if self._logged_in:
            return
        if not self._session:
            await self._init_session()

        # ── Step 1: دریافت CSRF token ────────────────────────────────
        # پنل سنایی یک CSRF token در صفحه HTML login embed می‌کند.
        # باید اول GET بزنیم، token را از meta tag بگیریم،
        # بعد با آن token در header، POST login بفرستیم.
        #
        # فرمت HTML: <meta name="csrf-token" content="TOKEN_VALUE">
        # endpoint: POST {base}/login  (نه base/ — بلکه base/login)
        base_url = f"{self._base}/"
        login_url = f"{self._base}/login"
        logger.info(f"ورود به پنل {self._base}/ ...")
        try:
            # Step 1: GET صفحه اصلی — session cookie اولیه + CSRF token
            # httpx session به طور خودکار Set-Cookie را در cookie jar ذخیره می‌کند
            get_resp = await self._session.get(base_url)
            csrf_token = ""
            match = re.search(
                r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']',
                get_resp.text,
            )
            if match:
                csrf_token = match.group(1)
                logger.debug(f"CSRF token دریافت شد: {csrf_token[:20]}...")
            else:
                logger.warning("CSRF token در صفحه پیدا نشد — ادامه بدون آن")

            # Step 2: POST login — session cookie jar خودکار cookie‌ها را می‌فرستد
            headers = {"Content-Type": "application/json"}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            resp = await self._session.post(
                login_url,
                json={"username": self._username, "password": self._password},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                raise XUIAuthError(f"ورود ناموفق: {data.get('msg')}")
            # session cookie jar به صورت خودکار آپدیت شد
            self._logged_in = True
            # CSRF token رو ذخیره کن تا در همه درخواست‌های بعدی استفاده بشه
            self._csrf_token = csrf_token
            logger.success("ورود به پنل موفق ✓")
        except httpx.HTTPStatusError as exc:
            raise XUIAuthError(f"خطای HTTP در ورود: {exc}") from exc
        except httpx.TransportError as exc:
            raise XUIConnectionError(f"اتصال ممکن نیست: {exc}") from exc

    # ── Inbounds ─────────────────────────────

    async def get_inbounds(self) -> List[InboundInfo]:
        """GET /inbounds/list — لیست کامل inbound‌ها."""
        data = await self._request("GET", "/inbounds/list")
        result: List[InboundInfo] = []
        for item in data.get("obj", []):
            result.append(
                InboundInfo(
                    id=item["id"],
                    remark=item.get("remark", ""),
                    protocol=item.get("protocol", ""),
                    port=item.get("port", 0),
                    enable=item.get("enable", True),
                    up=item.get("up", 0),
                    down=item.get("down", 0),
                    total=item.get("total", 0),
                    expiry_time=item.get("expiryTime", 0),
                    raw=item,
                )
            )
        return result

    async def get_inbound(self, inbound_id: int) -> InboundInfo:
        """GET /inbounds/get/:id — یک inbound مشخص."""
        data = await self._request("GET", f"/inbounds/get/{inbound_id}")
        item = data["obj"]
        return InboundInfo(
            id=item["id"],
            remark=item.get("remark", ""),
            protocol=item.get("protocol", ""),
            port=item.get("port", 0),
            enable=item.get("enable", True),
            up=item.get("up", 0),
            down=item.get("down", 0),
            total=item.get("total", 0),
            expiry_time=item.get("expiryTime", 0),
            raw=item,
        )

    # ── Clients (API جدید) ────────────────────

    async def add_client(
        self,
        inbound_id: int,
        email: str,
        traffic_gb: int = 0,
        expire_days: int = 30,
        expiry_time_ms: Optional[int] = None,
        sub_id: Optional[str] = None,
        limit_ip: int = 0,
        tg_id: int = 0,
    ) -> ClientInfo:
        """
        POST /panel/api/clients/add
        ساختار جدید: {"client": {...}, "inboundIds": [...]}

        Args:
            inbound_id: شناسه inbound در پنل
            email: ایمیل/نام منحصر به فرد کلاینت
            traffic_gb: محدودیت ترافیک (GB)، 0 = نامحدود
            expire_days: مدت اعتبار (روز)، 0 = نامحدود
            expiry_time_ms: timestamp میلی‌ثانیه برای انقضای دقیق
            sub_id: subId اختیاری (اتوماتیک تولید می‌شود)
            limit_ip: حداکثر IP همزمان (0 = نامحدود)
            tg_id: آی‌دی تلگرام برای ربات پنل (اختیاری)
        """
        if sub_id is None:
            sub_id = uuid.uuid4().hex[:16]

        # تبدیل GB به bytes
        total_bytes = traffic_gb * 1024 ** 3 if traffic_gb > 0 else 0

        # تبدیل روز به timestamp ms
        if expiry_time_ms is not None:
            expiry_ts = int(expiry_time_ms)
        elif expire_days > 0:
            expiry_ts = int((time.time() + expire_days * 86400) * 1000)
        else:
            expiry_ts = 0

        payload = {
            "client": {
                "email": email,
                "totalGB": total_bytes,
                "expiryTime": expiry_ts,
                "tgId": tg_id,
                "limitIp": limit_ip,
                "enable": True,
                "subId": sub_id,
                "reset": 0,
            },
            "inboundIds": [inbound_id],
        }

        await self._request("POST", "/clients/add", json=payload)
        logger.info(f"کلاینت '{email}' در inbound {inbound_id} ایجاد شد.")

        # پنل UUID نهایی را بعد از ایجاد برمی‌گرداند؛ یک بار از get_client
        # می‌خوانیم تا client_uuid/uuid در دیتابیس و خروجی دقیق باشد.
        created = await self.get_client(email)
        if created:
            return created

        return ClientInfo(
            email=email,
            sub_id=sub_id,
            inbound_ids=[inbound_id],
            enable=True,
            total_gb=total_bytes,
            expiry_time=expiry_ts,
            limit_ip=limit_ip,
        )

    async def update_client(
        self,
        email: str,
        traffic_gb: int = 0,
        expire_days: int = 30,
        expiry_time_ms: Optional[int] = None,
        enable: bool = True,
        tg_id: int = 0,
        limit_ip: int = 0,
    ) -> None:
        """
        POST /panel/api/clients/update/:email
        ساختار جدید: فقط email در URL، نه inbound_id یا uuid

        توجه: پارامترهای inbound_id و client_uuid دیگر لازم نیستند.
        """
        total_bytes = traffic_gb * 1024 ** 3 if traffic_gb > 0 else 0
        if expiry_time_ms is not None:
            expiry_ts = int(expiry_time_ms)
        elif expire_days > 0:
            expiry_ts = int((time.time() + expire_days * 86400) * 1000)
        else:
            expiry_ts = 0

        payload = {
            "email": email,
            "totalGB": total_bytes,
            "expiryTime": expiry_ts,
            "tgId": tg_id,
            "enable": enable,
            "limitIp": limit_ip,
        }

        await self._request("POST", f"/clients/update/{email}", json=payload)
        logger.info(f"کلاینت '{email}' به‌روزرسانی شد.")

    async def get_new_uuid(self) -> str:
        """GET /panel/api/server/getNewUUID — UUID تازه برای کلاینت‌ها."""
        data = await self._request("GET", "/server/getNewUUID")
        obj = data.get("obj")
        if not obj:
            raise XUIError("پنل UUID جدید برنگرداند.")
        return str(obj)

    async def delete_client(self, email: str, keep_traffic: bool = False) -> None:
        """
        POST /panel/api/clients/del/:email
        ساختار جدید: email در URL (نه inbound_id + uuid)

        Args:
            email: ایمیل کلاینت
            keep_traffic: حفظ آمار ترافیک بعد از حذف
        """
        path = f"/clients/del/{email}"
        if keep_traffic:
            path += "?keepTraffic=1"
        await self._request("POST", path)
        logger.info(f"کلاینت '{email}' حذف شد.")

    async def get_client_traffic(self, email: str) -> Optional[ClientInfo]:
        """
        GET /panel/api/clients/traffic/:email
        ساختار response جدید: obj با فیلدهای email, up, down, total, expiryTime
        """
        try:
            data = await self._request("GET", f"/clients/traffic/{email}")
            obj = data.get("obj")
            if not obj:
                return None
            return ClientInfo(
                email=obj.get("email", email),
                sub_id="",
                inbound_ids=[],
                enable=True,
                up=obj.get("up", 0),
                down=obj.get("down", 0),
                total_gb=obj.get("total", 0),
                expiry_time=obj.get("expiryTime", 0),
                raw=obj,
            )
        except XUINotFoundError:
            return None

    async def get_client(self, email: str) -> Optional[ClientInfo]:
        """
        GET /panel/api/clients/get/:email
        دریافت اطلاعات کامل یک کلاینت.
        """
        try:
            data = await self._request("GET", f"/clients/get/{email}")
            obj = data.get("obj")
            if not obj:
                return None
            return self._parse_client(obj)
        except (XUINotFoundError, XUIError):
            return None

    def _parse_client(self, obj: Dict[str, Any]) -> ClientInfo:
        """تبدیل dict پنل به ClientInfo."""
        traffic = obj.get("traffic") or {}
        return ClientInfo(
            email=obj.get("email", ""),
            sub_id=obj.get("subId", ""),
            inbound_ids=obj.get("inboundIds", []),
            enable=obj.get("enable", True),
            total_gb=obj.get("totalGB", 0),
            expiry_time=obj.get("expiryTime", 0),
            up=traffic.get("up", 0),
            down=traffic.get("down", 0),
            limit_ip=obj.get("limitIp", 0),
            uuid=obj.get("uuid", ""),
            raw=obj,
        )

    async def reset_client_traffic(self, email: str) -> None:
        """
        POST /panel/api/clients/resetTraffic/:email
        ساختار جدید: فقط email در URL (نه inbound_id)
        """
        await self._request("POST", f"/clients/resetTraffic/{email}")
        logger.info(f"ترافیک کلاینت '{email}' ریست شد.")

    async def get_sub_links(self, sub_id: str) -> List[str]:
        """
        GET /panel/api/clients/subLinks/:subId
        لیست لینک‌های اتصال برای یک subscription ID.

        Returns:
            لیست string لینک‌ها (vless://, vmess://, ...)
        """
        try:
            data = await self._request("GET", f"/clients/subLinks/{sub_id}")
            return data.get("obj", [])
        except (XUINotFoundError, XUIError):
            return []

    async def get_client_links(self, email: str) -> List[str]:
        """
        GET /panel/api/clients/links/:email
        لینک‌های اتصال یک کلاینت بر اساس ایمیل.
        """
        try:
            data = await self._request("GET", f"/clients/links/{email}")
            return data.get("obj", [])
        except (XUINotFoundError, XUIError):
            return []

    # ── Server / Panel management ─────────────

    async def get_server_status(self) -> Dict[str, Any]:
        """GET /panel/api/server/status — وضعیت سرور (CPU، RAM، xray)."""
        data = await self._request("GET", "/server/status")
        return data.get("obj", {})

    async def restart_xray(self) -> None:
        """POST /panel/api/server/restartXrayService — ریستارت Xray."""
        await self._request("POST", "/server/restartXrayService")
        logger.info("Xray ریستارت شد.")

    async def get_xray_logs(self, count: int = 100, level: str = "info") -> str:
        """
        POST /panel/api/server/logs/:count
        ساختار جدید: POST با body JSON

        پنل سنایی ممکن است obj را به شکل‌های مختلف برگرداند:
          - list of strings
          - single string (چند خط با \n)
          - None / "" → لاگ خالی یا endpoint پشتیبانی نمی‌شود
        """
        try:
            data = await self._request(
                "POST",
                f"/server/logs/{count}",
                json={"level": level, "syslog": False},
            )
            obj = data.get("obj")

            # حالت ۱: لیست رشته‌ها
            if isinstance(obj, list) and obj:
                return "\n".join(str(x) for x in obj)

            # حالت ۲: رشته چندخطی
            if isinstance(obj, str) and obj.strip():
                return obj.strip()

            # حالت ۳: None یا خالی — تلاش با endpoint جایگزین (GET)
            try:
                data2 = await self._request("GET", f"/server/logs/{count}")
                obj2 = data2.get("obj")
                if isinstance(obj2, list) and obj2:
                    return "\n".join(str(x) for x in obj2)
                if isinstance(obj2, str) and obj2.strip():
                    return obj2.strip()
            except Exception:
                pass

            return "__EMPTY__"
        except XUIError as e:
            raise XUIError(f"دریافت لاگ ناموفق: {e}") from e

    # ── Subscription link helper ──────────────

    def build_sub_link(self, sub_id: str) -> str:
        """
        ساخت URL subscription برای دادن به کاربر.
        مسیر استاندارد پنل: {origin}/sub/{sub_id}
        فقط scheme+host+port استفاده می‌شود — webBasePath حذف می‌شود.

        مثال:
          PANEL_URL = https://host:8443/ebHlkqXkBbjm2bI260
          sub_link  = https://host:8443/sub/abc123subid   ✅ (بدون webBasePath)
        """
        return f"{self._sub_base}/sub/{sub_id}"

    # ── Panel DB download ─────────────────────

    async def download_panel_db(self) -> bytes:
        """
        GET /panel/api/server/getDb
        دانلود مستقیم فایل SQLite پنل به صورت bytes.
        این endpoint فایل را به صورت stream (attachment) برمی‌گرداند.
        """
        if not self._session:
            await self._init_session()

        url = f"{self._base}{self._api}/server/getDb"
        try:
            headers = {"Accept": "application/octet-stream"}
            if self._api_token:
                headers["Authorization"] = f"Bearer {self._api_token}"
            resp = await self._session.get(url, headers=headers)
            if resp.status_code == 401:
                if self._api_token:
                    raise XUIAuthError("API Token پنل نامعتبر است یا دسترسی دانلود DB ندارد.")
                await self.login()
                resp = await self._session.get(url, headers=headers)
            resp.raise_for_status()

            content = resp.content

            # بررسی: اگر پنل به جای فایل، JSON خطا برگرداند
            if content and not content.startswith(b"SQLite format 3"):
                # تلاش برای خواندن پیام خطای JSON
                try:
                    err = resp.json()
                    msg = err.get("msg", "پاسخ نامعتبر از پنل")
                    raise XUIError(f"پنل فایل DB برنگرداند: {msg}")
                except (ValueError, KeyError):
                    raise XUIError("پاسخ پنل یک فایل SQLite معتبر نیست")

            return content
        except httpx.TransportError as exc:
            raise XUIConnectionError(f"خطای اتصال در دانلود DB: {exc}") from exc

    async def get_all_clients(self) -> List[ClientInfo]:
        """
        GET /panel/api/clients/list
        لیست کامل کلاینت‌ها با ترافیک و اطلاعات inbound.
        """
        try:
            data = await self._request("GET", "/clients/list")
            result: List[ClientInfo] = []
            for item in data.get("obj", []):
                result.append(self._parse_client(item))
            return result
        except XUIError:
            return []

    async def get_online_clients(self) -> List[str]:
        """
        POST /panel/api/clients/onlines
        لیست ایمیل کلاینت‌های آنلاین (متصل).
        """
        try:
            data = await self._request("POST", "/clients/onlines", json={})
            return data.get("obj", []) or []
        except XUIError:
            return []

    async def find_client_by_uuid(self, uuid_str: str) -> Optional[ClientInfo]:
        """
        جستجوی کلاینت بر اساس UUID در لیست کامل کلاینت‌ها.

        از GET /panel/api/clients/list استفاده می‌کند که uuid را برمی‌گرداند.
        UUID کاملاً یونیک است — هر کلاینت یک UUID منحصربه‌فرد دارد.

        Args:
            uuid_str: UUID کلاینت (36 کاراکتر، فرمت: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)

        Returns:
            ClientInfo اگر پیدا شد، None در غیر این صورت
        """
        uuid_clean = uuid_str.strip().lower()
        try:
            data = await self._request("GET", "/clients/list")
            for item in data.get("obj", []):
                if str(item.get("uuid", "")).lower() == uuid_clean:
                    return self._parse_client(item)
        except XUIError as e:
            logger.warning(f"خطا در جستجوی UUID: {e}")
        return None
