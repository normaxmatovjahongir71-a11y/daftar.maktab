"""
Raqamli Daftar — Telegram Bot + Flask Web Server
python-telegram-bot + Flask + APScheduler
To'liq o'zbek tilida (lotin). Maktab daftari tizimi.
"""

import asyncio
import logging
import os
import sys
import uuid
import threading
from datetime import datetime, date
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory, send_file
from telegram import (
    Update, WebAppInfo, MenuButtonWebApp,
    KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from database import Database
from auth import create_token, verify_token

# ──────────────────────── Sozlamalar ────────────────────────

load_dotenv()

TOKEN = os.getenv('BOT_TOKEN', '8783586377:AAF8Bx_hp0cCLImSoMKu2HgjffYYGBiLIg4')
PORT = int(os.getenv('PORT', '5000'))

# Sayt manzili: Render yoki tashqi muhitdan avtomatik aniqlash
SITE_URL = (
    os.getenv('RENDER_EXTERNAL_URL')
    or os.getenv('SITE_URL')
    or 'https://daftar-maktab.onrender.com'
).strip()

if 'trycloudflare.com' in SITE_URL or 'localhost' in SITE_URL or '127.0.0.1' in SITE_URL:
    render_ext = os.getenv('RENDER_EXTERNAL_URL')
    if render_ext:
        SITE_URL = render_ext
    elif os.environ.get('RENDER'):
        SITE_URL = 'https://daftar-maktab.onrender.com'
TEACHER_LOGIN = os.getenv('TEACHER_LOGIN', 'admin')
TEACHER_PASS = os.getenv('TEACHER_PASS', 'admin1234')
ADMIN_CODE = os.getenv('ADMIN_CODE', '7777')
SUPABASE_KEY = os.getenv('SUPABASE_KEY', 'sb_publishable_AJ9YQzNKEcDJGzaimxjn2g_IIOYPBne')

admin_states = {}

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / 'templates'
UPLOADS_DIR = BASE_DIR / 'uploads'
UPLOADS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

db = Database()
db.init_db()

if TEACHER_LOGIN and TEACHER_PASS:
    db.create_teacher_if_not_exists(TEACHER_LOGIN, TEACHER_PASS)
    logger.info("Boshlang'ich o'qituvchi hisobi tekshirildi.")

KUNLAR = {
    1: "Dushanba",
    2: "Seshanba",
    3: "Chorshanba",
    4: "Payshanba",
    5: "Juma",
    6: "Shanba"
}

# ══════════════════════════════════════════════════════════════
#                      FLASK WEB SERVER
# ══════════════════════════════════════════════════════════════

flask_app = Flask(__name__, static_folder=None)
flask_app.config['MAX_CONTENT_LENGTH'] = 15 * 1024 * 1024  # 15 MB


@flask_app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


@flask_app.before_request
def handle_options():
    if request.method == 'OPTIONS':
        return '', 204


def get_current_user():
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    return verify_token(auth_header[7:])


def require_teacher_role():
    user = get_current_user()
    if not user or user.get('role') != 'teacher':
        return None
    return user


def require_any_auth():
    return get_current_user()


def error_response(msg, status=400):
    return jsonify({'error': msg}), status


@flask_app.route('/')
def serve_index():
    index_path = TEMPLATES_DIR / 'index.html'
    if index_path.exists():
        return send_file(index_path)
    return 'index.html topilmadi', 404


@flask_app.route('/healthz', methods=['GET'])
@flask_app.route('/ping', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'service': 'Raqamli Daftar Web & Telegram Bot',
        'time': datetime.now().isoformat()
    }), 200


@flask_app.route('/uploads/<filename>')
def serve_upload(filename):
    filepath = UPLOADS_DIR / filename
    if filepath.exists() and filepath.is_file():
        return send_from_directory(str(UPLOADS_DIR), filename)
    return 'Fayl topilmadi', 404


# ────────────── Auth API ──────────────

@flask_app.route('/api/config', methods=['GET'])
def api_config():
    return jsonify({
        'supabase_key': SUPABASE_KEY,
        'site_url': SITE_URL
    })


@flask_app.route('/api/auth/check-setup', methods=['GET'])
def api_check_setup():
    return jsonify({'has_teacher': db.has_teacher()})


@flask_app.route('/api/auth/setup', methods=['POST'])
def api_setup():
    if db.has_teacher():
        return error_response("O'qituvchi hisobi allaqachon mavjud.")
    data = request.get_json(silent=True) or {}
    login = data.get('login', '').strip()
    password = data.get('password', '').strip()
    if len(login) < 3 or len(password) < 4:
        return error_response("Login kamida 3, parol kamida 4 belgidan iborat bo'lishi kerak.")
    tid = db.create_teacher(login, password)
    if tid:
        token = create_token(tid, 'teacher', login)
        return jsonify({
            'success': True, 'token': token,
            'user': {'id': tid, 'login': login, 'role': 'teacher'}
        })
    return error_response('Xatolik yuz berdi.', 500)


@flask_app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    login = data.get('login', '').strip()
    password = data.get('password', '').strip()
    role = data.get('role', 'student')
    supabase_key = data.get('supabase_key')
    if supabase_key:
        logger.info(f"Supabase kaliti bilan login: {supabase_key[:12]}...")

    if role == 'teacher':
        user = db.verify_teacher(login, password)
        if user:
            token = create_token(user['id'], 'teacher', login)
            return jsonify({
                'success': True, 'token': token,
                'user': {'id': user['id'], 'login': login, 'role': 'teacher'}
            })
    else:
        user = db.verify_student(login, password)
        if user:
            token = create_token(user['id'], 'student', login, user['name'])
            return jsonify({
                'success': True, 'token': token,
                'user': {'id': user['id'], 'name': user['name'],
                         'login': login, 'role': 'student'}
            })

    return error_response("Login yoki parol noto'g'ri.", 401)


# ────────────── Jadval API ──────────────

@flask_app.route('/api/schedule', methods=['GET'])
def api_get_schedule():
    schedule = db.get_schedule()
    exam = db.get_exam()
    return jsonify({'schedule': schedule, 'exam': exam})


@flask_app.route('/api/schedule', methods=['POST'])
def api_set_schedule():
    if not require_teacher_role():
        return error_response("Ruxsat yo'q", 403)
    data = request.get_json(silent=True) or {}
    db.set_schedule(data['day'], data['slot'], data.get('subject', ''))
    return jsonify({'success': True})


@flask_app.route('/api/exam', methods=['GET'])
def api_get_exam():
    return jsonify(db.get_exam())


@flask_app.route('/api/exam', methods=['POST'])
def api_set_exam():
    if not require_teacher_role():
        return error_response("Ruxsat yo'q", 403)
    data = request.get_json(silent=True) or {}
    db.set_exam(data.get('name', ''), data.get('date', ''))
    return jsonify({'success': True})


# ────────────── Vazifalar API ──────────────

@flask_app.route('/api/tasks', methods=['GET'])
def api_get_tasks():
    return jsonify({'tasks': db.get_tasks()})


