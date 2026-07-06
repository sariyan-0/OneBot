#!/bin/bash
# migrate_env.sh — اضافه کردن متغیرهای جدید به .env موجود
# اجرا: bash migrate_env.sh
# ============================================================

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[✓]${NC} $1"; }
warning() { echo -e "${YELLOW}[!]${NC} $1"; }

ENV_FILE=".env"

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ فایل .env پیدا نشد. ابتدا setup.sh را اجرا کنید."
    exit 1
fi

echo ""
echo "🔄 مهاجرت .env به نسخه v15"
echo "================================"

CHANGED=0

# ── MaxelPay API Key ─────────────────────────────
if ! grep -q "MAXELPAY_API_KEY" "$ENV_FILE"; then
    echo "" >> "$ENV_FILE"
    echo "# ===== پرداخت کریپتو (MaxelPay) — نسخه v15 =====" >> "$ENV_FILE"
    echo "# درگاه انتخابی را از پنل ادمین → روش‌های پرداخت تنظیم کنید" >> "$ENV_FILE"
    echo "MAXELPAY_API_KEY=" >> "$ENV_FILE"
    echo "MAXELPAY_WEBHOOK_URL=https://your-domain.com:9988/webhook/maxelpay" >> "$ENV_FILE"
    echo "BOT_USERNAME=" >> "$ENV_FILE"
    info "متغیرهای MaxelPay اضافه شدند"
    CHANGED=1
else
    info "MAXELPAY_API_KEY از قبل وجود دارد"
fi

# ── پرسیدن مقادیر MaxelPay ──────────────────────
echo ""
read -p "آیا می‌خواهید MaxelPay را همین الان تنظیم کنید؟ [y/N] " SETUP_MAXEL
if [[ "$SETUP_MAXEL" == "y" || "$SETUP_MAXEL" == "Y" ]]; then
    read -p "  MAXELPAY_API_KEY: " MAXELPAY_KEY
    if [[ -n "$MAXELPAY_KEY" ]]; then
        sed -i "s|^MAXELPAY_API_KEY=.*|MAXELPAY_API_KEY=$MAXELPAY_KEY|" "$ENV_FILE"
        info "MAXELPAY_API_KEY تنظیم شد"
    fi

    read -p "  MAXELPAY_WEBHOOK_URL [https://your-domain.com:9988/webhook/maxelpay]: " MAXELPAY_WH
    if [[ -n "$MAXELPAY_WH" ]]; then
        sed -i "s|^MAXELPAY_WEBHOOK_URL=.*|MAXELPAY_WEBHOOK_URL=$MAXELPAY_WH|" "$ENV_FILE"
        info "MAXELPAY_WEBHOOK_URL تنظیم شد"
    fi

    read -p "  BOT_USERNAME (یوزرنیم ربات بدون @): " BOT_UNAME
    if [[ -n "$BOT_UNAME" ]]; then
        sed -i "s|^BOT_USERNAME=.*|BOT_USERNAME=$BOT_UNAME|" "$ENV_FILE"
        info "BOT_USERNAME تنظیم شد"
    fi
fi

echo ""
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ $CHANGED -eq 1 ]]; then
    warning "فایل .env آپدیت شد"
else
    info "فایل .env نیاز به تغییر نداشت"
fi
echo ""
echo "  برای اعمال تغییرات .env اجرا کنید:"
echo "  docker compose up -d"
echo ""
echo "  ⚠️  توجه: «docker compose restart» تغییرات .env را اعمال نمی‌کند"
echo "          همیشه از «docker compose up -d» استفاده کنید"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
