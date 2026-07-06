#!/usr/bin/env bash
# ============================================================
#  ONEBOT VPN Bot — Installer v1.3
#  Built by github.com/sariyan-0
#
#  Usage:
#    chmod +x install.sh && sudo bash install.sh
#
#  Supported OS:
#    Ubuntu 20.04 / 22.04 / 24.04
#    Debian 10 / 11 / 12
#    CentOS / RHEL / AlmaLinux / Rocky Linux 8 / 9
#
#  v1.3 changes:
#    - Fix false-positive "directory already exists" on fresh install
#    - Auto DNS fallback: if domain resolution fails, tries Google/Cloudflare/quad9 DNS
# ============================================================

set -euo pipefail

# Public repository used for streamed installs and updates
REPO_URL="https://github.com/sariyan-0/OneBot.git"

# ── Colors ───────────────────────────────────────────────────
RED='\033[0;31m';  GREEN='\033[0;32m';  YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m';      DIM='\033[2m'; RESET='\033[0m'

_banner() {
  clear
  echo -e "${CYAN}${BOLD}"
  echo "  ╔═══════════════════════════════════════════════════╗"
  echo "  ║        ONEBOT VPN BOT — INSTALLER v1.3            ║"
  echo "  ║        Built by github.com/sariyan-0              ║"
  echo "  ╚═══════════════════════════════════════════════════╝"
  echo -e "${RESET}"
}

_step()   { echo -e "\n${CYAN}${BOLD}▶  $*${RESET}"; }
_ok()     { echo -e "   ${GREEN}✔${RESET}  $*"; }
_warn()   { echo -e "   ${YELLOW}⚠${RESET}  $*"; }
_err()    { echo -e "   ${RED}✘  ERROR: $*${RESET}"; }
_info()   { echo -e "   ${CYAN}ℹ${RESET}  $*"; }
_line()   { echo -e "${DIM}────────────────────────────────────────────────────${RESET}"; }
_ask()    { read -rp "$(echo -e "   ${BOLD}?${RESET}  $1: ")" "$2"; }
_ask_s()  { read -rsp "$(echo -e "   ${BOLD}?${RESET}  $1: ")" "$2"; echo ""; }
_rand_secret() {
  local len="${1:-24}"
  local secret=""

  if command -v openssl &>/dev/null; then
    secret="$(openssl rand -base64 "$((len * 2))" 2>/dev/null | tr -dc 'A-Za-z0-9' | head -c "$len" || true)"
  fi

  if [[ -z "$secret" ]]; then
    secret="$(set +o pipefail; tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$len" 2>/dev/null || true)"
  fi

  printf '%s' "$secret"
}

# ── Root check ───────────────────────────────────────────────
[[ $EUID -ne 0 ]] && { _err "Run as root: sudo bash install.sh"; exit 1; }

# ── Detect OS ────────────────────────────────────────────────
_detect_os() {
  OS_ID=""
  OS_LIKE=""
  PKG_MANAGER=""

  if [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    OS_ID="${ID:-}"
    OS_LIKE="${ID_LIKE:-}"
  fi

  # تشخیص package manager
  if command -v apt-get &>/dev/null; then
    PKG_MANAGER="apt"
  elif command -v dnf &>/dev/null; then
    PKG_MANAGER="dnf"
  elif command -v yum &>/dev/null; then
    PKG_MANAGER="yum"
  else
    PKG_MANAGER="unknown"
  fi
}

# ── Retry wrapper ────────────────────────────────────────────
# اجرای یه دستور تا N بار با تاخیر — در صورت خطای شبکه یا apt lock
_retry() {
  local max_attempts=${1}; shift
  local delay=${1};        shift
  local attempt=1
  until "$@"; do
    if (( attempt >= max_attempts )); then
      _err "Command failed after ${max_attempts} attempts: $*"
      return 1
    fi
    _warn "Attempt ${attempt}/${max_attempts} failed. Retrying in ${delay}s..."
    sleep "$delay"
    (( attempt++ ))
  done
}

_wait_for_apt() {
  local timeout="${1:-300}"
  local waited=0
  local lock_paths=(
    /var/lib/dpkg/lock-frontend
    /var/lib/dpkg/lock
    /var/lib/apt/lists/lock
    /var/cache/apt/archives/lock
  )

  _info "Waiting for apt/dpkg to become free..."
  while true; do
    local busy=false
    if pgrep -x apt >/dev/null 2>&1 || \
       pgrep -x apt-get >/dev/null 2>&1 || \
       pgrep -x dpkg >/dev/null 2>&1 || \
       pgrep -x unattended-upgrade >/dev/null 2>&1 || \
       pgrep -x unattended-upgrades >/dev/null 2>&1; then
      busy=true
    fi

    for lock in "${lock_paths[@]}"; do
      if [[ -e "$lock" ]] && fuser "$lock" >/dev/null 2>&1; then
        busy=true
        break
      fi
    done

    if [[ "$busy" == "false" ]]; then
      return 0
    fi

    if (( waited >= timeout )); then
      _warn "apt is still busy after ${timeout}s."
      _warn "Please wait for the current package operation to finish, then rerun the installer."
      return 1
    fi

    sleep 5
    waited=$((waited + 5))
  done
}

# ── DNS Fallback ──────────────────────────────────────────────
# اگه یه دامنه resolve نشه، DNS سرورهای مختلف رو به resolv.conf اضافه می‌کنه
# و دوباره تست می‌کنه. این کار فقط یه بار انجام می‌شه.
_DNS_FIXED=false
_RESOLV_BACKUP=""

_fix_dns() {
  if [[ "$_DNS_FIXED" == "true" ]]; then
    return 0
  fi

  _warn "DNS resolution failed. Trying to apply fallback DNS servers..."

  local resolv_conf="/etc/resolv.conf"
  local fallback_dns=(
    "8.8.8.8"       # Google
    "8.8.4.4"       # Google secondary
    "1.1.1.1"       # Cloudflare
    "1.0.0.1"       # Cloudflare secondary
    "9.9.9.9"       # Quad9
    "208.67.222.222" # OpenDNS
  )

  # بک‌آپ از resolv.conf فعلی
  _RESOLV_BACKUP=$(cat "$resolv_conf" 2>/dev/null || echo "")
  _info "Current resolv.conf backed up."

  # اگه immutable flag داره، برش
  if command -v chattr &>/dev/null; then
    chattr -i "$resolv_conf" 2>/dev/null || true
  fi

  # ابتدا nameserverهای فعلی رو نگه بدار، بعد fallbackها رو اضافه کن
  {
    # nameserverهای فعلی (اگه وجود داشته باشند)
    grep "^nameserver" "$resolv_conf" 2>/dev/null || true
    # fallback DNS
    for dns in "${fallback_dns[@]}"; do
      echo "nameserver $dns"
    done
  } | awk '!seen[$0]++' > /tmp/_resolv_new.conf
  cp /tmp/_resolv_new.conf "$resolv_conf"

  _DNS_FIXED=true
  _ok "Fallback DNS applied: ${fallback_dns[*]}"
}

# بررسی resolve شدن یه دامنه و اگه نشد DNS fix بزن
_ensure_dns() {
  local host="${1:-download.docker.com}"
  if getent hosts "$host" &>/dev/null 2>&1 || \
     host "$host" &>/dev/null 2>&1 || \
     nslookup "$host" &>/dev/null 2>&1 || \
     dig +short "$host" &>/dev/null 2>&1; then
    return 0
  fi
  _fix_dns
  # بعد از fix یه بار دیگه تست کن (صبر کوتاه برای propagation)
  sleep 2
  if getent hosts "$host" &>/dev/null 2>&1 || \
     host "$host" &>/dev/null 2>&1 || \
     nslookup "$host" &>/dev/null 2>&1; then
    _ok "DNS now resolves $host ✓"
    return 0
  fi
  _warn "Still cannot resolve $host — will try anyway (some tools use their own resolver)"
  return 0  # ادامه بده حتی اگه resolve نشد — Docker/curl ممکنه کار کنن
}

# ── Docker install: Debian/Ubuntu ────────────────────────────
_install_docker_apt() {
  _info "Installing Docker via apt (Debian/Ubuntu)..."

  # بررسی DNS قبل از شروع دانلود
  _ensure_dns "download.docker.com"

  # رفع apt lock در صورت وجود
  _wait_for_apt 300 || return 1

  _retry 3 5 apt-get -o Dpkg::Lock::Timeout=120 update -qq
  _retry 3 5 apt-get -o Dpkg::Lock::Timeout=120 install -y -qq ca-certificates curl gnupg lsb-release

  install -m 0755 -d /etc/apt/keyrings

  # تشخیص distro واقعی (ubuntu یا debian) حتی اگر OS_ID چیز دیگه‌ای باشه
  local distro="ubuntu"
  if echo "${OS_ID} ${OS_LIKE}" | grep -qi "debian" && ! echo "${OS_ID} ${OS_LIKE}" | grep -qi "ubuntu"; then
    distro="debian"
  fi

  local gpg_url="https://download.docker.com/linux/${distro}/gpg"
  local codename
  codename="$(lsb_release -cs 2>/dev/null || echo "")"

  # اگه lsb_release نداد، از VERSION_CODENAME بگیر
  if [[ -z "$codename" ]] && [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    codename="${VERSION_CODENAME:-}"
  fi

  if [[ -z "$codename" ]]; then
    _err "Could not detect OS codename. Please install Docker manually."
    return 1
  fi

  _retry 3 5 bash -c "curl -fsSL '${gpg_url}' | gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg"
  chmod a+r /etc/apt/keyrings/docker.gpg

  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${distro} ${codename} stable" \
    > /etc/apt/sources.list.d/docker.list

  _wait_for_apt 300 || return 1
  _retry 3 5 apt-get -o Dpkg::Lock::Timeout=120 update -qq
  _retry 3 10 apt-get -o Dpkg::Lock::Timeout=120 install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
}

# ── Docker install: RHEL/CentOS/Alma/Rocky ───────────────────
_install_docker_rpm() {
  _info "Installing Docker via dnf/yum (RHEL/CentOS/Alma/Rocky)..."

  # بررسی DNS قبل از شروع دانلود
  _ensure_dns "download.docker.com"

  # حذف podman و buildah که با Docker تداخل دارند
  if command -v dnf &>/dev/null; then
    dnf remove -y podman buildah 2>/dev/null || true
    _retry 3 5 dnf install -y dnf-plugins-core
    _retry 3 5 dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    _retry 3 10 dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  else
    yum remove -y podman buildah 2>/dev/null || true
    _retry 3 5 yum install -y yum-utils
    _retry 3 5 yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    _retry 3 10 yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  fi
}

# ── Install Docker (main) ─────────────────────────────────────
_install_docker() {
  case "$PKG_MANAGER" in
    apt) _install_docker_apt ;;
    dnf|yum) _install_docker_rpm ;;
    *)
      _warn "Unknown package manager. Trying the official Docker convenience script..."
      _retry 3 10 bash -c "curl -fsSL https://get.docker.com | sh"
      ;;
  esac

  # فعال و start کردن service
  if command -v systemctl &>/dev/null; then
    systemctl enable docker --now 2>/dev/null || true
  elif command -v service &>/dev/null; then
    service docker start 2>/dev/null || true
  fi

  # تأیید نصب
  if ! command -v docker &>/dev/null; then
    _err "Docker installation failed. Please install manually: https://docs.docker.com/engine/install/"
    exit 1
  fi
  _ok "Docker installed: $(docker --version | cut -d' ' -f3 | tr -d ',')"
}