@flask_app.route('/api/tasks', methods=['POST'])
def api_add_task():
    if not require_teacher_role():
        return error_response("Ruxsat yo'q", 403)
    data = request.get_json(silent=True) or {}
    text = data.get('text', '').strip()
    deadline = data.get('deadline', '').strip()
    if not text or not deadline:
        return error_response('Matn va muddat kerak.')
    tid = db.add_task(text, deadline)
    return jsonify({'success': True, 'id': tid})


@flask_app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
def api_delete_task(task_id):
    if not require_teacher_role():
        return error_response("Ruxsat yo'q", 403)
    db.delete_task(task_id)
    return jsonify({'success': True})


# ────────────── O'quvchilar API ──────────────

@flask_app.route('/api/students', methods=['GET'])
def api_get_students():
    if not require_teacher_role():
        return error_response("Ruxsat yo'q", 403)
    return jsonify({'students': db.get_students()})


@flask_app.route('/api/students', methods=['POST'])
def api_add_student():
    if not require_teacher_role():
        return error_response("Ruxsat yo'q", 403)
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    login = data.get('login', '').strip()
    password = data.get('password', '').strip()
    if not name or not login or len(password) < 4:
        return error_response("Ism, login va parol (kamida 4 belgi) kerak.")
    sid = db.create_student(name, login, password)
    if sid:
        return jsonify({'success': True, 'id': sid})
    return error_response(f"'{login}' login allaqachon band.", 409)


@flask_app.route('/api/students/<int:student_id>', methods=['DELETE'])
def api_delete_student(student_id):
    if not require_teacher_role():
        return error_response("Ruxsat yo'q", 403)
    db.delete_student(student_id)
    return jsonify({'success': True})


@flask_app.route('/api/students/<int:student_id>/password', methods=['PUT'])
def api_update_password(student_id):
    if not require_teacher_role():
        return error_response("Ruxsat yo'q", 403)
    data = request.get_json(silent=True) or {}
    password = data.get('password', '').strip()
    if len(password) < 4:
        return error_response('Parol kamida 4 belgi.')
    db.update_student_password(student_id, password)
    return jsonify({'success': True})


# ────────────── Baholar API ──────────────

@flask_app.route('/api/grades', methods=['GET'])
def api_get_grades():
    auth_user = require_any_auth()
    if not auth_user:
        return error_response('Avtorizatsiya kerak', 401)

    student_id = request.args.get('student_id')
    if auth_user['role'] == 'student':
        student_id = auth_user['uid']

    if student_id:
        grades = db.get_grades(int(student_id))
    else:
        grades = db.get_all_grades()

    return jsonify({'grades': grades})


@flask_app.route('/api/grades', methods=['POST'])
def api_add_grade():
    if not require_teacher_role():
        return error_response("Ruxsat yo'q", 403)
    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id')
    subject = data.get('subject', '').strip()
    grade = data.get('grade')
    if not student_id or not subject or grade not in [2, 3, 4, 5]:
        return error_response('student_id, subject va grade (2-5) kerak.')
    gid = db.add_grade(int(student_id), subject, int(grade))
    return jsonify({'success': True, 'id': gid})


@flask_app.route('/api/grades/<int:grade_id>', methods=['DELETE'])
def api_delete_grade(grade_id):
    if not require_teacher_role():
        return error_response("Ruxsat yo'q", 403)
    db.delete_grade(grade_id)
    return jsonify({'success': True})


# ────────────── Rasmlar API ──────────────

@flask_app.route('/api/upload', methods=['POST'])
def api_upload_image():
    auth_user = require_any_auth()
    if not auth_user:
        return error_response('Avtorizatsiya kerak', 401)

    student_id = auth_user['uid']
    task_id_str = request.form.get('task_id', '')
    task_id = int(task_id_str) if task_id_str else None

    if 'image' not in request.files:
        return error_response('Rasm topilmadi.')

    file = request.files['image']
    if file.filename == '':
        return error_response('Fayl tanlanmagan.')

    ext = 'jpg'
    if '.' in file.filename:
        ext = file.filename.rsplit('.', 1)[1].lower()
    fname = f"{student_id}_{uuid.uuid4().hex[:8]}.{ext}"
    filepath = UPLOADS_DIR / fname
    file.save(str(filepath))

    iid = db.save_image(task_id, student_id, fname, file.filename)
    return jsonify({'success': True, 'id': iid, 'image_url': f'/uploads/{fname}'})


@flask_app.route('/api/images', methods=['GET'])
def api_get_images():
    auth_user = require_any_auth()
    if not auth_user:
        return error_response('Avtorizatsiya kerak', 401)

    task_id = request.args.get('task_id')
    student_id = request.args.get('student_id')

    if auth_user['role'] == 'student':
        student_id = auth_user['uid']

    images = db.get_images(
        task_id=int(task_id) if task_id else None,
        student_id=int(student_id) if student_id else None
    )
    for img in images:
        img['image_url'] = f"/uploads/{img['image_path']}"
    return jsonify({'images': images})


# ══════════════════════════════════════════════════════════════
#                    TELEGRAM BOT HANDLERLARI
# ══════════════════════════════════════════════════════════════

