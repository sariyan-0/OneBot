# ONEBOT VPN Bot

ربات تلگرام فروش اشتراک VPN — طراحی‌شده برای پنل **3X-UI**

[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://python.org)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-2CA5E0)](https://docs.aiogram.dev)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED)](https://docker.com)
[![License](https://img.shields.io/badge/License-ONEBOT--Attribution-purple)](LICENSE)

---

> **توجه مهم:** این ربات فقط با پنل [3X-UI](https://github.com/MHSanaei/3x-ui) سازگار است.
> پیش از نصب مطمئن شوید پنل 3X-UI روی سرور شما نصب و در حال اجراست.

---

## امکانات

**فروش و پرداخت**
- فروش پلن‌های حجمی و نامحدود با نمایش قیمت به دلار و تومان
- پرداخت کارت به کارت با تأیید دستی ادمین
- پرداخت رمزارز USDT از طریق **MaxelPay** *(توصیه‌شده)* یا NOWPayments
- تأیید خودکار پرداخت از طریق webhook — بدون دخالت دستی
- پشتیبانی از کد تخفیف با درصد، تعداد مجاز و تاریخ انقضا
- اشتراک تست رایگان — یک‌بار برای هر کاربر (قابل تنظیم از پنل ادمین)

**مدیریت کاربران**
- سیستم دعوت دوستان با لینک اختصاصی و پاداش خودکار
- تیکت پشتیبانی با امکان پاسخ‌دهی ادمین
- ارسال پیام دسته‌جمعی به همه کاربران
- هشدار خودکار انقضا: ۷ روز، ۳ روز و ۱ روز قبل

**پنل ادمین**
- رابط مدیریت وب به Next.js منتقل شده و از مسیر `/admin` سرو می‌شود
- مدیریت کامل پلن‌ها، اینباندها، کدهای تخفیف
- آمار درآمد، کاربران، اشتراک‌ها و تراکنش‌ها
- تأیید/رد پرداخت‌های کارت به کارت
- پشتیبان‌گیری خودکار شبانه از دیتابیس، پنل، گواهی SSL و تنظیمات
- مشاهده وضعیت سرور و لاگ Xray مستقیم از ربات

**ابزار مدیریت CLI (`onebot`)**
- راه‌اندازی، توقف و ری‌استارت ربات
- ویرایش `.env` با اعمال خودکار تغییرات
- **SSL Manager** — گرفتن گواهی Let's Encrypt و تنظیم nginx proxy
- **Webhook Manager** — مدیریت و تنظیم webhook URL های درگاه‌ها
- **Backup & Restore** — بک‌آپ کامل (دیتابیس + SSL + `.env` + nginx)

---

## پیش‌نیازها

| مورد | جزئیات |
|---|---|
| سرور | لینوکس — حداقل 1 گیگابایت RAM |
| پنل | [3X-UI](https://github.com/MHSanaei/3x-ui) نصب و فعال |
| توکن ربات | دریافت از [@BotFather](https://t.me/BotFather) |
| Docker | در صورت نبود توسط اسکریپت نصب می‌شود |

سیستم‌عامل‌های پشتیبانی‌شده:
```
Ubuntu 20.04 / 22.04 / 24.04
Debian 10 / 11 / 12
CentOS / RHEL / AlmaLinux / Rocky Linux 8 / 9
```

---

## نصب

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/sariyan-0/OneBot/main/install.sh)
```

اسکریپت نصب به‌صورت تعاملی تمام تنظیمات را می‌پرسد و Docker را نیز در صورت نیاز نصب می‌کند.
پس از نصب، پنل وب در `https://YOUR_DOMAIN/admin` و وبهوک‌ها در `/webhook/*` در دسترس هستند.

---

## تنظیمات `.env`

**حداقل تنظیمات لازم:**

```env
BOT_TOKEN=توکن_ربات
ADMIN_IDS=آیدی_عددی_تلگرام
ADMIN_SECRET=رمز_ورود_ادمین
WEB_ADMIN_USERNAME=admin
WEB_ADMIN_PASSWORD=admin

PANEL_URL=https://your-server.com:8443/webBasePath
PANEL_USERNAME=admin
PANEL_PASSWORD=رمز_پنل
SUB_PORT=2096
```

فایل کامل: [`.env.example`](.env.example)

---

## مدیریت

```bash
onebot
```

**ورود به پنل ادمین در تلگرام:**
```
/admin_secret رمز_شما
```

---

## ساختار پروژه

```
bot/
├── install.sh              اسکریپت نصب خودکار
├── onebot                  ابزار مدیریت CLI
├── docker-compose.yml
├── Dockerfile.bot
├── web-panel/              Next.js admin panel
├── config.py               تنظیمات مرکزی (Pydantic v2)
├── main.py                 نقطه ورود + APScheduler
├── database/               SQLAlchemy 2.0 Async
├── handlers/               aiogram routers
├── services/               xui_api · payments · subscription · maxelpay
├── keyboards/              InlineKeyboard و ReplyKeyboard
├── middlewares/            Rate limiter
└── utils/                  QR Code generator
```

---

## روش‌های پرداخت

| روش | وضعیت | توضیح |
|---|---|---|
| کارت به کارت | ✅ فعال | تأیید دستی توسط ادمین |
| **MaxelPay** | ✅ **توصیه‌شده** | کریپتو بدون KYC — تأیید خودکار webhook |
| NOWPayments | ✅ فعال | کریپتو — تأیید خودکار webhook |

### چرا MaxelPay؟

[MaxelPay](https://maxelpay.com) یک درگاه پرداخت رمزارز بدون نیاز به KYC است که:
- **بدون احراز هویت** — برای کاربران ایرانی مناسب‌تر است
- **چند شبکه** — BSC، Ethereum، TRON و غیره
- **تأیید سریع** — webhook بلافاصله بعد از تأیید blockchain ارسال می‌شود
- **رایگان** — بدون کارمزد اضافی برای راه‌اندازی

**برای راه‌اندازی MaxelPay:**
1. در [maxelpay.com](https://maxelpay.com) ثبت‌نام کنید
2. از داشبورد → API Keys → Create API Key
3. اسکریپت نصب همه چیز را می‌پرسد، یا از `onebot → Webhook Manager` استفاده کنید

---

## راهنمای گام‌به‌گام

### گام ۱ — گرفتن توکن ربات از BotFather

1. در تلگرام [@BotFather](https://t.me/BotFather) را باز کنید
2. دستور `/newbot` را ارسال کنید
3. یک نام و username برای ربات وارد کنید (username باید به `bot` ختم شود)
4. توکن دریافتی را در `.env` به عنوان `BOT_TOKEN` وارد کنید

---

### گام ۲ — گرفتن آیدی عددی تلگرام

1. به ربات [@userinfobot](https://t.me/userinfobot) پیام بدهید
2. عدد کنار **Id** را کپی کنید
3. در `.env` به عنوان `ADMIN_IDS` وارد کنید
4. برای چند ادمین: `ADMIN_IDS=123456789,987654321`

---

### گام ۳ — تنظیم آدرس پنل 3X-UI

```bash
# اگر webBasePath دارید (Settings → Security → URI Path):
PANEL_URL=https://your-server.com:8443/ebHlkqXkBbjm2bI260

# اگر webBasePath ندارید:
PANEL_URL=https://your-server.com:54321
```

> ❌ اشتباه رایج: `/panel/api` یا `/panel` اضافه نکنید

---

### گام ۴ — راه‌اندازی SSL و Webhook (برای پرداخت کریپتو)

اگر دامنه یا ساب‌دامنه دارید، می‌توانید HTTPS را با `onebot` راه‌اندازی کنید:

```
onebot → [7] SSL Manager → [1] Get new certificate
```

اسکریپت به‌صورت خودکار:
- گواهی Let's Encrypt می‌گیرد
- nginx reverse proxy تنظیم می‌کند
- `.env` را با webhook URL های صحیح آپدیت می‌کند
- ربات را ری‌استارت می‌کند

سپس URL های نمایش‌داده‌شده را در داشبورد MaxelPay و NOWPayments ثبت کنید.

---

### گام ۵ — تنظیم `ADMIN_SECRET`

```env
ADMIN_SECRET=MySecretPass2024
```

بعد از راه‌اندازی، در تلگرام بفرستید:
```
/admin_secret MySecretPass2024
```

---

## راهنمای پنل ادمین

| دکمه | کاربرد |
|---|---|
| پلن‌ها | افزودن، ویرایش و غیرفعال کردن پلن‌های فروش |
| اینباند تست | انتخاب اینباندها برای اشتراک تست رایگان |
| تخفیف‌ها | ایجاد کد تخفیف با درصد، تعداد مجاز و تاریخ انقضا |
| کاربران | جستجو، مشاهده و مدیریت کاربران و اشتراک‌هایشان |
| آمار | آمار درآمد، تعداد کاربران و اشتراک‌های فعال |
| تنظیم کارت | شماره کارت برای پرداخت کارت به کارت |
| روش‌های پرداخت | فعال/غیرفعال کردن روش‌ها و انتخاب درگاه |
| اشتراک تست | تنظیم حجم و مدت اشتراک تست |
| مدیریت تراکنش‌ها | بررسی پرداخت‌های در صف — کارت و کریپتو |
| ایجاد اشتراک دستی | اعطای اشتراک رایگان به کاربر خاص |
| پیام دسته‌جمعی | ارسال پیام به همه کاربران |
| بک‌آپ | دریافت فایل پشتیبان از دیتابیس و پنل |

---

## سوالات متداول

**چرا ربات پاسخ نمی‌دهد؟**
```bash
onebot → Bot Control → [5] Live logs
# یا:
docker logs onebot_bot --tail 50
```

---

**پرداخت کریپتو انجام شد ولی اشتراک فعال نشد؟**

1. مطمئن شوید Webhook URL در داشبورد MaxelPay/NOWPayments ثبت شده
2. بررسی کنید webhook server فعال است: `onebot → Webhook Manager → [4] Test webhook server`
3. لاگ را برای جزئیات بررسی کنید

---

**چطور چند اینباند به یک پلن اضافه کنم؟**

پنل ادمین → **پلن‌ها** → روی پلن کلیک کنید → **اینباندها** را ویرایش کنید.

---

**آیا ربات روی همان سرور پنل مشکلی ایجاد می‌کند؟**

خیر. ربات داخل Docker ایزوله اجرا می‌شود. اگر RAM محدود است (زیر 2GB)، حالت SQLite را انتخاب کنید.

---

**چطور ربات را به‌روزرسانی کنم؟**

```bash
cd /opt/onebot && git pull
onebot → Bot Control → [4] Rebuild image + restart
```

---

**backup کجا ذخیره می‌شود؟**

پشتیبان‌گیری خودکار هر شب انجام می‌شود و فایل‌ها به تلگرام ادمین‌ها ارسال می‌شود.
برای backup دستی: `onebot → [4] Backup & Restore → [1] Full backup`

این شامل: دیتابیس + گواهی‌های SSL + فایل `.env` + تنظیمات nginx است.

---

## حمایت از پروژه

اگر این پروژه برایتان مفید بوده، می‌توانید از طریق USDT/TRX حمایت کنید:

**شبکه TRX / TRC-20:**
```
TGzhHsfta8MdMPNzpexnqFCUiux7T1H5Ng
```

---

## مشارکت

گزارش باگ و پیشنهاد از طریق [Issues](../../issues) پذیرفته می‌شود.  
Pull Request ها با رعایت قوانین لایسنس پذیرفته می‌شوند.

---

## لایسنس

این پروژه تحت **ONEBOT Attribution License** منتشر شده است.  
استفاده، تغییر و توزیع آزاد است، **به شرط حفظ اعتبار سازنده**.  
برای جزئیات کامل فایل [LICENSE](LICENSE) را مطالعه کنید.

---

<sub>Credits: <a href="https://github.com/sariyan-0">github.com/sariyan-0</a></sub>
