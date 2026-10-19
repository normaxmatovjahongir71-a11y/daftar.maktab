# 📓 Raqamli Daftar

Maktab uchun raqamli daftar tizimi: Telegram bot + Web ilova.

## O'rnatish

### 1. Python kutubxonalarini o'rnatish

```bash
pip install -r requirements.txt
```

### 2. Muhit o'zgaruvchilarini sozlash

`.env` faylni tahrirlang:

```env
BOT_TOKEN=your_telegram_bot_token
SITE_URL=http://localhost:8080
TEACHER_LOGIN=admin
TEACHER_PASS=admin1234
```

### 3. Botni ishga tushirish

```bash
python bot.py
```

Bot va web server birgalikda ishga tushadi:
- 🤖 Telegram bot — polling rejimida
- 🌐 Web server — `http://localhost:8080`
- ⏰ Kundalik bildirishnoma — har kuni 14:00 (Toshkent vaqti)

## Telegram Bot Buyruqlari

### O'quvchi uchun
| Buyruq | Tavsif |
|--------|--------|
| `/start` | Boshlash, WebApp tugmasini olish |
| `/kirish login parol` | Tizimga kirish |
| `/baholarim` | O'z baholarini ko'rish |

### O'qituvchi uchun
| Buyruq | Tavsif |
|--------|--------|
| `/oqituvchi login parol` | O'qituvchi sifatida kirish |
| `/oquvchi_qosh Ism login parol` | Yangi o'quvchi qo'shish |
| `/oquvchilar` | O'quvchilar ro'yxati |
| `/baho login fan baho` | Baho qo'yish (2-5) |
| `/rasmlar` | O'quvchilar yuklagan rasmlar |

## Rasm Yuklash

O'quvchi Telegram botga rasm yuborganda:
1. Rasm serverga saqlanadi
2. O'qituvchiga avtomatik yuboriladi
3. Saytda ham ko'rinadi

## Web Ilova

Saytni brauzerda yoki Telegram WebApp orqali ochish mumkin:
- `http://localhost:8080` — brauzerda
- Telegram bot menyusidagi "📓 Daftar" tugmasi — Telegram ichida

### Telegram WebApp uchun (HTTPS kerak)

Ishlab chiqarish muhitida:
1. VPS serverga deploy qiling
2. nginx + SSL sertifikat o'rnating
3. `.env` da `SITE_URL=https://your-domain.com` deb yozing

Yoki test uchun ngrok ishlatish mumkin:
```bash
ngrok http 8080
```

## Fayl Tuzilishi

```
daftar.maktab/
├── bot.py              # Telegram bot + Web server + API
├── database.py         # SQLite ma'lumotlar bazasi
├── auth.py             # Parol shifrlash (PBKDF2)
├── requirements.txt    # Python kutubxonalar
├── .env                # Muhit o'zgaruvchilari
├── daftar.db           # SQLite baza (avtomatik yaratiladi)
├── templates/
│   └── index.html      # Web ilova
└── uploads/            # Yuklangan rasmlar
```

## Xavfsizlik

- Parollar PBKDF2-SHA256 (260,000 iteratsiya) + tuz bilan shifrlangan
- API tokenlari 7 kunlik muddatli
- O'qituvchi buyruqlari faqat tasdiqlangan foydalanuvchilar uchun
- CORS himoyasi mavjud
