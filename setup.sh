#!/bin/bash
# ============================================================
# setup.sh — اسکریپت راه‌اندازی ربات VPN تلگرام
# اجرا: bash setup.sh
# ============================================================

set -e
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${GREEN}[✓]${NC} $1"; }
warning() { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }

echo ""
echo "🤖 راه‌اندازی ربات VPN تلگرام"
echo "================================"
echo ""

# ── بررسی Docker ────────────────────────────────
if ! command -v docker &>/dev/null; then
    warning "Docker نصب نیست. در حال نصب..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    info "Docker نصب شد"
fi

if ! command -v docker &>/dev/null || ! docker compose version &>/dev/null 2>&1; then
    if ! command -v docker-compose &>/dev/null; then
        warning "Docker Compose نصب نیست. در حال نصب..."
        sudo apt-get install -y docker-compose-plugin 2>/dev/null || \
        sudo pip3 install docker-compose
    fi
fi
info "Docker: $(docker --version)"

# ── ساخت فایل .env ──────────────────────────────
if [ ! -f ".env" ]; then
    if [ ! -f ".env.example" ]; then
        error "فایل .env.example پیدا نشد!"
    fi
    cp .env.example .env
    warning "فایل .env از .env.example کپی شد"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  لطفاً فایل .env را ویرایش کنید:"
    echo "  nano .env"
    echo ""
    echo "  حداقل این فیلدها را پر کنید:"
    echo "  • BOT_TOKEN"
    echo "  • PANEL_URL"
    echo "  • PANEL_USERNAME"
    echo "  • PANEL_PASSWORD"
    echo "  • ADMIN_IDS"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    read -p "آیا می‌خواهید الان .env را ویرایش کنید؟ [Y/n] " EDIT_ENV
    if [[ "$EDIT_ENV" != "n" && "$EDIT_ENV" != "N" ]]; then
        ${EDITOR:-nano} .env
    fi
else
    info "فایل .env موجود است"
    # ── مهاجرت .env قدیمی — اضافه کردن متغیرهای جدید ──
    MIGRATED=0
    if ! grep -q "MAXELPAY_API_KEY" .env; then
        echo "" >> .env
        echo "# ===== پرداخت کریپتو (MaxelPay) — اضافه شده در نسخه v15 =====" >> .env
        echo "# اگر خالی باشد، MaxelPay غیرفعال است" >> .env
        echo "# برای فعال‌سازی: ادمین → روش‌های پرداخت → درگاه را به MaxelPay تغییر دهید" >> .env
        echo "MAXELPAY_API_KEY=" >> .env
        echo "MAXELPAY_WEBHOOK_URL=https://your-domain.com:9988/webhook/maxelpay" >> .env
        echo "BOT_USERNAME=your_bot_username" >> .env
        MIGRATED=1
    fi
    if [[ $MIGRATED -eq 1 ]]; then
        warning "متغیرهای MaxelPay به .env اضافه شدند — در صورت نیاز مقادیر را تنظیم کنید"
    fi
fi

# ── بررسی BOT_TOKEN ─────────────────────────────
BOT_TOKEN=$(grep -E "^BOT_TOKEN=" .env | cut -d= -f2 | tr -d '"' | tr -d "'")
if [[ -z "$BOT_TOKEN" || "$BOT_TOKEN" == "your_bot_token_from_botfather" ]]; then
    error "BOT_TOKEN در .env تنظیم نشده! ابتدا .env را ویرایش کنید."
fi
info "BOT_TOKEN تنظیم شده است"

# ── بررسی PANEL_URL ─────────────────────────────
PANEL_URL=$(grep -E "^PANEL_URL=" .env | cut -d= -f2 | tr -d '"' | tr -d "'")
if [[ -z "$PANEL_URL" || "$PANEL_URL" == "https://your-server.com:54321" ]]; then
    error "PANEL_URL در .env تنظیم نشده!"
fi
info "PANEL_URL: $PANEL_URL"

# ── درگاه پرداخت کریپتو ──────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  💳 تنظیم درگاه پرداخت کریپتو"
echo "  (می‌توانید بعداً از پنل ادمین تغییر دهید)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  1) NOWPayments"
echo "  2) MaxelPay"
read -p "  انتخاب [1/2، پیش‌فرض=1]: " GW_CHOICE

if [[ "$GW_CHOICE" == "2" ]]; then
    read -p "  MAXELPAY_API_KEY: " MAXELPAY_KEY
    if [[ -n "$MAXELPAY_KEY" ]]; then
        sed -i "s|^MAXELPAY_API_KEY=.*|MAXELPAY_API_KEY=$MAXELPAY_KEY|" .env
        info "MaxelPay API Key تنظیم شد"
    fi
    read -p "  MAXELPAY_WEBHOOK_URL (مثال: https://your-domain.com:9988/webhook/maxelpay): " MAXELPAY_WH
    if [[ -n "$MAXELPAY_WH" ]]; then
        sed -i "s|^MAXELPAY_WEBHOOK_URL=.*|MAXELPAY_WEBHOOK_URL=$MAXELPAY_WH|" .env
        info "MaxelPay Webhook URL تنظیم شد"
    fi
    read -p "  BOT_USERNAME (یوزرنیم ربات بدون @): " BOT_UNAME
    if [[ -n "$BOT_UNAME" ]]; then
        sed -i "s|^BOT_USERNAME=.*|BOT_USERNAME=$BOT_UNAME|" .env
        info "Bot username تنظیم شد"
    fi
else
    NOWPAY_KEY=$(grep -E "^NOWPAYMENTS_API_KEY=" .env | cut -d= -f2 | tr -d '"' | tr -d "'")
    if [[ -z "$NOWPAY_KEY" || "$NOWPAY_KEY" == "your_nowpayments_api_key" ]]; then
        read -p "  NOWPAYMENTS_API_KEY (خالی = sandbox): " NOWPAY_INPUT
        if [[ -n "$NOWPAY_INPUT" ]]; then
            sed -i "s|^NOWPAYMENTS_API_KEY=.*|NOWPAYMENTS_API_KEY=$NOWPAY_INPUT|" .env
            info "NOWPayments API Key تنظیم شد"
        else
            warning "NOWPayments API Key خالی — حالت sandbox فعال است"
        fi
    else
        info "NOWPayments API Key از قبل تنظیم شده"
    fi
fi

# ── ساخت پوشه‌های لازم ──────────────────────────
mkdir -p logs
touch bot_data.db
info "پوشه‌ها آماده شدند"

# ── Build و Start ────────────────────────────────
echo ""
info "در حال build کردن Docker image..."
docker compose build --no-cache

echo ""
info "در حال راه‌اندازی سرویس‌ها..."
docker compose up -d

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}  ✅ ربات با موفقیت راه‌اندازی شد!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  دستورات مفید:"
echo "  • مشاهده لاگ:        docker compose logs -f bot"
echo "  • وضعیت:             docker compose ps"
echo "  • توقف:              docker compose down"
echo "  • اعمال تغییر .env:  docker compose up -d   ← بعد از ویرایش .env"
echo ""
echo "  ⚠️  مهم: بعد از تغییر .env حتماً «docker compose up -d» اجرا کنید"
echo "          «docker compose restart» تغییرات .env را اعمال نمی‌کند"
echo ""
info "لاگ‌های زنده:"
docker compose logs -f bot