# ── Install docker-compose-plugin اگه نبود ───────────────────
_install_compose_plugin() {
  _warn "docker compose (v2) not found. Installing compose plugin..."
  case "$PKG_MANAGER" in
    apt)
      _wait_for_apt 300 || return 1
      _retry 3 5 apt-get -o Dpkg::Lock::Timeout=120 install -y -qq docker-compose-plugin
      ;;
    dnf)
      _retry 3 5 dnf install -y docker-compose-plugin
      ;;
    yum)
      _retry 3 5 yum install -y docker-compose-plugin
      ;;
    *)
      # نصب binary مستقیم از GitHub
      _info "Installing docker-compose binary from GitHub..."
      _ensure_dns "api.github.com"
      _ensure_dns "github.com"
      local compose_ver
      compose_ver=$(curl -fsSL https://api.github.com/repos/docker/compose/releases/latest \
        | grep '"tag_name"' | cut -d'"' -f4 2>/dev/null || echo "v2.27.0")
      local dest="/usr/local/lib/docker/cli-plugins"
      mkdir -p "$dest"
      _retry 3 10 curl -fsSL \
        "https://github.com/docker/compose/releases/download/${compose_ver}/docker-compose-$(uname -s)-$(uname -m)" \
        -o "${dest}/docker-compose"
      chmod +x "${dest}/docker-compose"
      ;;
  esac
}

