"""
main.py — نقطه ورود ربات تلگرام VPN
فاز ۵: APScheduler + rate limiting + error handling
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import shutil
import socket
import subprocess
from pathlib import Path
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from config import settings
from database import AsyncSessionLocal, init_db
from database.crud import create_plan, get_all_plans, get_setting, set_setting
from handlers.admin import router as admin_router
from handlers.broadcast import router as broadcast_router
from handlers.errors import router as error_router
from handlers.navigation import router as navigation_router
from handlers.payments import router as payment_router
from handlers.referral import router as referral_router
from handlers.shop import router as shop_router
from handlers.tickets import router as ticket_router
from handlers.user import router as user_router
from handlers.uuid_import import router as uuid_router
from handlers.card_payment import router as card_payment_router
from handlers.maxelpay_payment import router as maxelpay_router
from middlewares.activity_log import ActivityLogMiddleware
from middlewares.blocked_user import BlockedUserMiddleware
from middlewares.rate_limit import RateLimitMiddleware
from services.activity_log import ActivityLoggingBot
from services.notifications import check_expired_subscriptions, cleanup_stale_payments
from services.backup import send_daily_backups
from services.webhook_server import start_webhook_server


# ──────────────────────────────────────────────
# تنظیم لاگ
# ──────────────────────────────────────────────

async def _notify_admins_startup(bot) -> None:
    """
    بعد از راه‌اندازی به ادمین‌ها پیام می‌فرسته.
    اگه کریپتو فعاله ولی API Key نداره، هشدار قرمز می‌ده.
    """
    from services.payment_methods import get_payment_status

    warnings = []

    if not settings.nowpayments_api_key:
        pm = await get_payment_status()
        if pm["crypto"]:
            warnings.append(
                "🚨 <b>کریپتو فعاله ولی API Key ندارد!</b>\n"
                "کاربران آدرس جعلی می‌بینند.\n"
                "→ <code>NOWPAYMENTS_API_KEY</code> را در .env وارد کنید."
            )

    if settings.nowpayments_api_key and not settings.nowpayments_ipn_secret:
        warnings.append(
            "⚠️ <b>IPN Secret تنظیم نشده.</b>\n"
            "تأیید خودکار پرداخت غیرفعال است.\n"
            "→ <code>NOWPAYMENTS_IPN_SECRET</code> را در .env وارد کنید."
        )

    if not warnings:
        return

    text = (
        "🤖 <b>ربات راه‌اندازی شد</b> — هشدار تنظیمات:\n"
        "━━━━━━━━━━━━━━━\n"
        + "\n\n".join(warnings)
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception:
            pass


def _web_panel_dir() -> Path:
    return Path(__file__).resolve().parent / "web-panel"


def _node_cmd() -> str:
    if os.name == "nt":
        return "npm.cmd"
    return "npm"


def _port_available(port: int, host: str = "127.0.0.1") -> bool:
    families = [socket.AF_INET]
    if hasattr(socket, "AF_INET6"):
        families.append(socket.AF_INET6)

    for family in families:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                bind_host = host
                if family == socket.AF_INET6 and host == "127.0.0.1":
                    bind_host = "::1"
                sock.bind((bind_host, port))
            except OSError:
                return False
    return True


def _find_free_port(start: int = 3000, limit: int = 25, host: str = "127.0.0.1") -> int | None:
    for port in range(start, start + limit):
        if _port_available(port, host=host):
            return port
    return None


def _listening_pids_for_port(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []

    pids: set[int] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local = parts[1]
        state = parts[3].upper()
        pid_raw = parts[4]
        if state != "LISTENING" or not pid_raw.isdigit():
            continue
        if local.endswith(f":{port}"):
            pids.add(int(pid_raw))
    return sorted(pids)


def _process_commandline_windows(pid: int) -> str:
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    return result.stdout.strip()


def _terminate_pid_tree(pid: int) -> None:
    with suppress(OSError):
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )


def _free_web_panel_port(panel_dir: Path, port: int) -> bool:
    if _port_available(port, host="127.0.0.1" if os.name == "nt" else "0.0.0.0"):
        return True

    if os.name == "nt":
        repo_hint = str(panel_dir / ".next" / "standalone" / "server.js").lower()
        for pid in _listening_pids_for_port(port):
            cmdline = _process_commandline_windows(pid).lower()
            if repo_hint and repo_hint in cmdline:
                logger.warning(f"Stopping stale standalone web panel on port {port} (PID {pid}).")
                _terminate_pid_tree(pid)
        return _port_available(port, host="127.0.0.1")

    return False


def _web_panel_sources_newer_than(panel_dir: Path, build_id: Path) -> bool:
    if not build_id.exists():
        return True

    build_mtime = build_id.stat().st_mtime
    roots = [
        panel_dir / "app",
        panel_dir / "components",
        panel_dir / "lib",
        panel_dir / "scripts",
    ]
    files = [
        panel_dir / "next.config.js",
        panel_dir / "package.json",
        panel_dir / "package-lock.json",
        panel_dir / "tsconfig.json",
    ]

    for root in roots:
        if not root.exists():
            continue
        for file_path in root.rglob("*"):
            if file_path.is_file() and file_path.stat().st_mtime > build_mtime:
                return True

    return any(file_path.exists() and file_path.stat().st_mtime > build_mtime for file_path in files)


def _sync_standalone_web_assets(panel_dir: Path) -> None:
    standalone_dir = panel_dir / ".next" / "standalone"
    if not standalone_dir.exists():
        return

    source_static = panel_dir / ".next" / "static"
    target_static = standalone_dir / ".next" / "static"
    if source_static.exists():
        if target_static.exists():
            shutil.rmtree(target_static, ignore_errors=True)
        shutil.copytree(source_static, target_static, dirs_exist_ok=True)

    source_public = panel_dir / "public"
    target_public = standalone_dir / "public"
    if source_public.exists():
        if target_public.exists():
            shutil.rmtree(target_public, ignore_errors=True)
        shutil.copytree(source_public, target_public, dirs_exist_ok=True)


RESTART_MARKER = Path(__file__).resolve().parent / ".onebot-restart"
INSTANCE_LOCK = Path(__file__).resolve().parent / ".onebot-main.lock"
WEB_PANEL_PID_FILE = Path(__file__).resolve().parent / ".onebot-web-panel.pid"


def _read_lock_pid(lock_path: Path) -> int | None:
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.isdigit():
        return None
    return int(raw)


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return False
        return result.returncode == 0 and str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _acquire_instance_lock(lock_path: Path) -> bool:
    current_pid = os.getpid()
    existing_pid = _read_lock_pid(lock_path)
    if existing_pid and existing_pid != current_pid and _pid_exists(existing_pid):
        logger.error(
            f"Another bot instance is already running with PID {existing_pid}. "
            "This process will exit to avoid Telegram polling conflicts."
        )
        return False

    if lock_path.exists():
        with suppress(OSError):
            lock_path.unlink()

    try:
        lock_path.write_text(str(current_pid), encoding="utf-8")
    except OSError as exc:
        logger.error(f"Could not create instance lock file {lock_path}: {exc}")
        return False

    return True


def _release_instance_lock(lock_path: Path) -> None:
    existing_pid = _read_lock_pid(lock_path)
    if existing_pid != os.getpid():
        return
    with suppress(OSError):
        lock_path.unlink()


def _read_panel_pid() -> int | None:
    try:
        raw = WEB_PANEL_PID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.isdigit():
        return None
    return int(raw)


def _write_panel_pid(pid: int) -> None:
    try:
        WEB_PANEL_PID_FILE.write_text(str(pid), encoding="utf-8")
    except OSError as exc:
        logger.warning(f"Could not persist web panel PID: {exc}")


def _clear_panel_pid() -> None:
    with suppress(OSError):
        WEB_PANEL_PID_FILE.unlink()


def _ensure_nginx_upload_limit() -> None:
    if os.name == "nt" or shutil.which("nginx") is None:
        return

    conf_path = Path("/etc/nginx/conf.d/onebot-upload-size.conf")
    vhost_path = Path("/etc/nginx/conf.d/onebot-webhook.conf")
    desired = "# ONEBOT VPN Bot - shared upload size limit\n# 0 disables nginx request body size checks for panel uploads.\nclient_max_body_size 0;\n"

    try:
        current = conf_path.read_text(encoding="utf-8") if conf_path.exists() else ""
        if current != desired:
            conf_path.write_text(desired, encoding="utf-8")
        if vhost_path.exists():
            current_vhost = vhost_path.read_text(encoding="utf-8")
            updated_vhost = re.sub(r"client_max_body_size\s+[^;]+;", "client_max_body_size 0;", current_vhost)
            if updated_vhost != current_vhost:
                vhost_path.write_text(updated_vhost, encoding="utf-8")
    except OSError as exc:
        logger.warning(f"Could not write nginx upload limit config: {exc}")
        return

    try:
        test = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
        if test.returncode != 0:
            logger.warning("nginx config test failed after writing upload limit.")
            return

        reload_proc = subprocess.run(["systemctl", "reload", "nginx"], capture_output=True, text=True)
        if reload_proc.returncode != 0:
            subprocess.run(["nginx", "-s", "reload"], capture_output=True, text=True)
        logger.info("nginx upload limit configured.")
    except Exception as exc:
        logger.warning(f"Could not refresh nginx upload limit: {exc}")


async def _watch_restart_marker(marker: Path, restart_event: asyncio.Event) -> None:
    while not restart_event.is_set():
        if marker.exists():
            with suppress(OSError):
                marker.unlink()
            restart_event.set()
            return
        await asyncio.sleep(2)


async def _start_node_web_panel() -> asyncio.subprocess.Process | None:
    """
    Starts the Next.js operator panel alongside the bot.
    The panel is optional and only starts when the folder exists and
    WEB_ADMIN_ENABLED is not explicitly disabled.
    """
    panel_dir = _web_panel_dir()
    if not panel_dir.exists():
        logger.warning(f"Node web panel directory not found: {panel_dir}")
        return None

    existing_pid = _read_panel_pid()
    if existing_pid and _pid_exists(existing_pid):
        logger.info(f"Reusing already running Node web panel (PID {existing_pid}).")
        return None
    if existing_pid and not _pid_exists(existing_pid):
        _clear_panel_pid()

    npm = _node_cmd()
    npx = "npx.cmd" if os.name == "nt" else "npx"
    node_cmd = "node.exe" if os.name == "nt" else "node"
    if shutil.which("node") is None and shutil.which(node_cmd) is None and shutil.which("npx") is None and shutil.which(npx) is None:
        logger.warning("Node.js/npm not found — web panel will not start.")
        return None

    root_dir = str(Path(__file__).resolve().parent)
    bind_host = "127.0.0.1" if os.name == "nt" else "0.0.0.0"
    panel_port = 3000
    if not _free_web_panel_port(panel_dir, panel_port):
        logger.warning("Web panel port 3000 is unavailable and could not be recovered.")
        return None

    _ensure_nginx_upload_limit()

    dev_mode_flag = os.environ.get("ONEBOT_WEB_PANEL_DEV", "").strip().lower()
    use_dev_mode = os.name == "nt" and dev_mode_flag != "0"
    node_env = {
        **os.environ,
        "NODE_ENV": "development" if use_dev_mode else "production",
        "PORT": str(panel_port),
        "HOST": bind_host,
        "HOSTNAME": bind_host,
        "ONEBOT_DATA_DIR": str((Path(__file__).resolve().parent / "data").resolve()),
        "ONEBOT_ROOT": root_dir,
    }
    if use_dev_mode:
        logger.info(f"Starting Node web panel in dev mode for Windows: {panel_dir} (port {panel_port})")
        proc = await asyncio.create_subprocess_exec(
            npm,
            "run",
            "dev",
            cwd=str(panel_dir),
            stdout=None,
            stderr=None,
            stdin=None,
            env=node_env,
        )
        _write_panel_pid(proc.pid)
        return proc

    next_build_id = panel_dir / ".next" / "BUILD_ID"
    next_static = panel_dir / ".next" / "static"
    needs_build = not next_build_id.exists() or not next_static.exists() or _web_panel_sources_newer_than(panel_dir, next_build_id)
    if needs_build:
        logger.info("Node web panel build missing or stale — building it now...")
        build_proc = await asyncio.create_subprocess_exec(
            npm,
            "run",
            "build",
            cwd=str(panel_dir),
            stdout=None,
            stderr=None,
            stdin=None,
            env=node_env,
        )
        build_code = await build_proc.wait()
        if build_code != 0 or not next_build_id.exists() or not next_static.exists():
            logger.warning("Node web panel build failed or production assets are missing.")
            return None

    standalone_server = panel_dir / ".next" / "standalone" / "server.js"
    if standalone_server.exists():
        _sync_standalone_web_assets(panel_dir)
        logger.info(f"Starting Node web panel (standalone): {panel_dir} (port {panel_port})")
        proc = await asyncio.create_subprocess_exec(
            node_cmd,
            str(standalone_server),
            cwd=str(panel_dir),
            stdout=None,
            stderr=None,
            stdin=None,
            env=node_env,
        )
        _write_panel_pid(proc.pid)
        return proc

    logger.info(f"Starting Node web panel (production start): {panel_dir} (port {panel_port})")
    proc = await asyncio.create_subprocess_exec(
        npx,
        "next",
        "start",
        "-p",
        str(panel_port),
        "-H",
        bind_host,
        cwd=str(panel_dir),
        stdout=None,
        stderr=None,
        stdin=None,
        env=node_env,
    )
    _write_panel_pid(proc.pid)
    return proc


async def _terminate_web_panel_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        _clear_panel_pid()
        return
    if os.name == "nt":
        with suppress(Exception):
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=10)
        _clear_panel_pid()
        return

    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        proc.kill()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=10)
    _clear_panel_pid()


async def _seed_default_plans() -> None:
    """
    اگر هیچ پلنی در دیتابیس نیست، پلن‌های پیش‌فرض ایجاد می‌کند.
    ادمین می‌تواند بعداً قیمت‌ها را از بات تغییر دهد.
    """
    async with AsyncSessionLocal() as session:
        plans = await get_all_plans(session)
        if plans:
            return  # پلن وجود دارد، seed نمی‌کند

        defaults = [
            # (نام، حجم GB، روز، قیمت USDT، limit_ip، sort)
            ("10 گیگ — ۱ ماهه", 10, 30, 3.0, 0, 1),
            ("20 گیگ — ۱ ماهه", 20, 30, 5.0, 0, 2),
            ("40 گیگ — ۱ ماهه", 40, 30, 8.0, 0, 3),
            ("60 گیگ — ۱ ماهه", 60, 30, 11.0, 0, 4),
            ("100 گیگ — ۱ ماهه", 100, 30, 15.0, 0, 5),
            ("نامحدود — ۱ کاربره", 0, 30, 10.0, 1, 6),
            ("نامحدود — ۲ کاربره", 0, 30, 18.0, 2, 7),
            ("نامحدود — ۳ کاربره", 0, 30, 25.0, 3, 8),
        ]
        for name, gb, days, price, lip, sort in defaults:
            await create_plan(session, name=name, traffic_gb=gb,
                              duration_days=days, price_usdt=price,
                              limit_ip=lip, sort_order=sort)
        logger.success(f"✅ {len(defaults)} پلن پیش‌فرض ایجاد شد.")


async def _configured_bot_username() -> str:
    configured = settings.bot_username.strip().lstrip("@")
    try:
        async with AsyncSessionLocal() as session:
            db_username = (await get_setting(session, "BOT_USERNAME", "")).strip().lstrip("@")
            if db_username:
                configured = db_username
    except Exception as exc:
        logger.debug(f"خواندن BOT_USERNAME از دیتابیس ناموفق بود: {exc}")
    return configured


async def _resolve_bot_token() -> str:
    """
    BOT_TOKEN را از admin_settings دیتابیس می‌خواند و اگر خالی بود،
    از محیط اجرا / .env استفاده می‌کند.

    این باعث می‌شود وقتی توکن از web panel ذخیره می‌شود،
    مقدار جدید حتی اگر .env قدیمی باشد، برنده شود.
    """
    env_token = settings.bot_token.strip()
    expected_username = (await _configured_bot_username()).lower()

    async def _probe_token(token: str) -> tuple[str, int] | None:
        probe_bot: Bot | None = None
        try:
            probe_bot = Bot(token=token)
            me = await probe_bot.get_me()
            return (me.username or "").lower(), int(me.id)
        except Exception as exc:
            logger.warning(f"بررسی BOT_TOKEN ناموفق بود: {exc}")
            return None
        finally:
            if probe_bot is not None:
                await probe_bot.session.close()

    try:
        async with AsyncSessionLocal() as session:
            db_token = (await get_setting(session, "BOT_TOKEN", "")).strip()
            if db_token:
                if env_token and env_token != db_token:
                    logger.warning(
                        "BOT_TOKEN ذخیره‌شده در دیتابیس با BOT_TOKEN محیط/.env متفاوت است. "
                        "این حالت بعد از انتقال دیتابیس به ربات جدید می‌تواند باعث شود polling روی ربات قبلی اجرا شود."
                    )
                    if expected_username:
                        db_identity = await _probe_token(db_token)
                        env_identity = await _probe_token(env_token)
                        db_matches = bool(db_identity and db_identity[0] == expected_username)
                        env_matches = bool(env_identity and env_identity[0] == expected_username)
                        if env_matches and not db_matches:
                            logger.warning(
                                f"BOT_USERNAME روی @{expected_username} تنظیم شده و فقط توکن محیط با آن تطبیق دارد؛ "
                                "BOT_TOKEN دیتابیس با مقدار محیط همگام شد."
                            )
                            await set_setting(session, "BOT_TOKEN", env_token)
                            return env_token
                        if db_matches:
                            logger.info("BOT_TOKEN دیتابیس با BOT_USERNAME تنظیم‌شده تطبیق دارد؛ همان استفاده می‌شود.")
                        elif env_identity and not db_identity:
                            logger.warning("BOT_TOKEN دیتابیس معتبر نبود؛ از BOT_TOKEN محیط استفاده می‌شود.")
                            await set_setting(session, "BOT_TOKEN", env_token)
                            return env_token
                    else:
                        db_identity = await _probe_token(db_token)
                        if not db_identity:
                            env_identity = await _probe_token(env_token)
                            if env_identity:
                                logger.warning("BOT_TOKEN دیتابیس معتبر نبود؛ از BOT_TOKEN محیط استفاده می‌شود.")
                                await set_setting(session, "BOT_TOKEN", env_token)
                                return env_token
                        logger.warning(
                            "BOT_USERNAME تنظیم نشده، بنابراین برای جلوگیری از تغییر ناخواسته، BOT_TOKEN دیتابیس استفاده می‌شود. "
                            "برای مهاجرت امن به ربات جدید، BOT_USERNAME و BOT_TOKEN جدید را در .env/پنل ذخیره کنید."
                        )
                logger.info("BOT_TOKEN از admin_settings خوانده شد.")
                return db_token
    except Exception as exc:
        logger.warning(f"خواندن BOT_TOKEN از دیتابیس ناموفق بود: {exc}")

    if env_token:
        logger.info("BOT_TOKEN از .env استفاده شد.")
    return env_token


async def _prepare_bot_for_polling(bot: Bot) -> None:
    """Verify the bot identity and remove any stale webhook before polling."""
    me = await bot.get_me()
    logger.success(f"Telegram bot آماده است: @{me.username or '-'} (id={me.id})")
    expected_username = await _configured_bot_username()
    if expected_username and (me.username or "").lower() != expected_username.lower():
        logger.warning(
            f"BOT_USERNAME روی @{expected_username} تنظیم شده اما توکن فعلی متعلق به @{me.username or '-'} است. "
            "اگر دیتابیس را به ربات جدید منتقل کرده‌اید، BOT_TOKEN ذخیره‌شده در admin_settings را بررسی کنید."
        )
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url:
        logger.warning(
            f"Webhook فعال روی Telegram پیدا شد و قبل از polling پاک می‌شود: {webhook_info.url}"
        )
    if webhook_info.pending_update_count:
        logger.info(f"Telegram pending updates before polling: {webhook_info.pending_update_count}")
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook تلگرام پاک شد و polling می‌تواند پیام‌های جدید را دریافت کند.")


def setup_logging() -> None:
    """
    پیکربندی loguru: console رنگی + فایل چرخشی.
    اگر پوشه لاگ قابل نوشتن نباشد، فقط stdout فعال می‌ماند و crash نمی‌کند.
    """
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level:<8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> — {message}"
        ),
        colorize=True,
    )

    # فایل لاگ — اختیاری، در صورت مشکل permission فقط هشدار می‌دهد
    try:
        log_path = Path(settings.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            settings.log_file,
            level=settings.log_level,
            rotation="10 MB",
            retention="30 days",
            compression="zip",
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{line} — {message}",
        )
    except PermissionError:
        logger.warning("⚠️  دسترسی نوشتن در پوشه logs نیست — فقط stdout فعال است.")


# ──────────────────────────────────────────────
# راه‌اندازی Scheduler
# ──────────────────────────────────────────────

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """
    ایجاد و پیکربندی APScheduler.

    Job‌ها:
      • check_expired_subscriptions   — هر ۶ ساعت
      • cleanup_stale_payments        — هر ساعت یک‌بار
      • send_daily_backups            — هر روز ساعت ۰۲:۰۰ UTC
    """
    scheduler = AsyncIOScheduler(timezone="UTC")

    # بررسی انقضا و sync ترافیک ← هر ۶ ساعت
    scheduler.add_job(
        check_expired_subscriptions,
        trigger=CronTrigger(hour="*/6", minute=0),
        args=[bot],
        id="six_hour_expiry_check",
        name="بررسی اشتراک‌ها هر ۶ ساعت",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # پاک‌سازی تراکنش‌های کریپتوی منقضی‌شده ← هر ساعت
    # تراکنش‌هایی که expires_at گذشته ولی status هنوز waiting است
    scheduler.add_job(
        cleanup_stale_payments,
        trigger=CronTrigger(minute=0),   # دقیقه ۰ هر ساعت
        id="hourly_payment_cleanup",
        name="پاک‌سازی تراکنش‌های کریپتوی منقضی",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # backup روزانه ← هر روز ۰۲:۰۰ UTC (قبل از ساعت شلوغ)
    scheduler.add_job(
        send_daily_backups,
        trigger=CronTrigger(hour=2, minute=0),
        args=[bot],
        id="daily_backup",
        name="پشتیبان‌گیری روزانه دیتابیس ربات و پنل",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    return scheduler


# ──────────────────────────────────────────────
# راه‌اندازی ربات
# ──────────────────────────────────────────────

async def main() -> None:
    setup_logging()
    if not _acquire_instance_lock(INSTANCE_LOCK):
        return
    try:
        logger.info("🚀 ربات VPN در حال راه‌اندازی...")
        web_panel_proc = None
        scheduler = None
        restart_requested = False

        # اگر restart قبلی نیمه‌کاره بوده، پاکش کن
        with suppress(OSError):
            RESTART_MARKER.unlink()

        logger.info("بررسی و ایجاد جداول دیتابیس...")
        await init_db()
        logger.success("دیتابیس آماده است ✓")

        await _seed_default_plans()

        stale_count = await cleanup_stale_payments()
        if stale_count:
            logger.info(f"startup cleanup: {stale_count} تراکنش کریپتوی منقضی‌شده پاک‌سازی شد.")

        bot = None
        bot_token = await _resolve_bot_token()
        if bot_token:
            bot = ActivityLoggingBot(
                token=bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
        else:
            logger.warning("BOT_TOKEN تنظیم نشده است — فقط پنل وب و webhook server اجرا می‌شوند.")

        if bot is not None:
            await _notify_admins_startup(bot)

        try:
            web_panel_proc = await _start_node_web_panel()
        except Exception as exc:
            logger.warning(f"Node web panel startup failed: {exc}")

        dp = Dispatcher(storage=MemoryStorage())

        blocked_middleware = BlockedUserMiddleware(admin_ids=list(settings.admin_ids))
        dp.message.middleware(ActivityLogMiddleware())
        dp.message.middleware(blocked_middleware)
        dp.callback_query.middleware(blocked_middleware)
        dp.message.middleware(
            RateLimitMiddleware(
                rate_limit=6,
                window_sec=8.0,
                admin_ids=list(settings.admin_ids),
            )
        )

        dp.include_router(error_router)
        dp.include_router(navigation_router)
        dp.include_router(admin_router)
        dp.include_router(broadcast_router)
        dp.include_router(referral_router)
        dp.include_router(shop_router)
        dp.include_router(card_payment_router)
        dp.include_router(maxelpay_router)
        dp.include_router(payment_router)
        dp.include_router(uuid_router)
        dp.include_router(ticket_router)
        dp.include_router(user_router)

        webhook_runner = None
        if settings.nowpayments_ipn_secret or getattr(settings, "webhook_port", 0):
            try:
                webhook_runner = await start_webhook_server()
            except Exception as e:
                logger.warning(f"Webhook server راه‌اندازی نشد: {e} — پرداخت polling دستی کار می‌کند")

        if bot is not None:
            scheduler = setup_scheduler(bot)
            scheduler.start()
            logger.success(f"Scheduler راه‌اندازی شد — {len(scheduler.get_jobs())} job فعال")

        try:
            if bot is None:
                logger.info("پنل وب فعال است. برای فعال شدن polling، BOT_TOKEN را در .env تنظیم کنید.")
                restart_event = asyncio.Event()
                watcher_task = asyncio.create_task(_watch_restart_marker(RESTART_MARKER, restart_event))
                idle_task = asyncio.create_task(asyncio.Event().wait())
                done, _ = await asyncio.wait({idle_task, watcher_task}, return_when=asyncio.FIRST_COMPLETED)
                if watcher_task in done and restart_event.is_set():
                    restart_requested = True
                    if not idle_task.done():
                        idle_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await idle_task
                else:
                    with suppress(asyncio.CancelledError):
                        await idle_task

            logger.info("شروع دریافت پیام‌ها (polling)...")
            restart_event = asyncio.Event()
            watcher_task = asyncio.create_task(_watch_restart_marker(RESTART_MARKER, restart_event))
            await _prepare_bot_for_polling(bot)
            allowed_updates = dp.resolve_used_update_types()
            logger.info(f"Allowed Telegram updates: {allowed_updates}")
            polling_task = asyncio.create_task(
                dp.start_polling(
                    bot,
                    allowed_updates=allowed_updates,
                    drop_pending_updates=True,
                )
            )
            logger.success("Polling task started; waiting for Telegram updates.")
            try:
                done, _ = await asyncio.wait({polling_task, watcher_task}, return_when=asyncio.FIRST_COMPLETED)
                if watcher_task in done and restart_event.is_set():
                    restart_requested = True
                    if not polling_task.done():
                        polling_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await polling_task
                else:
                    await polling_task
            except TelegramUnauthorizedError:
                logger.error(
                    "BOT_TOKEN نامعتبر است یا دسترسی Telegram رد شده است. "
                    "Bot polling متوقف شد، اما web panel و webhook server همچنان فعال می‌مانند."
                )
                if scheduler is not None:
                    scheduler.shutdown(wait=False)
                    scheduler = None
                await asyncio.Event().wait()
            except TelegramAPIError as exc:
                logger.warning(
                    f"Telegram API در زمان startup در دسترس نبود: {exc}. "
                    "Bot polling غیرفعال شد، اما web panel و webhook server همچنان فعال می‌مانند."
                )
                if scheduler is not None:
                    scheduler.shutdown(wait=False)
                    scheduler = None
                await asyncio.Event().wait()
            finally:
                watcher_task.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher_task
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)
            if webhook_runner:
                await webhook_runner.cleanup()
            if web_panel_proc and web_panel_proc.returncode is None and not restart_requested:
                await _terminate_web_panel_process(web_panel_proc)
            if bot is not None:
                await bot.session.close()
            logger.info("ربات متوقف شد.")

        if restart_requested:
            logger.info("🔁 تنظیمات جدید شناسایی شد — فرآیند در حال راه‌اندازی مجدد است...")
            _release_instance_lock(INSTANCE_LOCK)
            os.execv(sys.executable, [sys.executable, *sys.argv])
    finally:
        _release_instance_lock(INSTANCE_LOCK)


if __name__ == "__main__":
    asyncio.run(main())
