"""
middlewares/rate_limit.py — Anti-flood + Progressive Penalty Middleware

منطق کار:
  1. Burst detection: اگه کاربر در مدت BURST_WINDOW_SEC بیش از BURST_THRESHOLD پیام بفرسته →
     اولین تخلف = 5s cooldown، دومی = 10s، ... تا MAX_COOLDOWN_SEC (30s)
  2. اگه کاربر در حال cooldown بود، پیام رد می‌شه و زمان باقی‌مانده اعلام می‌شه
  3. هر RESET_INTERVAL_SEC (1 ساعت) تمام وضعیت همه کاربران پاک می‌شه
  4. UUID و deep link whitelist شدن
  5. ادمین‌ها از rate limit معاف هستن
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from loguru import logger

# ── تنظیمات ────────────────────────────────────
BURST_THRESHOLD    = 6      # پیام در BURST_WINDOW_SEC قبل از penalty
BURST_WINDOW_SEC   = 8.0    # پنجره burst (ثانیه)
COOLDOWN_STEP_SEC  = 3      # هر تخلف چند ثانیه اضافه می‌شه
MAX_COOLDOWN_SEC   = 15     # حداکثر cooldown
RESET_INTERVAL_SEC = 3600   # ریست ساعتی

# ── Whitelist patterns ─────────────────────────
_UUID_RE     = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_UUID_HEX_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


async def _get_admin_command() -> str:
    """دستور ورود ادمین را از DB می‌خواند (cached نیست — lightweight)."""
    try:
        from database import AsyncSessionLocal
        from database.crud import get_setting
        async with AsyncSessionLocal() as session:
            return await get_setting(session, "admin_login_command", "admin_secret")
    except Exception:
        return "admin_secret"


def _is_whitelisted(text: Optional[str]) -> bool:
    """
    آیا این متن از rate limit معاف است؟
    - UUID استاندارد (با یا بدون خط‌تیره)
    - /start با deep link payload
    """
    if not text:
        return False
    s = text.strip()
    if _UUID_RE.match(s) or _UUID_HEX_RE.match(s):
        return True
    if re.match(r"^/start\s+\S+", s):
        return True
    return False


async def _is_admin_command(text: str) -> bool:
    """آیا این پیام دستور ورود ادمین است؟ — از rate limit معاف است."""
    if not text.startswith("/"):
        return False
    cmd_used = text.split()[0].lstrip("/").split("@")[0]
    current_cmd = await _get_admin_command()
    return cmd_used == current_cmd


# ── وضعیت هر کاربر ────────────────────────────

@dataclass
class _UserState:
    burst_times:     List[float] = field(default_factory=list)
    cooldown_until:  float = 0.0
    violation_count: int   = 0
    last_warned_at:  float = 0.0


# ── Middleware ─────────────────────────────────

class RateLimitMiddleware(BaseMiddleware):
    """
    Anti-flood با penalty پله‌ای و ریست خودکار ساعتی.

    سازگار با main.py فعلی:
        dp.message.middleware(RateLimitMiddleware(rate_limit=6, window_sec=8.0))
    """

    def __init__(
        self,
        rate_limit: int = BURST_THRESHOLD,
        window_sec: float = BURST_WINDOW_SEC,
        admin_ids: Optional[list] = None,
    ) -> None:
        self._burst_threshold = rate_limit
        self._burst_window    = window_sec
        self._admin_ids: list = admin_ids or []
        self._states: Dict[int, _UserState] = defaultdict(_UserState)
        self._last_reset: float = time.monotonic()

    def _maybe_reset(self, now: float) -> None:
        """ریست ساعتی — وضعیت همه کاربران پاک می‌شه."""
        if now - self._last_reset >= RESET_INTERVAL_SEC:
            count = len(self._states)
            self._states.clear()
            self._last_reset = now
            if count:
                logger.info(f"[RateLimit] ریست ساعتی — {count} کاربر پاک شد")

    def _next_cooldown(self, violation_count: int) -> int:
        """violation=1→3s, =2→6s, ... max 15s"""
        return min(violation_count * COOLDOWN_STEP_SEC, MAX_COOLDOWN_SEC)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        user = event.from_user
        if not user:
            return await handler(event, data)

        uid  = user.id
        now  = time.monotonic()
        text = event.text or event.caption or ""

        # ریست ساعتی
        self._maybe_reset(now)

        # ادمین‌ها معاف
        if uid in self._admin_ids:
            return await handler(event, data)

        # UUID و deep link معاف
        if _is_whitelisted(text):
            return await handler(event, data)

        # دستور ورود ادمین (هر اسمی که داشته باشه) معاف
        if text.startswith("/") and await _is_admin_command(text):
            return await handler(event, data)

        state = self._states[uid]

        # ── در حال cooldown؟ ──────────────────
        if state.cooldown_until > now:
            remaining = int(state.cooldown_until - now) + 1
            # هر 5 ثانیه یه هشدار (نه برای هر پیام)
            if now - state.last_warned_at >= 5.0:
                state.last_warned_at = now
                try:
                    await event.answer(
                        f"⏳ لطفاً <b>{remaining} ثانیه</b> صبر کنید.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            return  # پیام رد می‌شه

        # ── بررسی burst ──────────────────────
        state.burst_times = [
            t for t in state.burst_times if now - t < self._burst_window
        ]
        state.burst_times.append(now)

        if len(state.burst_times) >= self._burst_threshold:
            state.violation_count += 1
            cooldown = self._next_cooldown(state.violation_count)
            state.cooldown_until  = now + cooldown
            state.burst_times     = []
            state.last_warned_at  = now

            logger.warning(
                f"[RateLimit] کاربر {uid} — تخلف #{state.violation_count} "
                f"— cooldown {cooldown}s"
            )
            try:
                await event.answer(
                    f"⚠️ ارسال سریع شناسایی شد.\n"
                    f"⏳ <b>{cooldown} ثانیه</b> محدودیت فعال شد.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            return

        return await handler(event, data)