_install_certbot_nginx() {
  _info "Installing certbot/nginx for automatic HTTPS..."
  case "$PKG_MANAGER" in
    apt)
      _wait_for_apt 300 || return 1
      _retry 3 5 apt-get -o Dpkg::Lock::Timeout=120 install -y -qq certbot nginx ;;
    dnf)
      _retry 3 5 dnf install -y certbot nginx ;;
    yum)
      _retry 3 5 yum install -y certbot nginx ;;
    *)
      _warn "Unknown package manager. Skipping certbot/nginx install."
      return 1 ;;
  esac
  command -v certbot &>/dev/null || return 1
  command -v nginx &>/dev/null || return 1
  if command -v systemctl &>/dev/null; then
    systemctl enable nginx --now 2>/dev/null || true
  fi
  _ok "certbot/nginx ready."
}

_write_nginx_proxy() {
  local domain="$1"
  local wh_port="${2:-9988}"
  local conf_file="/etc/nginx/conf.d/onebot-webhook.conf"
  cat > "$conf_file" <<NGINXCONF
# ONEBOT VPN Bot — Webhook HTTPS proxy
# Auto-generated by install.sh

server {
    listen 80;
    server_name ${domain};
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl;
    server_name ${domain};

    ssl_certificate     /etc/letsencrypt/live/${domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${domain}/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location /webhook/ {
        proxy_pass         http://127.0.0.1:${wh_port};
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_read_timeout 15s;
    }
    location /health {
        proxy_pass http://127.0.0.1:${wh_port}/health;
    }
    location / { return 404; }
}
NGINXCONF
  nginx -t >/dev/null 2>&1 || return 1
  systemctl reload nginx 2>/dev/null || nginx -s reload 2>/dev/null || true
  _ok "nginx reverse proxy configured for https://${domain}/webhook/"
}

_obtain_ssl_certificate() {
  local domain="$1"
  local email="$2"
  _info "Requesting Let’s Encrypt certificate for ${domain}..."
  local nginx_was_running=false
  if command -v systemctl &>/dev/null && systemctl is-active nginx &>/dev/null; then
    systemctl stop nginx 2>/dev/null || true
    nginx_was_running=true
  fi
  if certbot certonly --standalone --non-interactive --agree-tos --email "$email" --domain "$domain" >/tmp/onebot-certbot.log 2>&1; then
    [[ "$nginx_was_running" == "true" ]] && systemctl start nginx 2>/dev/null || true
    _ok "Certificate obtained for ${domain}"
    return 0
  fi
  [[ "$nginx_was_running" == "true" ]] && systemctl start nginx 2>/dev/null || true
  cat /tmp/onebot-certbot.log 2>/dev/null || true
  _err "certbot failed for ${domain}"
  return 1
}

_banner
echo -e "  This script will install ONEBOT VPN Bot on your server."
echo -e "  It will install Docker (if needed) and configure the bot."
echo ""
read -rp "  Press Enter to start or Ctrl+C to cancel..." _

# ════════════════════════════════════════════════════════════
#  STEP 1 — DNS & Docker
# ════════════════════════════════════════════════════════════
_step "Checking network & DNS..."
_detect_os
_info "Detected OS: ${OS_ID:-unknown} | Package manager: ${PKG_MANAGER}"

# بررسی DNS قبل از هر چیز — اگه مشکل داشت اتوماتیک fix می‌شه
_ensure_dns "download.docker.com"
_ensure_dns "registry-1.docker.io"

# ── نصب ابزارهای پایه‌ای مورد نیاز nexo-bot ──────────────────
_step "Installing base utilities..."
_install_base_utils() {
  local pkgs_apt=()
  local pkgs_rpm=()

  # openssl — برای نمایش تاریخ انقضا گواهی SSL در SSL Manager
  command -v openssl &>/dev/null || { pkgs_apt+=(openssl); pkgs_rpm+=(openssl); }
  # curl — برای health check و IP detection
  command -v curl    &>/dev/null || { pkgs_apt+=(curl);    pkgs_rpm+=(curl); }
  # git — برای clone در حالت streamed install و update flow
  command -v git     &>/dev/null || { pkgs_apt+=(git);     pkgs_rpm+=(git); }
  # getent — معمولاً هست، ولی بعضی minimal imageها ندارند
  command -v getent  &>/dev/null || { pkgs_apt+=(libc-bin); pkgs_rpm+=(glibc-common); }

  if [[ ${#pkgs_apt[@]} -eq 0 ]]; then
    _ok "Base utilities already present."
    return 0
  fi

  case "$PKG_MANAGER" in
    apt)
      _wait_for_apt 300 || return 1
      _retry 3 5 apt-get -o Dpkg::Lock::Timeout=120 install -y -qq "${pkgs_apt[@]}" ;;
    dnf)
      _retry 3 5 dnf install -y "${pkgs_rpm[@]}" ;;
    yum)
      _retry 3 5 yum install -y "${pkgs_rpm[@]}" ;;
  esac
  _ok "Base utilities installed."
}
_install_base_utils

_step "Checking Docker..."
if command -v docker &>/dev/null; then
  _ok "Docker already installed: $(docker --version | cut -d' ' -f3 | tr -d ',')"
else
  _install_docker
fi

# بررسی docker compose v2
if ! docker compose version &>/dev/null 2>&1; then
  _install_compose_plugin
  # بررسی مجدد
  if ! docker compose version &>/dev/null 2>&1; then
    _err "docker compose (v2) still not available after install attempt."
    _err "Please run manually: apt-get install docker-compose-plugin"
    exit 1
  fi
fi
_ok "Docker Compose v2: $(docker compose version --short 2>/dev/null || echo 'available')"

# اطمینان از اینکه Docker daemon در حال اجراست
if ! docker info &>/dev/null 2>&1; then
  _warn "Docker daemon not running. Starting..."
  if command -v systemctl &>/dev/null; then
    systemctl start docker
    sleep 3
  fi
  if ! docker info &>/dev/null 2>&1; then
    _err "Docker daemon failed to start. Check: systemctl status docker"
    exit 1
  fi
fi
_ok "Docker daemon is running."

# ════════════════════════════════════════════════════════════
#  STEP 2 — Choose install directory
# ════════════════════════════════════════════════════════════
_step "Install directory"

# مسیر اسکریپت — resolve symlink برای مقایسه دقیق
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

DEFAULT_DIR="/opt/onebot"
echo -e "   Default: ${CYAN}$DEFAULT_DIR${RESET}"
read -rp "   Press Enter to use default or type a different path: " INSTALL_DIR
INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_DIR}"
# normalize — trailing slash و .. حل بشه
INSTALL_DIR="$(realpath -m "$INSTALL_DIR" 2>/dev/null || echo "$INSTALL_DIR")"