def start_keep_alive():
    """Render Web Servisining uxlab qolishining oldini olish uchun self-ping (Keep-Alive)."""
    import time
    import urllib.request

    def _worker():
        time.sleep(20)
        logger.info("✅ Keep-alive monitoring ishga tushdi.")
        while True:
            try:
                target_url = SITE_URL.strip() if (SITE_URL and SITE_URL.startswith('http')) else f"http://127.0.0.1:{PORT}"
                ping_url = f"{target_url.rstrip('/')}/healthz"
                req = urllib.request.Request(
                    ping_url,
                    headers={'User-Agent': 'DaftarBot-KeepAlive/1.0'}
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    logger.debug(f"Keep-alive ping: {resp.status}")
            except Exception as e:
                logger.debug(f"Keep-alive ping xabari: {e}")
            time.sleep(600)  # Har 10 daqiqada bir marta (Render 15 daqiqada uxlaydi)

    t = threading.Thread(target=_worker, daemon=True, name="keep_alive_worker")
    t.start()


async def reply_safe(message_or_update, text: str, **kwargs):
    """
    Xabarni xavfsiz yuborish.
    Agar Markdown parse xatosi yuz bersa, bot to'xtab qolmaydi va oddiy matn tarzida yuboradi.
    """
    msg = None
    if hasattr(message_or_update, 'reply_text'):
        msg = message_or_update
    elif hasattr(message_or_update, 'message') and message_or_update.message:
        msg = message_or_update.message

    if not msg:
        return None

    try:
        return await msg.reply_text(text, **kwargs)
    except Exception as e:
        err = str(e).lower()
        if 'parse' in err or 'entity' in err:
            clean_kwargs = {k: v for k, v in kwargs.items() if k != 'parse_mode'}
            return await msg.reply_text(text, **clean_kwargs)
        logger.error(f"reply_safe xatosi: {e}")
        return None


async def send_safe(bot, chat_id: int, text: str, **kwargs):
    """bot.send_message uchun xavfsiz wrapper."""
    try:
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except Exception as e:
        err = str(e).lower()
        if 'parse' in err or 'entity' in err:
            clean_kwargs = {k: v for k, v in kwargs.items() if k != 'parse_mode'}
            return await bot.send_message(chat_id=chat_id, text=text, **clean_kwargs)
        logger.error(f"send_safe xatosi (chat {chat_id}): {e}")
        return None


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Botning barcha kutilmagan xatolarini ushlash va botni to'xtamasdan ishlashini ta'minlash."""
    err = context.error
    logger.error(f"Telegram Bot xatoligi: {err}", exc_info=err)


def save_session_backup(chat_id: int, role: str, login: str):
    """Sessiyani doimiy faylga saqlash (qayta yuklanganda ham saqlanib qolishi uchun)."""
    try:
        backup_file = BASE_DIR / 'sessions_backup.json'
        sessions = {}
        if backup_file.exists():
            import json
            with open(backup_file, 'r', encoding='utf-8') as f:
                sessions = json.load(f)
        sessions[str(chat_id)] = {'role': role, 'login': login, 'time': datetime.now().isoformat()}
        with open(backup_file, 'w', encoding='utf-8') as f:
            import json
            json.dump(sessions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Sessiyani saqlashda ogohlantirish: {e}")


def get_admin_keyboard():
    """Adminlar uchun boshqaruv tugmalari."""
    keyboard = [
        [KeyboardButton("➕ O'quvchi ochish"), KeyboardButton("👨‍🏫 O'qituvchi ochish")],
        [KeyboardButton("📋 O'quvchilar ro'yxati"), KeyboardButton("👥 O'qituvchilar ro'yxati")],
        [KeyboardButton("⭐ Baho qo'yish"), KeyboardButton("📊 Tizim statistikasi")],
        [KeyboardButton("🔑 Admin kodi"), KeyboardButton("🔙 Asosiy menyu")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_main_keyboard(chat_id: int):
    """Foydalanuvchi roliga mos menyu klaviaturasini qaytaradi."""
    teacher = db.get_teacher_by_chat_id(chat_id)
    is_admin_user = db.is_admin(chat_id)

    if SITE_URL.startswith('https://'):
        top_btn = KeyboardButton("📓 Daftarni ochish (Sayt)", web_app=WebAppInfo(url=SITE_URL))
    else:
        top_btn = KeyboardButton("📓 Daftarni ochish (Sayt)")

    keyboard = [
        [top_btn],
        [KeyboardButton("📊 Baholarim"), KeyboardButton("📅 Dars jadvali")],
        [KeyboardButton("📝 Uy vazifalari"), KeyboardButton("🏆 Imtihonlar")],
        [KeyboardButton("📷 Rasm yuklash (Uy ishi)"), KeyboardButton("🔑 Profil / Kirish")]
    ]

    if is_admin_user:
        keyboard.append([KeyboardButton("👑 Admin paneli"), KeyboardButton("👨‍🏫 O'qituvchi boshqaruvi")])
    elif teacher:
        keyboard.append([KeyboardButton("👨‍🏫 O'qituvchi boshqaruvi")])
    
    keyboard.append([KeyboardButton("ℹ️ Yordam / Buyruqlar")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Boshlash buyrug'i — Salomlashish va barcha qulay tugmalar."""
    if not update.effective_chat:
        return
    chat_id = update.effective_chat.id
    user_first_name = (update.effective_user.first_name if update.effective_user else None) or "Foydalanuvchi"

    is_admin_user = db.is_admin(chat_id)
    teacher = db.get_teacher_by_chat_id(chat_id)
    student = db.get_student_by_chat_id(chat_id)

    status_text = ""
    if is_admin_user:
        status_text = f"\n👑 *Siz tizim bosh administratori sifatida tizimdasiz* (`admin`)"
    elif teacher:
        status_text = f"\n👨‍🏫 *Siz o'qituvchi sifatida tizimdasiz* (`{teacher['login']}`)"
    elif student:
        status_text = f"\n👨‍🎓 *Siz o'quvchi sifatida tizimdasiz*: *{student['name']}*"
    else:
        status_text = "\n💡 *Eslatma:* Baholaringizni shaxsiy ko'rish uchun `/kirish login parol` yuboring."

    reply_markup = get_main_keyboard(chat_id)

    text = (
        f"Assalomu alaykum, *{user_first_name}*! 👋🌟\n\n"
        f"Maktab uchun mo'ljallangan *«Raqamli Daftar»* tizimiga xush kelibsiz! 📚🎒{status_text}\n\n"
        f"Quyidagi tugmalar orqali barcha maktab ma'lumotlariga bir bosishda ega bo'ling:\n\n"
        f"• 📓 *Daftarni ochish* — Veb-saytni to'g'ridan-to'g'ri Telegram ichida ochish\n"
        f"• 📊 *Baholarim* — O'zlashtirish va barcha fan baholari\n"
        f"• 📅 *Dars jadvali* — Bugungi va haftalik darslar\n"
        f"• 📝 *Uy vazifalari* — Berilgan topshiriqlar va muddatlar\n"
        f"• 📷 *Rasm yuklash* — Daftardagi uy ishini rasmga olib yuborish (o'qituvchiga boradi)\n"
        f"• 🏆 *Imtihonlar* — Yaqinlashayotgan imtihon sanasi\n\n"
        f"Kerakli bo'limni tanlang 👇"
    )

    await reply_safe(
        update,
        text,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin boshqaruv panelini ochish — kodsiz to'g'ridan-to'g'ri admin qilish."""
    if not update.effective_chat:
        return
    chat_id = update.effective_chat.id
    username = (update.effective_user.username if update.effective_user else None) or (update.effective_user.first_name if update.effective_user else "Admin")

    # Foydalanuvchini admin sifatida saqlash
    db.add_admin(chat_id, username)
    save_session_backup(chat_id, 'admin', 'admin')

    await reply_safe(
        update,
        "👑 *ADMIN BOSHQARUV PANELIGA XUSH KELIBSIZ!*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Siz tizim administratori sifatida tanindingiz. "
        "Bu yerdan yangi o'quvchi yoki o'qituvchi hisoblarini ochishingiz, "
        "barcha ma'lumotlarni ko'rishingiz va tizimni boshqarishingiz mumkin.\n\n"
        "Kerakli bo'limni pastdagi tugmalardan tanlang 👇",
        parse_mode='Markdown',
        reply_markup=get_admin_keyboard()
    )


async def send_schedule_view(chat_id, context, selected_day=None, message_id=None):
    """Dars jadvalini chiroyli inline tugmalar bilan ko'rsatish."""
    today_weekday = datetime.now().isoweekday()  # 1: Mon .. 7: Sun
    if selected_day is None:
        selected_day = today_weekday if 1 <= today_weekday <= 6 else 1

    all_schedule = db.get_schedule()
    day_lessons = [s for s in all_schedule if s['day'] == selected_day]
    day_lessons.sort(key=lambda x: x['slot'])

    day_name = KUNLAR.get(selected_day, "Dushanba")
    is_today_str = " (Bugun 📌)" if selected_day == today_weekday else ""

    text = f"📅 *DARS JADVALI — {day_name.upper()}*{is_today_str}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n"

    active_lessons = [l for l in day_lessons if l['subject'].strip()]
    if active_lessons:
        for item in active_lessons:
            text += f"*{item['slot']}-dars:* 📘 {item['subject']}\n"
    else:
        text += "📭 Bu kunga darslar belgilanmagan yoki dam olish kuni.\n"

    text += "━━━━━━━━━━━━━━━━━━━━━━\n"
    text += "Boshqa kunlarni ko'rish uchun tugmani bosing 👇"

    # Kunlarni tanlash tugmalari
    inline_buttons = [
        [
            InlineKeyboardButton(f"{'✅ ' if selected_day == 1 else ''}Dush", callback_data="sched_1"),
            InlineKeyboardButton(f"{'✅ ' if selected_day == 2 else ''}Sesh", callback_data="sched_2"),
            InlineKeyboardButton(f"{'✅ ' if selected_day == 3 else ''}Chor", callback_data="sched_3"),
        ],
        [
            InlineKeyboardButton(f"{'✅ ' if selected_day == 4 else ''}Pay", callback_data="sched_4"),
            InlineKeyboardButton(f"{'✅ ' if selected_day == 5 else ''}Juma", callback_data="sched_5"),
            InlineKeyboardButton(f"{'✅ ' if selected_day == 6 else ''}Shan", callback_data="sched_6"),
        ]
    ]
    markup = InlineKeyboardMarkup(inline_buttons)

    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode='Markdown',
                reply_markup=markup
            )
            return
        except Exception:
            pass

    await send_safe(
        context.bot,
        chat_id=chat_id,
        text=text,
        parse_mode='Markdown',
        reply_markup=markup
    )


async def schedule_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Jadval kunlarini o'zgartirish callbacki."""
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("sched_"):
        day = int(data.split("_")[1])
        await send_schedule_view(
            chat_id=query.message.chat_id,
            context=context,
            selected_day=day,
            message_id=query.message.message_id
        )


async def show_grades_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Baholarni ko'rsatish."""
    chat_id = update.effective_chat.id
    student = db.get_student_by_chat_id(chat_id)

    if not student:
        await reply_safe(
            update,
            "🔒 *Siz hali tizimga kirmagansiz!*\n\n"
            "O'z baholaringizni ko'rish uchun o'quvchi hisobingizga kiring:\n"
            "`/kirish login parol`\n\n"
            "Masalan: `/kirish ali ali123`",
            parse_mode='Markdown'
        )
        return

    grades = db.get_grades(student['id'])
    if not grades:
        await reply_safe(
            update,
            f"👤 *{student['name']}*, sizga hali baholar qo'yilmagan.\n"
            "O'qituvchi baho qo'yganda bu yerda ko'rishingiz mumkin! 🌟",
            parse_mode='Markdown'
        )
        return

    subjects = {}
    for g in grades:
        subj = g['subject']
        if subj not in subjects:
            subjects[subj] = []
        subjects[subj].append(g['grade'])

    text = f"📊 *{student['name'].upper()}NING BAHOLARI*\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n"

    total_sum, total_count = 0, 0
    for subj, sg in sorted(subjects.items()):
        avg = sum(sg) / len(sg)
        grades_str = ' '.join([_grade_emoji(g) for g in sg])
        text += f"📘 *{subj}*: {grades_str}\n"
        text += f"   └── O'rtacha: *{avg:.2f}*\n\n"
        total_sum += sum(sg)
        total_count += len(sg)

    overall = total_sum / total_count if total_count else 0
    text += "━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"📈 *Umumiy o'rtacha ball:* *{overall:.2f}* ⭐\n\n"

    # Maqsad maslahati
    if overall < 5.0:
        needed_fives = 0
        cur_sum = total_sum
        cur_count = total_count
        while (cur_sum / cur_count) < 4.8 and needed_fives < 20:
            cur_sum += 5
            cur_count += 1
            needed_fives += 1
        if needed_fives > 0:
            text += f"💡 *Maslahat:* A'lochi (4.8+) bo'lish uchun ketma-ket yana *{needed_fives} ta «5»* olishingiz kerak! 💪"

    await reply_safe(update, text, parse_mode='Markdown')


async def show_tasks_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Vazifalarni ko'rsatish."""
    tasks = db.get_tasks()
    if not tasks:
        await reply_safe(
            update,
            "📝 *Hozircha faol uy vazifalari yo'q!*\nDam oling yoki mustaqil shug'ullaning. 🎉",
            parse_mode='Markdown'
        )
        return

    text = "📝 *UYGA VAZIFALAR RO'YXATI*\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n"

    today_str = date.today().isoformat()

    for idx, t in enumerate(tasks, 1):
        deadline = t['deadline']
        is_expired = deadline < today_str
        status_icon = "🔴 (Muddati o'tgan)" if is_expired else "⏳"
        text += f"*{idx}. {t['text']}*\n"
        text += f"   📅 Muddat: `{deadline}` {status_icon}\n\n"

    text += "━━━━━━━━━━━━━━━━━━━━━━\n"
    text += (
        "📸 *Uyga vazifani daftaringizda bajargan bo'lsangiz:*\n"
        "Shunchaki sahifani rasmga tushiring va shu botga yuboring! "
        "Rasm avtomatik o'qituvchiga ko'rinadi va tekshiriladi. ✅"
    )

    await reply_safe(update, text, parse_mode='Markdown')


async def show_exam_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Imtihon ma'lumotlarini ko'rsatish."""
    exam = db.get_exam()
    exam_name = exam.get('name', '').strip()
    exam_date_str = exam.get('date', '').strip()

    if not exam_name and not exam_date_str:
        await reply_safe(
            update,
            "🏆 *Hozircha rejalashtirilgan imtihon belgilanmagan.*\n"
            "O'qituvchi imtihon sanasini kiritishi bilan bu yerda ko'rinadi.",
            parse_mode='Markdown'
        )
        return

    text = f"🏆 *KUTILAYOTGAN IMTIHON*\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"📌 *Fan / Nomi:* {exam_name or 'Imtihon'}\n"
    text += f"📅 *Belgilangan sana:* `{exam_date_str}`\n"

    try:
        exam_dt = datetime.strptime(exam_date_str, "%Y-%m-%d").date()
        today = date.today()
        days_left = (exam_dt - today).days

        if days_left > 0:
            text += f"⏳ *Imtihongacha qoldi:* *{days_left} kun* ⏰\n\n"
            text += "Tayyorgarlikni hozirdan boshlang, muvaffaqiyat tilaymiz! 🎯"
        elif days_left == 0:
            text += "\n🔥 *Bugun imtihon kuni! Omad yor bo'lsin!* 🌟"
        else:
            text += f"\n✅ Imtihon o'tib ketgan ({abs(days_left)} kun oldin)."
    except Exception:
        pass

    await reply_safe(update, text, parse_mode='Markdown')


async def show_profile_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Profil va kirish ma'lumotlari."""
    chat_id = update.effective_chat.id
    teacher = db.get_teacher_by_chat_id(chat_id)
    student = db.get_student_by_chat_id(chat_id)

    if teacher:
        text = (
            "👨‍🏫 *SIZNING PROFILINGIZ (O'qituvchi)*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Login:* `{teacher['login']}`\n"
            f"🆔 *Telegram ID:* `{chat_id}`\n\n"
            "O'qituvchi buyruqlari:\n"
            "• `/oquvchi_qosh Ism login parol` — Yangi o'quvchi qo'shish\n"
            "• `/oquvchilar` — Barcha o'quvchilar ro'yxati\n"
            "• `/baho login fan baho` — O'quvchiga baho qo'yish (2-5)\n"
            "• `/rasmlar` — O'quvchilar yuklagan uy ishi rasmlari\n\n"
            "Chiqish uchun: `/chiqish`"
        )
    elif student:
        avg = db.get_student_average(student['id'])
        avg_str = f"{avg:.2f}" if avg else "Baholar yo'q"
        text = (
            "👨‍🎓 *SIZNING PROFILINGIZ (O'quvchi)*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *Ism:* *{student['name']}*\n"
            f"🔑 *Login:* `{student['login']}`\n"
            f"⭐ *O'rtacha ball:* *{avg_str}*\n"
            f"🆔 *Telegram ID:* `{chat_id}`\n\n"
            "Har kuni 14:00 da kunlik baholar hisoboti shu yerga keladi.\n"
            "Hisobdan chiqish uchun: `/chiqish`"
        )
    else:
        text = (
            "🔑 *TIZIMGA KIRISH*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Siz hozir tizimga ulanmagansiz.\n\n"
            "👨‍🎓 *O'quvchilar uchun kirish:*\n"
            "`/kirish login parol`\n"
            "_Misol: /kirish jasur jasur123_\n\n"
            "👨‍🏫 *O'qituvchilar uchun kirish:*\n"
            "`/oqituvchi login parol`\n"
            "_Misol: /oqituvchi admin admin1234_\n\n"
            "Login va parolingizni bilmasangiz, o'qituvchingizdan so'rang."
        )

    await reply_safe(update, text, parse_mode='Markdown')


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yordam va barcha buyruqlar."""
    text = (
        "ℹ️ *«RAQAMLI DAFTAR» BOTI YORDAMI*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Ushbu bot maktab o'quvchilari va o'qituvchilari uchun yaratilgan.\n\n"
        "📌 *Asosiy buyruqlar:*\n"
        "• `/start` — Botni qayta ishga tushirish va asosiy menyu\n"
        "• `/kirish login parol` — O'quvchi profiliga kirish\n"
        "• `/baholarim` — Baholar va o'rtacha ballni ko'rish\n"
        "• `/chiqish` — Hisobdan chiqish\n\n"
        "👨‍🏫 *O'qituvchilar uchun:*\n"
        "• `/oqituvchi login parol` — O'qituvchi profiliga kirish\n"
        "• `/oquvchi_qosh Ism login parol` — Yangi o'quvchi kiritish\n"
        "• `/oquvchilar` — O'quvchilar ro'yxati\n"
        "• `/baho login fan baho` — Baho qo'yish (masalan: `/baho ali Tarix 5`)\n"
        "• `/rasmlar` — Yuklangan rasmlarni ko'rish\n\n"
        "📸 *Uyga vazifa rasmini yuborish:*\n"
        "Daftaringizni rasmga olib shunchaki botga yuborsangiz kifoya!"
    )
    await reply_safe(update, text, parse_mode='Markdown')


async def cmd_chiqish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram bog'lanmasidan chiqish."""
    chat_id = update.effective_chat.id
    conn = db._conn()
    conn.execute("UPDATE students SET telegram_chat_id = NULL WHERE telegram_chat_id = ?", (chat_id,))
    conn.execute("UPDATE teachers SET telegram_chat_id = NULL WHERE telegram_chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

    await reply_safe(
        update,
        "👋 Siz hisobingizdan muvaffaqiyatli chiqdingiz.\n"
        "Qayta kirish uchun `/kirish login parol` yuborishingiz mumkin.",
        reply_markup=get_main_keyboard(chat_id)
    )


async def cmd_kirish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await reply_safe(update, "❌ Foydalanish: `/kirish login parol`", parse_mode='Markdown')
        return
    login, password = context.args[0], context.args[1]
    student = db.verify_student(login, password)
    if student:
        db.set_student_chat_id(login, update.effective_chat.id)
        save_session_backup(update.effective_chat.id, 'student', login)
        await reply_safe(
            update,
            f"✅ Xush kelibsiz, *{student['name']}*!\n\n"
            f"Har kuni 14:00 da baholaringiz hisoboti sizga yuboriladi.\n"
            f"Daftarni ochish uchun quyidagi tugmalardan foydalaning.",
            parse_mode='Markdown',
            reply_markup=get_main_keyboard(update.effective_chat.id)
        )
        try:
            await update.message.delete()
        except Exception:
            pass
    else:
        await reply_safe(update, "❌ Login yoki parol noto'g'ri.")


async def cmd_oqituvchi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await reply_safe(update, "❌ Foydalanish: `/oqituvchi login parol`", parse_mode='Markdown')
        return
    login, password = context.args[0], context.args[1]
    teacher = db.verify_teacher(login, password)
    if teacher:
        db.set_teacher_chat_id(login, update.effective_chat.id)
        save_session_backup(update.effective_chat.id, 'teacher', login)
        await reply_safe(
            update,
            f"✅ O'qituvchi hisobiga kirdingiz (`{login}`)!\n\n"
            "Siz endi bot va veb-sayt orqali baholar, jadval, vazifalar va o'quvchilarni boshqarishingiz mumkin.",
            parse_mode='Markdown',
            reply_markup=get_main_keyboard(update.effective_chat.id)
        )
        try:
            await update.message.delete()
        except Exception:
            pass
    else:
        await reply_safe(update, "❌ Login yoki parol noto'g'ri.")


async def cmd_baholarim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_grades_view(update, context)


async def cmd_oquvchi_qosh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    teacher = db.get_teacher_by_chat_id(chat_id)
    if not teacher:
        await reply_safe(update, "❌ Siz o'qituvchi sifatida kirmagansiz. `/oqituvchi login parol`", parse_mode='Markdown')
        return
    if len(context.args) < 3:
        await reply_safe(update, "❌ Foydalanish: `/oquvchi_qosh Ism login parol`", parse_mode='Markdown')
        return
    name, login, password = context.args[0], context.args[1], context.args[2]
    if len(password) < 4:
        await reply_safe(update, "❌ Parol kamida 4 belgidan iborat bo'lishi kerak.")
        return
    result = db.create_student(name, login, password)
    if result:
        await reply_safe(
            update,
            f"✅ *O'quvchi muvaffaqiyatli qo'shildi!*\n\n"
            f"👤 Ism: *{name}*\n"
            f"🔑 Login: `{login}`\n"
            f"🔒 Parol: `{password}`\n\n"
            f"O'quvchi botga kirishi uchun: `/kirish {login} {password}`",
            parse_mode='Markdown'
        )
    else:
        await reply_safe(update, f"❌ '{login}' login allaqachon band.")


async def cmd_oquvchilar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    teacher = db.get_teacher_by_chat_id(chat_id)
    if not teacher:
        await reply_safe(update, "❌ Bu buyruq faqat o'qituvchilar uchun.")
        return
    students = db.get_students()
    if not students:
        await reply_safe(update, "📭 O'quvchilar hali kiritilmagan.")
        return
    text = "👨‍🎓 *O'QUVCHILAR RO'YXATI:*\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for i, s in enumerate(students, 1):
        avg = db.get_student_average(s['id'])
        avg_str = f"{avg:.2f}" if avg else "—"
        connected = "🟢 (Telegram ulangan)" if s.get('telegram_chat_id') else "⚪"
        text += f"*{i}. {s['name']}* (`{s['login']}`)\n"
        text += f"   └── O'rtacha: *{avg_str}* | {connected}\n\n"
    await reply_safe(update, text, parse_mode='Markdown')


async def cmd_baho(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    teacher = db.get_teacher_by_chat_id(chat_id)
    if not teacher:
        await reply_safe(update, "❌ Bu buyruq faqat o'qituvchilar uchun.")
        return
    if len(context.args) < 3:
        await reply_safe(update, "❌ Foydalanish: `/baho login fan baho(2-5)`\nMasalan: `/baho ali Matematika 5`", parse_mode='Markdown')
        return
    login, subject = context.args[0], context.args[1]
    try:
        grade = int(context.args[2])
        if grade < 2 or grade > 5:
            raise ValueError()
    except ValueError:
        await reply_safe(update, "❌ Baho 2 dan 5 gacha bo'lishi kerak.")
        return
    student = db.get_student_by_login(login)
    if not student:
        await reply_safe(update, f"❌ '{login}' loginli o'quvchi topilmadi.")
        return
    db.add_grade(student['id'], subject, grade)
    emoji = _grade_emoji(grade)
    await reply_safe(
        update,
        f"✅ *Baho qo'yildi!*\n\n"
        f"👤 O'quvchi: *{student['name']}*\n"
        f"📘 Fan: *{subject}*\n"
        f"⭐ Baho: {emoji}",
        parse_mode='Markdown'
    )


async def cmd_rasmlar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    teacher = db.get_teacher_by_chat_id(chat_id)
    if not teacher:
        await reply_safe(update, "❌ Bu bo'lim faqat o'qituvchilar uchun.")
        return
    images = db.get_all_images()
    if not images:
        await reply_safe(update, "📭 Hali o'quvchilar tomonidan rasm yuklanmagan.")
        return
    await reply_safe(update, f"🖼 *Oxirgi yuklangan daftarlar rasmlari (jami {len(images)} ta):*", parse_mode='Markdown')
    for img in images[:6]:
        img_path = UPLOADS_DIR / img['image_path']
        if img_path.exists():
            caption = f"👤 O'quvchi: {img['student_name']}\n📅 Sana: {img['created_at']}"
            try:
                with open(img_path, 'rb') as f:
                    await update.message.reply_photo(photo=f, caption=caption)
            except Exception as e:
                logger.error(f"Rasm yuborishda xato: {e}")


async def handle_text_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Matnli tugmalar bosilganda ishlaydigan markaziy handler."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()

    if text in ["📓 Daftarni ochish (Sayt)", "Daftarni ochish", "Sayt", "sayt", "Daftar"]:
        if SITE_URL.startswith('https://'):
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("📓 Daftarni ochish (Web App)", web_app=WebAppInfo(url=SITE_URL))],
                [InlineKeyboardButton("🌐 Brauzerda ochish (Chrome / Safari)", url=SITE_URL)]
            ])
            await reply_safe(
                update,
                f"📓 *«Raqamli Daftar» tizimiga kirish:*\n\n"
                f"🔗 *Sayt manzili:* `{SITE_URL}`\n\n"
                f"Quyidagi tugmalardan birini bosing 👇",
                parse_mode='Markdown',
                reply_markup=markup
            )
        else:
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Saytga o'tish (Brauzer)", url=SITE_URL)]])
            await reply_safe(
                update,
                f"📓 *Raqamli Daftar veb-sayti:*\n\n"
                f"🔗 Sayt manzili: {SITE_URL}\n\n"
                f"💡 _Sayt kompyuter yoki telefon brauzerida to'liq ishlaydi._",
                parse_mode='Markdown',
                reply_markup=markup
            )
    elif text in ["📊 Baholarim", "Baholar", "baholar"]:
        await show_grades_view(update, context)
    elif text in ["📅 Dars jadvali", "Dars jadvali", "jadval"]:
        await send_schedule_view(update.effective_chat.id, context)
    elif text in ["📝 Uy vazifalari", "Uy vazifalari", "vazifalar"]:
        await show_tasks_view(update, context)
    elif text in ["🏆 Imtihonlar", "Imtihon", "imtihon"]:
        await show_exam_view(update, context)
    elif text in ["📷 Rasm yuklash (Uy ishi)", "📷 Rasm yuborish", "Rasm"]:
        await reply_safe(
            update,
            "📸 *Uyga vazifa daftarini yuborish:*\n\n"
            "Daftaringizni oching, uyga vazifa yozilgan betini rasmga oling va to'g'ridan-to'g'ri ushbu botga rasm tarzida yuboring!\n\n"
            "Bot rasmni qabul qilib, o'qituvchingizga avtomatik yetkazadi! 🚀",
            parse_mode='Markdown'
        )
    elif text in ["🔑 Profil / Kirish", "Profil", "profil"]:
        await show_profile_view(update, context)
    elif text in ["ℹ️ Yordam / Buyruqlar", "Yordam", "yordam"]:
        await cmd_help(update, context)
    # ── Admin holatlarini tekshirish (State handling) ──
    chat_id = update.effective_chat.id
    if text == "/cancel":
        admin_states.pop(chat_id, None)
        await reply_safe(update, "❌ Amal bekor qilindi.", reply_markup=get_main_keyboard(chat_id))
        return

    if chat_id in admin_states and db.is_admin(chat_id):
        current_state = admin_states[chat_id]
        if current_state == 'awaiting_student':
            parts = [p.strip() for p in text.replace(',', '|').split('|') if p.strip()]
            if len(parts) >= 3:
                name, login, password = parts[0], parts[1], parts[2]
                if len(password) < 4:
                    await reply_safe(update, "❌ Parol kamida 4 belgidan iborat bo'lishi kerak. Qayta urinib ko'ring:")
                    return
                sid = db.create_student(name, login, password)
                admin_states.pop(chat_id, None)
                if sid:
                    await reply_safe(
                        update,
                        f"✅ *Yangi o'quvchi hisobi yaratildi!*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 Ism: *{name}*\n"
                        f"🔑 Login: `{login}`\n"
                        f"🔒 Parol: `{password}`\n\n"
                        f"O'quvchi botga kirishi uchun:\n`/kirish {login} {password}`",
                        parse_mode='Markdown',
                        reply_markup=get_admin_keyboard()
                    )
                else:
                    await reply_safe(update, f"❌ '{login}' login allaqachon band. Boshqa login tanlang.", reply_markup=get_admin_keyboard())
                return
            else:
                await reply_safe(
                    update,
                    "⚠️ *Noto'g'ri format!*\n"
                    "Iltimos, quyidagicha ajratib yozing:\n"
                    "`Ism Familiya | login | parol`\n\n"
                    "Misol: `Aziz Rahimov | aziz123 | aziz2026`\n"
                    "Yoki bekor qilish uchun /cancel yozing.",
                    parse_mode='Markdown'
                )
                return

        elif current_state == 'awaiting_teacher':
            parts = [p.strip() for p in text.replace(',', '|').split('|') if p.strip()]
            if len(parts) >= 2:
                login, password = parts[0], parts[1]
                if len(password) < 4:
                    await reply_safe(update, "❌ Parol kamida 4 belgidan iborat bo'lishi kerak. Qayta urinib ko'ring:")
                    return
                tid = db.create_teacher(login, password)
                admin_states.pop(chat_id, None)
                if tid:
                    await reply_safe(
                        update,
                        f"✅ *Yangi o'qituvchi hisobi yaratildi!*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🔑 Login: `{login}`\n"
                        f"🔒 Parol: `{password}`\n\n"
                        f"O'qituvchi kirishi uchun:\n`/oqituvchi {login} {password}`",
                        parse_mode='Markdown',
                        reply_markup=get_admin_keyboard()
                    )
                else:
                    await reply_safe(update, f"❌ '{login}' login allaqachon mavjud.", reply_markup=get_admin_keyboard())
                return
            else:
                await reply_safe(
                    update,
                    "⚠️ *Noto'g'ri format!*\n"
                    "Iltimos, quyidagicha ajratib yozing:\n"
                    "`login | parol`\n\n"
                    "Misol: `matematika_ustoz | ustoz2026`\n"
                    "Yoki bekor qilish uchun /cancel yozing.",
                    parse_mode='Markdown'
                )
                return

    # ── Tugmalar ──
    if text.lower() in ["admin", "/admin", "👑 admin paneli"]:
        await cmd_admin(update, context)
    elif text in ["➕ O'quvchi ochish", "➕ O'quvchi yaratish"]:
        if not db.is_admin(chat_id):
            await reply_safe(update, "❌ Bu bo'lim faqat adminlar uchun.")
            return
        admin_states[chat_id] = 'awaiting_student'
        await reply_safe(
            update,
            "👤 *YANGI O'QUVCHI HISOBINI OCHISH*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "O'quvchi ma'lumotlarini bitta xabarda quyidagicha yuboring:\n\n"
            "`Ism Familiya | login | parol`\n\n"
            "Misol:\n"
            "`Aziz Rahimov | aziz123 | aziz2026`\n\n"
            "_Bekor qilish uchun:_ /cancel",
            parse_mode='Markdown'
        )
    elif text in ["👨‍🏫 O'qituvchi ochish", "👨‍🏫 O'qituvchi yaratish"]:
        if not db.is_admin(chat_id):
            await reply_safe(update, "❌ Bu bo'lim faqat adminlar uchun.")
            return
        admin_states[chat_id] = 'awaiting_teacher'
        await reply_safe(
            update,
            "👨‍🏫 *YANGI O'QITUVCHI HISOBINI OCHISH*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "O'qituvchi login va parolini quyidagicha yuboring:\n\n"
            "`login | parol`\n\n"
            "Misol:\n"
            "`matematika_ustoz | ustoz777`\n\n"
            "_Bekor qilish uchun:_ /cancel",
            parse_mode='Markdown'
        )
    elif text in ["👥 O'qituvchilar ro'yxati"]:
        if not db.is_admin(chat_id):
            await reply_safe(update, "❌ Bu bo'lim faqat adminlar uchun.")
            return
        teachers = db.get_teachers()
        if not teachers:
            await reply_safe(update, "📭 Hali o'qituvchilar qo'shilmagan.")
            return
        msg = "👨‍🏫 *O'QITUVCHILAR RO'YXATI:*\n━━━━━━━━━━━━━━━━━━━━━━\n"
        for idx, t in enumerate(teachers, 1):
            connected = "🟢 (Telegram ulangan)" if t.get('telegram_chat_id') else "⚪ (Telegram ulanmagan)"
            msg += f"*{idx}. Login:* `{t['login']}`\n   └── {connected}\n"
        await reply_safe(update, msg, parse_mode='Markdown')
    elif text in ["📊 Tizim statistikasi"]:
        if not db.is_admin(chat_id):
            await reply_safe(update, "❌ Bu bo'lim faqat adminlar uchun.")
            return
        stats = db.get_stats()
        text_stats = (
            "📊 *«RAQAMLI DAFTAR» TIZIM STATISTIKASI*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👨‍🎓 *Jami o'quvchilar:* {stats['students']} nafar\n"
            f"👨‍🏫 *Jami o'qituvchilar:* {stats['teachers']} nafar\n"
            f"⭐ *Qo'yilgan baholar:* {stats['grades']} ta\n"
            f"📝 *Uy vazifalari:* {stats['tasks']} ta\n"
            f"📸 *Yuklangan daftarlar:* {stats['images']} ta\n"
            f"👑 *Administratorlar:* {stats['admins']} nafar\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 *Sayt holati:* Faol (Online)\n"
            f"🛡️ *Supabase Auth:* Ulangan ✅"
        )
        await reply_safe(update, text_stats, parse_mode='Markdown')
    elif text in ["🔑 Admin kodi"]:
        if not db.is_admin(chat_id):
            await reply_safe(update, "❌ Bu bo'lim faqat adminlar uchun.")
            return
        await reply_safe(
            update,
            f"🔑 *ADMIN MAXFIY KODI*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Joriy maxfiy kod: `{ADMIN_CODE}`\n\n"
            f"Yangi admin qo'shish uchun unga quyidagi buyruqni botga yuborishini ayting:\n"
            f"`/admin {ADMIN_CODE}`",
            parse_mode='Markdown'
        )
    elif text in ["🔙 Asosiy menyu", "Asosiy menyu"]:
        admin_states.pop(chat_id, None)
        await reply_safe(update, "🏠 Asosiy menyudasiz.", reply_markup=get_main_keyboard(chat_id))
    elif text in ["👨‍🏫 O'qituvchi boshqaruvi"]:
        teacher = db.get_teacher_by_chat_id(update.effective_chat.id)
        if teacher or db.is_admin(chat_id):
            await reply_safe(
                update,
                "👨‍🏫 *O'qituvchi Boshqaruvi:*\n\n"
                "Buyruqlar:\n"
                "• `/oquvchi_qosh Ism login parol` — O'quvchi kiritish\n"
                "• `/oquvchilar` — Ro'yxatni ko'rish\n"
                "• `/baho login fan baho` — Baho qo'yish\n"
                "• `/rasmlar` — Daftarlar rasmlari",
                parse_mode='Markdown'
            )
        else:
            await reply_safe(update, "Siz o'qituvchi sifatida kirmagansiz. `/oqituvchi login parol`")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """O'quvchi rasm yuborganda uni saqlash va o'qituvchiga uzatish."""
    if not update.message or not update.message.photo:
        return
    chat_id = update.effective_chat.id
    student = db.get_student_by_chat_id(chat_id)

    if not student:
        await reply_safe(
            update,
            "⚠️ *Rasm saqlanishi uchun avval o'quvchi hisobingizga kiring!*\n"
            "`/kirish login parol`",
            parse_mode='Markdown'
        )
        return

    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    filename = f"{student['id']}_{uuid.uuid4().hex[:8]}.jpg"
    filepath = UPLOADS_DIR / filename
    await file.download_to_drive(str(filepath))

    tasks = db.get_tasks()
    task_id = tasks[-1]['id'] if tasks else None
    db.save_image(task_id, student['id'], filename, photo.file_id)

    task_text = f" ({tasks[-1]['text'][:30]}...)" if tasks and task_id else ""
    await reply_safe(
        update,
        f"✅ *Uyga vazifa rasmi qabul qilindi!*{task_text}\n"
        f"O'qituvchingiz daftaringizni tekshirishi uchun uzatildi. 📸👏",
        parse_mode='Markdown'
    )

    # Barcha o'qituvchilarga rasm xabarnomasini yuborish
    try:
        conn = db._conn()
        teachers = conn.execute(
            'SELECT telegram_chat_id FROM teachers WHERE telegram_chat_id IS NOT NULL'
        ).fetchall()
        conn.close()
        for t in teachers:
            try:
                with open(filepath, 'rb') as f:
                    await context.bot.send_photo(
                        chat_id=t['telegram_chat_id'],
                        photo=f,
                        caption=f"📝 *Yangi uy vazifasi rasmi!*\n👤 O'quvchi: *{student['name']}*{task_text}",
                        parse_mode='Markdown'
                    )
            except Exception:
                pass
    except Exception as e:
        logger.error(f"O'qituvchiga rasm yuborishda xato: {e}")