# بررسی: آیا دایرکتوری از قبل وجود داره و از اسکریپت source نیست؟
# اگه INSTALL_DIR == SCRIPT_DIR یعنی اسکریپت همون‌جاست — نباید warning بده
_dir_existed=false
if [[ -d "$INSTALL_DIR" ]]; then
  # real path مقایسه کن تا symlink فریب نده
  _real_install="$(realpath "$INSTALL_DIR" 2>/dev/null || echo "$INSTALL_DIR")"
  _real_script="$(realpath "$SCRIPT_DIR"   2>/dev/null || echo "$SCRIPT_DIR")"

  if [[ "$_real_install" != "$_real_script" ]]; then
    # دایرکتوری از قبل وجود داشته و جای دیگه‌ایه — بپرس
    _dir_existed=true
    _warn "Directory already exists: $INSTALL_DIR"
    read -rp "   Continue anyway? Files will be updated. (y/N): " yn
    [[ "$yn" =~ ^[Yy]$ ]] || { echo "Cancelled."; exit 0; }
  fi
  # اگه همون مسیر script است، بدون سوال ادامه بده
fi

if [[ "$_dir_existed" == "false" ]] && [[ ! -d "$INSTALL_DIR" ]]; then
  mkdir -p "$INSTALL_DIR"
fi

_ok "Install directory: $INSTALL_DIR"

# ════════════════════════════════════════════════════════════
#  STEP 3 — Copy bot files
# ════════════════════════════════════════════════════════════
_step "Copying bot files..."

# SCRIPT_DIR already set in STEP 2
_real_install="$(realpath "$INSTALL_DIR" 2>/dev/null || echo "$INSTALL_DIR")"
_real_script="$(realpath "$SCRIPT_DIR"   2>/dev/null || echo "$SCRIPT_DIR")"

_streamed_install=false
if [[ "$SCRIPT_DIR" == /proc/* || "$SCRIPT_DIR" == /dev/fd/* || ! -f "$SCRIPT_DIR/main.py" || ! -f "$SCRIPT_DIR/README.md" ]]; then
  _streamed_install=true
fi

if [[ "$_streamed_install" == "true" ]]; then
  _info "Installer launched from a streamed source; cloning repository snapshot..."
  _tmp_clone_dir="$(mktemp -d -t onebot-clone-XXXXXX)"
  trap 'rm -rf "$_tmp_clone_dir"' EXIT
  git clone --depth 1 "$REPO_URL" "$_tmp_clone_dir"
  cp -a "$_tmp_clone_dir"/. "$INSTALL_DIR/"
  _ok "Repository cloned to $INSTALL_DIR"
elif [[ "$_real_script" != "$_real_install" ]]; then
  cp -a "$SCRIPT_DIR"/. "$INSTALL_DIR/"
  _ok "Files copied to $INSTALL_DIR"
else
  _ok "Files already in $INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# Install the management command as soon as the files are in place so an
# interrupted install still leaves a usable recovery entrypoint.
if [[ -f "$INSTALL_DIR/onebot" ]]; then
  chmod +x "$INSTALL_DIR/onebot" "$INSTALL_DIR/ONEBOT" "$INSTALL_DIR/nexo-bot" 2>/dev/null || true
  ln -sf "$INSTALL_DIR/onebot" /usr/local/bin/onebot
  ln -sf "$INSTALL_DIR/onebot" /usr/local/bin/ONEBOT
  ln -sf "$INSTALL_DIR/nexo-bot" /usr/local/bin/nexo-bot
fi

# ════════════════════════════════════════════════════════════
#  STEP 3.5 — Cleanup old conflicting containers/resources
# ════════════════════════════════════════════════════════════
_step "Cleaning up old containers (if any)..."

# لیست کانتینرهایی که ممکن است conflict داشته باشند
OLD_CONTAINERS=("vpn_bot" "vpn_postgres" "nexora_bot" "nexora_postgres")
for c in "${OLD_CONTAINERS[@]}"; do
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${c}$"; then
    _warn "Removing old container: $c"
    docker rm -f "$c" 2>/dev/null || true
  fi
done

# شبکه‌های قدیمی که ممکن است با project name اشتباه ساخته شده باشند
OLD_NETWORKS=("vpn_network" "nexora_network")
for n in "${OLD_NETWORKS[@]}"; do
  if docker network ls --format '{{.Name}}' 2>/dev/null | grep -q "^${n}$"; then
    _warn "Removing old network: $n (will be recreated)"
    docker network rm "$n" 2>/dev/null || true
  fi
done

_ok "Cleanup done."

# ════════════════════════════════════════════════════════════
#  STEP 4 — Configure .env
# ════════════════════════════════════════════════════════════
_step "Configuration"
_line

echo -e "   ${DIM}Fill in the required settings below.${RESET}"
echo -e "   ${DIM}You can change these later with: onebot → Configuration${RESET}"
echo ""

# ── Bot Token ────────────────────────────────────────────────
echo -e "   ${BOLD}Telegram Bot Token${RESET}"
echo -e "   ${DIM}Get from @BotFather → /newbot${RESET}"
_ask_s "BOT_TOKEN" BOT_TOKEN
while [[ -z "$BOT_TOKEN" ]]; do
  _warn "Bot token cannot be empty."
  _ask_s "BOT_TOKEN" BOT_TOKEN
done

# ── Admin IDs ────────────────────────────────────────────────
echo -e "\n   ${BOLD}Your Telegram ID (numeric)${RESET}"
echo -e "   ${DIM}Send any message to @userinfobot to get your ID${RESET}"
_ask "Admin ID(s) — comma-separated if multiple" ADMIN_IDS
while [[ -z "$ADMIN_IDS" ]]; do
  _warn "Admin ID cannot be empty."
  _ask "Admin ID(s)" ADMIN_IDS
done

# ── Admin Secret ─────────────────────────────────────────────
echo -e "\n   ${BOLD}Admin Secret Password${RESET}"
echo -e "   ${DIM}Used to login as admin in the bot: /admin_secret YOUR_PASSWORD${RESET}"
_ask_s "Admin secret password" ADMIN_SECRET
while [[ -z "$ADMIN_SECRET" ]]; do
  _warn "Admin secret cannot be empty."
  _ask_s "Admin secret password" ADMIN_SECRET
done

# ── 3X-UI Panel ─────────────────────────────────────────────
echo -e "\n   ${BOLD}3X-UI Panel URL${RESET}"
echo -e "   ${DIM}Example: https://your-server.com:8443/webBasePath${RESET}"
_ask "PANEL_URL" PANEL_URL
while [[ -z "$PANEL_URL" ]]; do
  _warn "Panel URL cannot be empty."
  _ask "PANEL_URL" PANEL_URL
done
# حذف trailing slash
PANEL_URL="${PANEL_URL%/}"

echo -e "\n   ${BOLD}3X-UI Panel Username${RESET}"
_ask "PANEL_USERNAME (default: admin)" PANEL_USERNAME
PANEL_USERNAME="${PANEL_USERNAME:-admin}"

echo -e "\n   ${BOLD}3X-UI Panel Password${RESET}"
_ask_s "PANEL_PASSWORD" PANEL_PASSWORD
while [[ -z "$PANEL_PASSWORD" ]]; do
  _warn "Panel password cannot be empty."
  _ask_s "PANEL_PASSWORD" PANEL_PASSWORD
done

echo -e "\n   ${BOLD}3X-UI Panel API Token${RESET}  ${DIM}(optional — bearer auth for newer panels)${RESET}"
echo -e "   ${DIM}If you have one, paste it here. Otherwise press Enter to use username/password login.${RESET}"
_ask_s "PANEL_API_TOKEN" PANEL_API_TOKEN

# ── Sub Port ─────────────────────────────────────────────────
echo -e "\n   ${BOLD}Subscription Link Port${RESET}  ${DIM}(optional)${RESET}"
echo -e "   ${DIM}If your panel serves /sub/ on a different port than the main panel.${RESET}"
echo -e "   ${DIM}Example: panel on 8443, sub links on 2096 → enter 2096${RESET}"
echo -e "   ${DIM}Press Enter to use the same port as PANEL_URL (default)${RESET}"
_ask "SUB_PORT (default: 2096)" SUB_PORT
SUB_PORT="${SUB_PORT:-2096}"
_ok "Sub port: ${SUB_PORT}"

# ── Public domain / SSL ───────────────────────────────────────
echo -e "\n   ${BOLD}Public domain / subdomain${RESET}  ${DIM}(optional but recommended for HTTPS webhooks)${RESET}"
echo -e "   ${DIM}Example: pay.example.com${RESET}"
echo -e "   ${DIM}If you skip this, installation continues without auto SSL setup.${RESET}"
_ask "Domain" PUBLIC_DOMAIN
PUBLIC_DOMAIN="${PUBLIC_DOMAIN// /}"
if [[ -n "$PUBLIC_DOMAIN" ]]; then
  _ok "Public domain: ${PUBLIC_DOMAIN}"
fi

# ── Database ─────────────────────────────────────────────────
echo -e "\n   ${BOLD}Database Type${RESET}"
echo -e "   [1] PostgreSQL  ${GREEN}(Recommended for production)${RESET}"
echo -e "   [2] SQLite      ${DIM}(Simple, for testing)${RESET}"
read -rp "   Choose (1/2, default: 2): " DB_CHOICE
DB_CHOICE="${DB_CHOICE:-2}"

USE_POSTGRES=false
if [[ "$DB_CHOICE" == "1" ]]; then
  echo -e "\n   ${BOLD}PostgreSQL Password${RESET}"
  echo -e "   ${DIM}This will be set for the vpn_bot database${RESET}"
  _ask_s "PostgreSQL password (default: auto-generated)" POSTGRES_PASSWORD
  if [[ -z "$POSTGRES_PASSWORD" ]]; then
    POSTGRES_PASSWORD="$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24)"
    _ok "Auto-generated PostgreSQL password (saved in .env)"
  fi
  DB_URL="postgresql+asyncpg://botuser:${POSTGRES_PASSWORD}@onebot_postgres/vpn_bot"
  USE_POSTGRES=true
  _ok "Using PostgreSQL"
else
  DB_URL="sqlite+aiosqlite:////app/data/bot_data.db"
  POSTGRES_PASSWORD=""
  _ok "Using SQLite"
fi

# ── NowPayments (optional) ───────────────────────────────────
echo -e "\n   ${BOLD}NOWPayments API Key${RESET}  ${DIM}(optional — press Enter to skip)${RESET}"
_ask_s "NOWPAYMENTS_API_KEY" NOWPAYMENTS_API_KEY

NOWPAYMENTS_IPN_SECRET=""
NOWPAYMENTS_IPN_URL=""
WEBHOOK_PORT="9988"

if [[ -n "$NOWPAYMENTS_API_KEY" ]]; then
  # ── IPN Secret ──────────────────────────────────────────────
  echo -e "\n   ${BOLD}NOWPayments IPN Secret Key${RESET}  ${DIM}(برای تأیید خودکار پرداخت)${RESET}"
  echo -e "   ${DIM}از داشبورد NOWPayments → Settings → Payments → IPN Secret Key بسازید${RESET}"
  echo -e "   ${DIM}اگه ندارید Enter بزنید — کاربر باید دستی «بررسی پرداخت» بزند${RESET}"
  _ask_s "NOWPAYMENTS_IPN_SECRET" NOWPAYMENTS_IPN_SECRET

  if [[ -n "$NOWPAYMENTS_IPN_SECRET" ]]; then
    # ── Webhook Port ─────────────────────────────────────────
    echo -e "\n   ${BOLD}Webhook Port${RESET}  ${DIM}(پورت HTTP برای دریافت IPN از NOWPayments)${RESET}"
    echo -e "   ${DIM}پیش‌فرض: 9988 — با 3X-UI و nginx تداخل ندارد${RESET}"
    echo -e "   ${DIM}اگه 9988 اشغاله، عدد دیگه‌ای بزنید (مثلاً 7777)${RESET}"
    echo -e "   ${DIM}بررسی پورت آزاد: ss -tlnp | grep 9988${RESET}"

    _ask_port_ok=false
    while [[ "$_ask_port_ok" == "false" ]]; do
      _ask "Webhook port (default: 9988)" WEBHOOK_PORT
      WEBHOOK_PORT="${WEBHOOK_PORT:-9988}"

      # بررسی اینکه پورت عدد معتبره
      if ! [[ "$WEBHOOK_PORT" =~ ^[0-9]+$ ]] || (( WEBHOOK_PORT < 1024 || WEBHOOK_PORT > 65535 )); then
        _warn "پورت باید بین 1024 تا 65535 باشد."
        WEBHOOK_PORT="9988"
        continue
      fi

      # بررسی آزاد بودن پورت
      if ss -tlnp 2>/dev/null | grep -q ":${WEBHOOK_PORT} " || \
         netstat -tlnp 2>/dev/null | grep -q ":${WEBHOOK_PORT} "; then
        _warn "پورت ${WEBHOOK_PORT} در حال حاضر اشغال است!"
        echo -e "   ${DIM}پورت‌های اشغال: $(ss -tlnp 2>/dev/null | awk '{print $4}' | grep -oE '[0-9]+$' | sort -u | tr '\n' ' ')${RESET}"
        read -rp "   پورت دیگری وارد کنید: " WEBHOOK_PORT
        WEBHOOK_PORT="${WEBHOOK_PORT:-9988}"
      else
        _ok "پورت ${WEBHOOK_PORT} آزاد است ✓"
        _ask_port_ok=true
      fi
    done

    # ── IPN URL ──────────────────────────────────────────────
    if [[ -n "$PUBLIC_DOMAIN" ]]; then
      NOWPAYMENTS_IPN_URL="https://${PUBLIC_DOMAIN}/webhook/nowpayments"
      _ok "IPN URL: ${NOWPAYMENTS_IPN_URL}"
    else
      echo -e "\n   ${BOLD}آدرس سرور شما (برای IPN URL)${RESET}"
      echo -e "   ${DIM}فقط دامنه یا IP — بدون پورت و path${RESET}"
      echo -e "   ${DIM}مثال: your-domain.com  یا  1.2.3.4${RESET}"
      _ask "Domain or IP" _SERVER_HOST
      if [[ -n "$_SERVER_HOST" ]]; then
        NOWPAYMENTS_IPN_URL="http://${_SERVER_HOST}:${WEBHOOK_PORT}/webhook/nowpayments"
        _ok "IPN URL: ${NOWPAYMENTS_IPN_URL}"
        echo -e "   ${DIM}اگه HTTPS دارید، بعد از نصب در .env تغییر دهید${RESET}"
      else
        echo -e "\n   ${BOLD}Webhook URL for payment notifications${RESET}  ${DIM}(optional)${RESET}"
        echo -e "   ${DIM}Example: https://your-domain.com/webhook/nowpayments${RESET}"
        _ask "NOWPAYMENTS_IPN_URL" NOWPAYMENTS_IPN_URL
      fi
    fi
  fi
else
  echo -e "\n   ${BOLD}Webhook URL for payment notifications${RESET}  ${DIM}(optional)${RESET}"
  if [[ -n "$PUBLIC_DOMAIN" ]]; then
    NOWPAYMENTS_IPN_URL="https://${PUBLIC_DOMAIN}/webhook/nowpayments"
    _ok "IPN URL: ${NOWPAYMENTS_IPN_URL}"
  else
    echo -e "   ${DIM}Example: https://your-domain.com/webhook/nowpayments${RESET}"
    _ask "NOWPAYMENTS_IPN_URL" NOWPAYMENTS_IPN_URL
  fi
fi

# ── MaxelPay (optional) ──────────────────────────────────────
echo ""
_line
echo -e "\n   ${BOLD}MaxelPay Crypto Gateway${RESET}  ${DIM}(optional — alternative to NOWPayments)${RESET}"
echo -e "   ${DIM}Get your API key from: https://maxelpay.com${RESET}"
echo -e "   ${DIM}Press Enter to skip${RESET}"
_ask_s "MAXELPAY_API_KEY" MAXELPAY_API_KEY
echo -e "\n   ${BOLD}MaxelPay Webhook Secret${RESET}  ${DIM}(optional but recommended)${RESET}"
echo -e "   ${DIM}If your MaxelPay dashboard gives you a webhook secret, paste it here.${RESET}"
_ask_s "MAXELPAY_WEBHOOK_SECRET" MAXELPAY_WEBHOOK_SECRET

MAXELPAY_WEBHOOK_URL=""
BOT_USERNAME=""

if [[ -n "$MAXELPAY_API_KEY" ]]; then
  # ── MaxelPay Webhook URL ─────────────────────────────────
  echo -e "\n   ${BOLD}MaxelPay Webhook URL${RESET}  ${DIM}(باید HTTPS و قابل دسترس از اینترنت باشد)${RESET}"
  echo -e "   ${DIM}MaxelPay این آدرس را بعد از پرداخت فراخوانی می‌کند${RESET}"
  echo -e "   ${DIM}مثال: https://your-domain.com:9988/webhook/maxelpay${RESET}"
  echo -e "   ${DIM}اگه NOWPAYMENTS_IPN_URL تنظیم کردید، همان دامنه/پورت را استفاده کنید${RESET}"

  if [[ -n "$PUBLIC_DOMAIN" ]]; then
    _DEFAULT_MAXEL_URL="https://${PUBLIC_DOMAIN}/webhook/maxelpay"
    echo -e "   ${DIM}پیشنهاد: ${_DEFAULT_MAXEL_URL}${RESET}"
    MAXELPAY_WEBHOOK_URL="${_DEFAULT_MAXEL_URL}"
  elif [[ -n "$_SERVER_HOST" ]]; then
    _DEFAULT_MAXEL_URL="https://${_SERVER_HOST}:${WEBHOOK_PORT:-9988}/webhook/maxelpay"
    echo -e "   ${DIM}پیشنهاد: ${_DEFAULT_MAXEL_URL}${RESET}"
    _ask "MAXELPAY_WEBHOOK_URL (Enter for suggested)" _MAXEL_WH_INPUT
    MAXELPAY_WEBHOOK_URL="${_MAXEL_WH_INPUT:-$_DEFAULT_MAXEL_URL}"
  else
    _ask "MAXELPAY_WEBHOOK_URL" MAXELPAY_WEBHOOK_URL
  fi

  if [[ -n "$MAXELPAY_WEBHOOK_URL" ]]; then
    _ok "MaxelPay webhook URL: ${MAXELPAY_WEBHOOK_URL}"
  else
    _warn "Webhook URL خالی ماند — تأیید خودکار پرداخت MaxelPay غیرفعال است."
  fi

  # ── Bot Username (برای لینک بازگشت) ──────────────────────
  echo -e "\n   ${BOLD}Bot Username${RESET}  ${DIM}(بدون @ — برای لینک بازگشت بعد از پرداخت)${RESET}"
  echo -e "   ${DIM}مثال: اگه ربات @myvpn_bot است، فقط myvpn_bot وارد کنید${RESET}"
  echo -e "   ${DIM}Enter برای رد کردن${RESET}"
  _ask "BOT_USERNAME" BOT_USERNAME
  BOT_USERNAME="${BOT_USERNAME:-}"
  if [[ -n "$BOT_USERNAME" ]]; then
    BOT_USERNAME="${BOT_USERNAME#@}"   # اگه @ اضافه زد، برش بزن
    _ok "Bot username: ${BOT_USERNAME}"
  fi
else
  _ok "MaxelPay skipped."
fi

WEB_ADMIN_ENABLED=true
WEB_ADMIN_USERNAME="admin"
WEB_ADMIN_PASSWORD="admin"
WEB_ADMIN_COOKIE_SECRET="$(_rand_secret 32)"

# ════════════════════════════════════════════════════════════
#  STEP 5 — Write .env
# ════════════════════════════════════════════════════════════
_step "Writing .env file..."

cat > "$INSTALL_DIR/.env" <<EOF
# ===== ONEBOT VPN Bot — Auto-generated by installer =====
# Edit anytime with: onebot → Configuration → Edit .env

# Telegram Bot
BOT_TOKEN=${BOT_TOKEN}

# Admin
ADMIN_IDS=${ADMIN_IDS}
ADMIN_SECRET=${ADMIN_SECRET}

# 3X-UI Panel
PANEL_URL=${PANEL_URL}
PANEL_USERNAME=${PANEL_USERNAME}
PANEL_PASSWORD=${PANEL_PASSWORD}
PANEL_API_TOKEN=${PANEL_API_TOKEN}
SUB_PORT=${SUB_PORT}

# Database
DB_URL=${DB_URL}
EOF

if [[ "$USE_POSTGRES" == "true" ]]; then
  echo "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" >> "$INSTALL_DIR/.env"
fi

cat >> "$INSTALL_DIR/.env" <<'EOF'

# Defaults
DEFAULT_SUBSCRIPTION_DAYS=30
DEFAULT_TRAFFIC_GB=0

# Crypto Payments (optional)
EOF

echo "NOWPAYMENTS_API_KEY=${NOWPAYMENTS_API_KEY:-}" >> "$INSTALL_DIR/.env"
echo "NOWPAYMENTS_IPN_SECRET=${NOWPAYMENTS_IPN_SECRET:-}" >> "$INSTALL_DIR/.env"
echo "NOWPAYMENTS_IPN_URL=${NOWPAYMENTS_IPN_URL:-}" >> "$INSTALL_DIR/.env"
echo "WEBHOOK_PORT=${WEBHOOK_PORT:-9988}" >> "$INSTALL_DIR/.env"
echo "WEB_ADMIN_ENABLED=true" >> "$INSTALL_DIR/.env"
echo "WEB_ADMIN_USERNAME=${WEB_ADMIN_USERNAME:-admin}" >> "$INSTALL_DIR/.env"
echo "WEB_ADMIN_PASSWORD=${WEB_ADMIN_PASSWORD:-admin}" >> "$INSTALL_DIR/.env"
echo "WEB_ADMIN_COOKIE_SECRET=${WEB_ADMIN_COOKIE_SECRET:-$(_rand_secret 32)}" >> "$INSTALL_DIR/.env"

cat >> "$INSTALL_DIR/.env" <<'EOF'
NOWPAYMENTS_PAY_CURRENCY=usdttrc20
INVOICE_EXPIRE_MINUTES=30

# MaxelPay (optional — alternative crypto gateway)
EOF

echo "MAXELPAY_API_KEY=${MAXELPAY_API_KEY:-}" >> "$INSTALL_DIR/.env"
echo "MAXELPAY_WEBHOOK_URL=${MAXELPAY_WEBHOOK_URL:-}" >> "$INSTALL_DIR/.env"
echo "MAXELPAY_WEBHOOK_SECRET=${MAXELPAY_WEBHOOK_SECRET:-}" >> "$INSTALL_DIR/.env"
echo "BOT_USERNAME=${BOT_USERNAME:-}" >> "$INSTALL_DIR/.env"

cat >> "$INSTALL_DIR/.env" <<'EOF'

# Misc
LOG_LEVEL=INFO
LOG_FILE=logs/bot.log
EOF

chmod 600 "$INSTALL_DIR/.env"
_ok ".env written and secured (chmod 600)"

# ════════════════════════════════════════════════════════════
#  STEP 6 — Build & Start
# ════════════════════════════════════════════════════════════
_step "Building Docker image..."
cd "$INSTALL_DIR"

# Build فقط سرویس bot
docker compose build --no-cache bot
_ok "Image built."

_step "Starting services..."

if [[ "$USE_POSTGRES" == "true" ]]; then
  # PostgreSQL: بالا آوردن db و bot با پروفایل postgres
  docker compose --profile postgres up -d
  _ok "PostgreSQL + Bot started."

  # صبر برای آماده شدن PostgreSQL
  echo -e "\n   ${DIM}Waiting for PostgreSQL to be ready...${RESET}"
  for i in $(seq 1 20); do
    if docker compose --profile postgres exec -T db pg_isready -U botuser -d vpn_bot &>/dev/null; then
      _ok "PostgreSQL is ready."
      break
    fi
    sleep 2
    echo -ne "   ${DIM}... ($i/20)${RESET}\r"
  done
else
  # SQLite: فقط bot بدون profile
  docker compose up -d bot
  _ok "Bot started (SQLite mode)."
fi

if [[ -n "$PUBLIC_DOMAIN" ]]; then
  echo ""
  _step "Configuring HTTPS reverse proxy"
  if _install_certbot_nginx; then
    if _obtain_ssl_certificate "$PUBLIC_DOMAIN" "noreply@${PUBLIC_DOMAIN}"; then
      _write_nginx_proxy "$PUBLIC_DOMAIN" "${WEBHOOK_PORT:-9988}" || _warn "nginx proxy setup skipped."
      echo ""
      _ok "Public webhooks prepared for https://${PUBLIC_DOMAIN}/webhook/"
      echo -e "   ${DIM}NOWPayments: ${NOWPAYMENTS_IPN_URL}${RESET}"
      [[ -n "$MAXELPAY_WEBHOOK_URL" ]] && echo -e "   ${DIM}MaxelPay: ${MAXELPAY_WEBHOOK_URL}${RESET}"
    else
      _warn "SSL certificate setup failed. You can retry later with onebot → SSL."
    fi
  else
    _warn "certbot/nginx could not be installed automatically."
  fi
fi

# ════════════════════════════════════════════════════════════
#  STEP 7 — Install onebot command
# ════════════════════════════════════════════════════════════
_step "Installing onebot command..."

chmod +x "$INSTALL_DIR/onebot" "$INSTALL_DIR/ONEBOT"
chmod +x "$INSTALL_DIR/nexo-bot"
# ذخیره متغیر USE_POSTGRES برای onebot
echo "USE_POSTGRES=${USE_POSTGRES}" >> "$INSTALL_DIR/.env"

ln -sf "$INSTALL_DIR/onebot" /usr/local/bin/onebot
ln -sf "$INSTALL_DIR/onebot" /usr/local/bin/ONEBOT
ln -sf "$INSTALL_DIR/nexo-bot" /usr/local/bin/nexo-bot
_ok "Command installed: onebot / ONEBOT"

# ════════════════════════════════════════════════════════════
#  DONE
# ════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}${BOLD}"
echo "  ╔═══════════════════════════════════════════════════╗"
echo "  ║           ✔  INSTALLATION COMPLETE!               ║"
echo "  ╚═══════════════════════════════════════════════════╝"
echo -e "${RESET}"
echo -e "  ${BOLD}Bot installed at:${RESET}  $INSTALL_DIR"
echo -e "  ${BOLD}Manage with:${RESET}       ${CYAN}onebot${RESET}"
echo ""
echo -e "  ${DIM}Next steps:${RESET}"
echo -e "  1. Open Telegram and send a message to your bot"
echo -e "  2. Login as admin: send  /admin_secret ${ADMIN_SECRET:0:2}****"
echo -e "     (full command is in your .env: ADMIN_SECRET)"
echo -e "  3. Use ${CYAN}onebot${RESET} anytime to manage the bot"
echo ""
echo -e "  ${DIM}Credits: github.com/sariyan-0${RESET}"
echo ""