def _grade_emoji(grade):
    return {5: '5️⃣ 🟢', 4: '4️⃣ 🔵', 3: '3️⃣ 🟡', 2: '2️⃣ 🔴'}.get(grade, str(grade))


# ══════════════════════════════════════════════════════════════
#                   KUNDALIK BILDIRISHNOMA
# ══════════════════════════════════════════════════════════════

async def send_daily_grades(bot):
    logger.info("Kundalik baho bildirishnomasi ishga tushdi.")
    try:
        students = db.get_students_with_chat_id()
        for student in students:
            try:
                today_grades = db.get_today_grades(student['id'])
                if not today_grades:
                    continue
                text = f"📊 *Bugungi baholaringiz — {student['name']}:*\n━━━━━━━━━━━━━━━━━━━━━━\n"
                for g in today_grades:
                    emoji = _grade_emoji(g['grade'])
                    text += f"📘 *{g['subject']}:* {emoji}\n"
                avg = db.get_student_average(student['id'])
                if avg:
                    text += f"\n📈 *Umumiy o'rtacha ball:* *{avg:.2f}*"
                await send_safe(
                    bot,
                    chat_id=student['telegram_chat_id'],
                    text=text,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Bildirishnoma ({student.get('login')}): {e}")
    except Exception as err:
        logger.error(f"send_daily_grades umumiy xatosi: {err}")


# ══════════════════════════════════════════════════════════════
#                            MAIN
# ══════════════════════════════════════════════════════════════

async def main():
    if not TOKEN:
        logger.error("BOT_TOKEN muhit o'zgaruvchisi topilmadi! .env faylni tekshiring.")
        return

    # ── Keep-Alive fon jarayoni (Render Free tier uxlamasligi uchun) ──
    start_keep_alive()

    # ── Flask Web Server ──
    flask_thread = threading.Thread(
        target=lambda: flask_app.run(
            host='0.0.0.0', port=PORT,
            debug=False, use_reloader=False
        ),
        daemon=True,
        name="flask_server"
    )
    flask_thread.start()
    logger.info(f"✅ Web server ishga tushdi: {SITE_URL}")

    # ── Telegram Bot ──
    request_config = HTTPXRequest(
        connection_pool_size=10,
        connect_timeout=20.0,
        read_timeout=30.0,
        write_timeout=20.0
    )
    application = (
        Application.builder()
        .token(TOKEN)
        .request(request_config)
        .build()
    )

    # Global xatoliklar ushlovchisi (bot to'xtab qolmasligi uchun)
    application.add_error_handler(global_error_handler)

    application.add_handler(CommandHandler("admin", cmd_admin))
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("kirish", cmd_kirish))
    application.add_handler(CommandHandler("oqituvchi", cmd_oqituvchi))
    application.add_handler(CommandHandler("baholarim", cmd_baholarim))
    application.add_handler(CommandHandler("oquvchi_qosh", cmd_oquvchi_qosh))
    application.add_handler(CommandHandler("oquvchilar", cmd_oquvchilar))
    application.add_handler(CommandHandler("baho", cmd_baho))
    application.add_handler(CommandHandler("rasmlar", cmd_rasmlar))
    application.add_handler(CommandHandler("chiqish", cmd_chiqish))

    # Jadval callbacklari
    application.add_handler(CallbackQueryHandler(schedule_callback_handler, pattern="^sched_"))

    # Rasm yuborish
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Barcha menyu matnli tugmalari
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_buttons))

    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logger.warning(f"Webhook tozalashda: {e}")

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    try:
        commands = [
            BotCommand("start", "📓 Boshlash va menyuni ochish"),
            BotCommand("baholarim", "📊 Baholarim va o'rtacha ball"),
            BotCommand("kirish", "👨‍🎓 O'quvchi hisobiga kirish"),
            BotCommand("help", "ℹ️ Yordam va ma'lumot"),
            BotCommand("oqituvchi", "👨‍🏫 O'qituvchi hisobiga kirish"),
            BotCommand("oquvchi_qosh", "➕ Yangi o'quvchi qo'shish"),
            BotCommand("oquvchilar", "📋 O'quvchilar ro'yxati"),
            BotCommand("baho", "⭐ O'quvchiga baho qo'yish"),
            BotCommand("rasmlar", "🖼 Daftarlar rasmlari"),
            BotCommand("chiqish", "🚪 Hisobdan chiqish")
        ]
        await application.bot.set_my_commands(commands)
        if SITE_URL.startswith('https://'):
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Play",
                    web_app=WebAppInfo(url=SITE_URL)
                )
            )
    except Exception as e:
        logger.warning(f"Bot menyusini sozlashda xato: {e}")

    logger.info("✅ Telegram bot ishga tushdi!")

    # ── APScheduler (14:00 Toshkent vaqti) ──
    tz = pytz.timezone('Asia/Tashkent')
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        send_daily_grades,
        CronTrigger(hour=14, minute=0, timezone=tz),
        args=[application.bot],
        id='daily_grades',
        replace_existing=True
    )
    scheduler.start()
    logger.info("✅ Kundalik bildirishnoma (14:00 Toshkent) sozlandi.")

    print("\n" + "=" * 55)
    print("[OK] RAQAMLI DAFTAR TIZIMI MUVAFFAQIYATLI ISHGA TUSHDI!")
    print(f"[*] Veb sayt: {SITE_URL}")
    print("[*] Telegram Bot: Faol (Polling rejimida)")
    print("=" * 55 + "\n")

    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("To'xtatilmoqda...")
    finally:
        scheduler.shutdown(wait=False)
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        logger.info("Bot va server to'xtatildi.")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
