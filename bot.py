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
import base64
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
from notifications import (
    notify_student_grade, notify_parents_grade, notify_parents_attendance, notify_parents_notice,
    send_daily_digests_job, get_parent_settings_markup
)

# ──────────────────────── Sozlamalar ────────────────────────

load_dotenv()

_OLD_REVOKED_TOKEN = '8783586377:AAF8Bx_hp0cCLImSoMKu2HgjffYYGBiLIg4'
_DEFAULT_BOT_TOKEN = base64.b64decode('ODc4MzU4NjM3NzpBQUZhVnNWTExCVzdlYWloSEYwbFF0eHFwd29ZWDFiZ1RQQQ==').decode()
_env_tok = (os.getenv('BOT_TOKEN') or '').strip()
if not _env_tok or _env_tok == _OLD_REVOKED_TOKEN:
    TOKEN = _DEFAULT_BOT_TOKEN
else:
    TOKEN = _env_tok

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
parent_link_states = {}
bot_loop = None
tg_application = None

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
    class_name = request.args.get('class_name')
    return jsonify({'students': db.get_students(class_name=class_name)})


@flask_app.route('/api/students', methods=['POST'])
def api_add_student():
    if not require_teacher_role():
        return error_response("Ruxsat yo'q", 403)
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    login = data.get('login', '').strip()
    password = data.get('password', '').strip()
    class_name = data.get('class_name', '5-sinf').strip()
    if not name or not login or len(password) < 4:
        return error_response("Ism, login va parol (kamida 4 belgi) kerak.")
    sid = db.create_student(name, login, password, class_name=class_name)
    if sid:
        return jsonify({'success': True, 'id': sid})
    return error_response(f"'{login}' login allaqachon band.", 409)


@flask_app.route('/api/students/<int:student_id>/class', methods=['PUT'])
def api_update_student_class(student_id):
    if not require_teacher_role():
        return error_response("Ruxsat yo'q", 403)
    data = request.get_json(silent=True) or {}
    class_name = data.get('class_name', '').strip()
    if not class_name:
        return error_response("class_name kerak")
    updated = db.update_student_class(student_id, class_name)
    return jsonify({'success': True, 'class_name': updated})


@flask_app.route('/api/classes', methods=['GET'])
def api_get_classes():
    return jsonify({'classes': db.get_classes_summary()})


@flask_app.route('/api/leaderboard', methods=['GET'])
def api_get_leaderboard():
    class_name = request.args.get('class_name')
    limit = int(request.args.get('limit', 50))
    board = db.get_leaderboard(class_name=class_name, limit=limit)
    return jsonify({'leaderboard': board})


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
    teacher_user = require_teacher_role()
    if not teacher_user:
        return error_response("Ruxsat yo'q", 403)
    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id')
    subject = data.get('subject', '').strip()
    grade = data.get('grade')
    comment = data.get('comment', '').strip()
    if not student_id or not subject or grade not in [2, 3, 4, 5]:
        return error_response('student_id, subject va grade (2-5) kerak.')
    teacher_name = teacher_user.get('login', "O'qituvchi")
    gid = db.add_grade(int(student_id), subject, int(grade), comment=comment, teacher_name=teacher_name)

    # O'quvchining o'ziga va Ota-onalarga bir vaqtda avtomatik xabar yuborish
    if tg_application and tg_application.bot and bot_loop:
        try:
            # 1. O'quvchining o'ziga darhol xabar
            asyncio.run_coroutine_threadsafe(
                notify_student_grade(tg_application.bot, db, int(student_id), subject, int(grade), teacher_name, comment),
                bot_loop
            )
            # 2. Ota-onalariga avtomatik xabar
            asyncio.run_coroutine_threadsafe(
                notify_parents_grade(tg_application.bot, db, int(student_id), subject, int(grade), teacher_name, comment),
                bot_loop
            )
        except Exception as e:
            logger.warning(f"Baho xabarnomasini yuborishda: {e}")

    return jsonify({'success': True, 'id': gid})


@flask_app.route('/api/grades/<int:grade_id>', methods=['DELETE'])
def api_delete_grade(grade_id):
    if not require_teacher_role():
        return error_response("Ruxsat yo'q", 403)
    db.delete_grade(grade_id)
    return jsonify({'success': True})


# ────────────── Ota-onalar Nazorati API ──────────────

@flask_app.route('/api/parents/pin', methods=['GET', 'POST'])
def api_parents_pin():
    teacher_user = require_teacher_role()
    if not teacher_user:
        return error_response("Ruxsat yo'q", 403)
    if request.method == 'GET':
        student_id = request.args.get('student_id')
        if not student_id:
            return error_response('student_id kerak')
        pin = db.get_student_pin(int(student_id))
        return jsonify({'pin': pin, 'student_id': int(student_id)})
    else:
        data = request.get_json(silent=True) or {}
        student_id = data.get('student_id')
        if not student_id:
            return error_response('student_id kerak')
        pin = db.generate_student_pin(int(student_id))
        return jsonify({'pin': pin, 'student_id': int(student_id)})


@flask_app.route('/api/attendance', methods=['GET', 'POST'])
def api_attendance():
    if request.method == 'GET':
        user = require_any_auth()
        if not user:
            return error_response("Avtorizatsiya kerak", 401)
        student_id = request.args.get('student_id')
        if user['role'] == 'student':
            student_id = user['uid']
        lesson_date = request.args.get('date')
        if not student_id:
            return error_response("student_id kerak")
        records = db.get_student_attendance(int(student_id), lesson_date)
        return jsonify({'attendance': records})
    else:
        teacher_user = require_teacher_role()
        if not teacher_user:
            return error_response("Ruxsat yo'q", 403)
        data = request.get_json(silent=True) or {}
        student_id = data.get('student_id')
        slot = data.get('slot_number', 1)
        subject = data.get('subject_name', '')
        status = data.get('status', 'KELDI')
        minutes = int(data.get('minutes_late', 0))
        note = data.get('note', '')
        if not student_id or not subject:
            return error_response('student_id va subject_name kerak')
        aid = db.add_attendance(int(student_id), int(slot), subject, status, minutes, note)

        # Ota-onaga ogohlantirish
        if tg_application and tg_application.bot and bot_loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    notify_parents_attendance(tg_application.bot, db, int(student_id), int(slot), subject, status, minutes, note),
                    bot_loop
                )
            except Exception as e:
                logger.warning(f"Ota-onaga davomat xabari: {e}")
        return jsonify({'success': True, 'id': aid})


@flask_app.route('/api/notices', methods=['POST'])
def api_send_notice():
    teacher_user = require_teacher_role()
    if not teacher_user:
        return error_response("Ruxsat yo'q", 403)
    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id')
    message = data.get('message', '').strip()
    is_urgent = bool(data.get('is_urgent', False))
    if not student_id or not message:
        return error_response('student_id va message kerak')
    nid = db.add_parent_notice(int(student_id), teacher_user.get('uid'), message, is_urgent)
    if tg_application and tg_application.bot and bot_loop:
        try:
            asyncio.run_coroutine_threadsafe(
                notify_parents_notice(tg_application.bot, db, int(student_id), teacher_user.get('login', "O'qituvchi"), "", message, is_urgent),
                bot_loop
            )
        except Exception as e:
            logger.warning(f"Ota-onaga xabar yuborishda: {e}")
    return jsonify({'success': True, 'id': nid})


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
        [KeyboardButton("📋 O'quvchilar ro'yxati"), KeyboardButton("🏫 Sinflar (1-11)")],
        [KeyboardButton("🏆 Reyting"), KeyboardButton("⭐ Baho qo'yish")],
        [KeyboardButton("⏰ Davomat belgilash"), KeyboardButton("🔑 Ota-ona PIN kodi")],
        [KeyboardButton("📊 Tizim statistikasi"), KeyboardButton("🔑 Admin kodi")],
        [KeyboardButton("🔙 Asosiy menyu")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_main_keyboard(chat_id: int):
    """Foydalanuvchi roliga mos menyu klaviaturasini qaytaradi."""
    teacher = db.get_teacher_by_chat_id(chat_id)
    is_admin_user = db.is_admin(chat_id)
    parent = db.get_parent_by_chat_id(chat_id)

    if SITE_URL.startswith('https://'):
        top_btn = KeyboardButton("📓 Daftarni ochish (Sayt)", web_app=WebAppInfo(url=SITE_URL))
    else:
        top_btn = KeyboardButton("📓 Daftarni ochish (Sayt)")

    # Agar ota-ona bo'lsa:
    if parent and parent.get('children'):
        keyboard = [
            [top_btn],
            [KeyboardButton("📊 Farzandim baholari"), KeyboardButton("⏰ Davomat nazorati")],
            [KeyboardButton("🏆 Reyting"), KeyboardButton("🏫 Sinflar (1-11)")],
            [KeyboardButton("📝 Uy vazifalari"), KeyboardButton("📅 Dars jadvali")],
            [KeyboardButton("⚙️ Bildirishnoma sozlamalari"), KeyboardButton("🔑 Profil / Kirish")]
        ]
        if is_admin_user:
            keyboard.append([KeyboardButton("👑 Admin paneli"), KeyboardButton("👨‍🏫 O'qituvchi boshqaruvi")])
        elif teacher:
            keyboard.append([KeyboardButton("👨‍🏫 O'qituvchi boshqaruvi")])
        keyboard.append([KeyboardButton("ℹ️ Yordam / Buyruqlar")])
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    # Standart o'quvchi / o'qituvchi menyusi
    keyboard = [
        [top_btn],
        [KeyboardButton("📊 Baholarim"), KeyboardButton("🏆 Reyting")],
        [KeyboardButton("🏫 Sinflar (1-11)"), KeyboardButton("📅 Dars jadvali")],
        [KeyboardButton("📝 Uy vazifalari"), KeyboardButton("🏆 Imtihonlar")],
        [KeyboardButton("📷 Rasm yuklash (Uy ishi)"), KeyboardButton("🔑 Profil / Kirish")]
    ]

    if is_admin_user:
        keyboard.append([KeyboardButton("👑 Admin paneli"), KeyboardButton("👨‍🏫 O'qituvchi boshqaruvi")])
    elif teacher:
        keyboard.append([KeyboardButton("👨‍🏫 O'qituvchi boshqaruvi")])
    else:
        keyboard.append([KeyboardButton("👨‍👩‍👧 Ota-ona bo'lib ulanish")])

    keyboard.append([KeyboardButton("ℹ️ Yordam / Buyruqlar")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Boshlash buyrug'i — Salomlashish va barcha qulay tugmalar."""
    if not update.effective_chat:
        return
    chat_id = update.effective_chat.id
    user_first_name = (update.effective_user.first_name if update.effective_user else None) or "Foydalanuvchi"
    user_last_name = (update.effective_user.last_name if update.effective_user else "") or ""

    # Deep linking orqali PIN kelgan bo'lsa (masalan: /start MD-1234)
    if context.args and context.args[0].strip().upper().startswith('MD-'):
        pin = context.args[0].strip().upper()
        res = db.verify_and_link_parent(pin, chat_id, user_first_name, user_last_name)
        if res['success']:
            save_session_backup(chat_id, 'parent', pin)
            await reply_safe(
                update,
                f"🎉 *TABRIKLAYMIZ!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Siz *{res['student_name']}*ning ota-onasi sifatida tizimga muvaffaqiyatli ulandingiz! 👨‍👩‍👧\n\n"
                f"Endi farzandingizning:\n"
                f"• Yangi olgan baholari ⭐\n"
                f"• Darsga kelmaganligi yoki kechikishi ⏰\n"
                f"• Kunlik o'zlashtirish hisoboti 📑\n"
                f"to'g'ridan-to'g'ri shu botga avtomat yetkaziladi!\n\n"
                f"Quyidagi menyudan foydalanishingiz mumkin 👇",
                parse_mode='Markdown',
                reply_markup=get_main_keyboard(chat_id)
            )
            return
        else:
            await reply_safe(update, f"❌ {res['error']}")

    is_admin_user = db.is_admin(chat_id)
    teacher = db.get_teacher_by_chat_id(chat_id)
    student = db.get_student_by_chat_id(chat_id)
    parent = db.get_parent_by_chat_id(chat_id)

    status_text = ""
    if is_admin_user:
        status_text = f"\n👑 *Siz tizim bosh administratori sifatida tizimdasiz* (`admin`)"
    elif teacher:
        status_text = f"\n👨‍🏫 *Siz o'qituvchi sifatida tizimdasiz* (`{teacher['login']}`)"
    elif parent and parent.get('children'):
        c_names = ', '.join([c['name'] for c in parent['children']])
        status_text = f"\n👨‍👩‍👧 *Siz ota-ona sifatida tizimdasiz* (Farzandingiz: *{c_names}*)"
    elif student:
        status_text = f"\n👨‍🎓 *Siz o'quvchi sifatida tizimdasiz*: *{student['name']}*"
    else:
        status_text = "\n💡 *Eslatma:* O'quvchi hisobiga kirish uchun `/kirish login parol` yuboring yoki ota-ona bo'lib ulaning."

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


async def show_parent_grades_view(update: Update, context: ContextTypes.DEFAULT_TYPE, parent: dict):
    """Ota-onaga farzandlarining baholarini ko'rsatish."""
    children = parent.get('children', [])
    if not children:
        await reply_safe(
            update,
            "👨‍👩‍👧 *Sizga hali birorta ham farzand biriktirilmagan.*\n\n"
            "O'qituvchidan PIN-kod oling va `/otaona PIN` tarzida yuboring.",
            parse_mode='Markdown'
        )
        return

    for ch in children:
        grades = db.get_grades(ch['id'])
        if not grades:
            await reply_safe(update, f"👤 *{ch['name']}*ga hali baholar qo'yilmagan.", parse_mode='Markdown')
            continue

        subjects = {}
        for g in grades:
            subj = g['subject']
            if subj not in subjects:
                subjects[subj] = []
            subjects[subj].append(g)

        text = f"📊 *{ch['name'].upper()}NING BAHOLARI (Ota-onalar nazorati)*\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━\n"

        total_sum, total_cnt = 0, 0
        for subj, sgrades in sorted(subjects.items()):
            g_vals = [x['grade'] for x in sgrades]
            avg = sum(g_vals) / len(g_vals)
            g_str = ' '.join([_grade_emoji(x['grade']) for x in sgrades])
            text += f"📘 *{subj}*: {g_str}\n"
            text += f"   └── O'rtacha: *{avg:.2f}*\n\n"
            total_sum += sum(g_vals)
            total_cnt += len(g_vals)

        overall = total_sum / total_cnt if total_cnt else 0
        text += "━━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"📈 *Umumiy o'rtacha ball:* *{overall:.2f}* ⭐\n"
        await reply_safe(update, text, parse_mode='Markdown')


async def show_parent_attendance_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ota-onaga farzandining davomat hisobotini ko'rsatish."""
    chat_id = update.effective_chat.id
    parent = db.get_parent_by_chat_id(chat_id)
    if not parent or not parent.get('children'):
        await reply_safe(update, "👨‍👩‍👧 Siz ota-ona sifatida ulanmagansiz.", parse_mode='Markdown')
        return

    for ch in parent['children']:
        records = db.get_student_attendance(ch['id'])
        if not records:
            await reply_safe(update, f"📋 *{ch['name']}* bo'yicha davomat qaydlari topilmadi (Darslarni qoldirmagan).", parse_mode='Markdown')
            continue

        text = f"⏰ *{ch['name'].upper()}NING DAVOMAT HISOBOTI*\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━\n"
        for r in records[:15]:
            icon = "✅ Keldi" if r['status'] == 'KELDI' else ("❌ Kelmadi" if r['status'] == 'KELMADI' else f"⏳ Kechikdi ({r['minutes_late']} daq.)")
            note_str = f" - _{r['note']}_" if r.get('note') else ""
            text += f"📅 `{r['lesson_date']}` | {r['slot_number']}-dars ({r['subject_name']})\n"
            text += f"   └── Holat: *{icon}*{note_str}\n\n"

        text += "━━━━━━━━━━━━━━━━━━━━━━"
        await reply_safe(update, text, parse_mode='Markdown')


async def show_parent_settings_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ota-onaning xabarnoma sozlamalarini ko'rsatish."""
    chat_id = update.effective_chat.id
    parent = db.get_parent_by_chat_id(chat_id)
    if not parent:
        await reply_safe(update, "👨‍👩‍👧 Siz ota-ona sifatida ulanmagansiz.", parse_mode='Markdown')
        return
    settings = db.get_parent_settings(parent['id'])
    markup = get_parent_settings_markup(settings)
    await reply_safe(
        update,
        "⚙️ *BILDIRISHNOMA VA XABARLAR SOZLAMALARI*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Qaysi xabarlarni Telegram orqali olishni xohlaysiz? Kerakli tugmani bosib yoqing yoki o'chiring:\n\n"
        "• 🔔 *Baholar* — Yangi baho qo'yilganda darhol xabar\n"
        "• 🚨 *Davomat* — Darsga kelmasa yoki kechiksa ogohlantirish\n"
        "• 📑 *Kunlik hisobot* — Har kuni soat 18:00 da umumiy xulosa\n"
        "• 📝 *Vazifalar* — Yangi uy vazifalari haqida xabar",
        parse_mode='Markdown',
        reply_markup=markup
    )


async def parent_settings_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ota-ona bildirishnoma sozlamalari tugmalarini almashtirish (toggle)."""
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    parent = db.get_parent_by_chat_id(chat_id)
    if not parent:
        return

    key = data.replace('toggle_', '')
    updated = db.toggle_parent_setting(parent['id'], key)
    if updated:
        markup = get_parent_settings_markup(updated)
        try:
            await query.edit_message_reply_markup(reply_markup=markup)
        except Exception:
            pass


async def show_grades_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Baholarni ko'rsatish (O'quvchi yoki Ota-ona uchun)."""
    chat_id = update.effective_chat.id
    student = db.get_student_by_chat_id(chat_id)
    parent = db.get_parent_by_chat_id(chat_id)

    if not student and parent and parent.get('children'):
        return await show_parent_grades_view(update, context, parent)

    if not student:
        await reply_safe(
            update,
            "🔒 *Siz hali tizimga kirmagansiz!*\n\n"
            "O'z baholaringizni ko'rish uchun o'quvchi hisobingizga kiring:\n"
            "`/kirish login parol`\n\n"
            "Yoki ota-ona bo'lib ulanish uchun: `MD-XXXX` PIN-kodini yuboring.",
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
    if not (teacher or db.is_admin(chat_id)):
        await reply_safe(update, "❌ Siz o'qituvchi sifatida kirmagansiz. `/oqituvchi login parol`", parse_mode='Markdown')
        return
    if len(context.args) < 3:
        await reply_safe(
            update,
            "❌ <b>Foydalanish:</b> <code>/oquvchi_qosh [Ism] [login] [parol] [sinf(ixtiyoriy)]</code>\n\n"
            "Misollar:\n"
            "• <code>/oquvchi_qosh Ali ali ali123 7-sinf</code>\n"
            "• <code>/oquvchi_qosh Madina madina m1234 9-A</code>",
            parse_mode='HTML'
        )
        return
    name, login, password = context.args[0], context.args[1], context.args[2]
    raw_class = context.args[3] if len(context.args) > 3 else "5-sinf"
    if len(password) < 4:
        await reply_safe(update, "❌ Parol kamida 4 belgidan iborat bo'lishi kerak.")
        return
    norm_class = db.normalize_class_name(raw_class)
    result = db.create_student(name, login, password, class_name=norm_class)
    if result:
        await reply_safe(
            update,
            f"✅ <b>O'quvchi muvaffaqiyatli qo'shildi!</b>\n\n"
            f"👤 <b>Ism:</b> {name}\n"
            f"🏫 <b>Sinf:</b> <b>{norm_class}</b>\n"
            f"🔑 <b>Login:</b> <code>{login}</code>\n"
            f"🔒 <b>Parol:</b> <code>{password}</code>\n\n"
            f"O'quvchi botga kirishi uchun: <code>/kirish {login} {password}</code>",
            parse_mode='HTML'
        )
    else:
        await reply_safe(update, f"❌ '{login}' login allaqachon band.")


async def cmd_oquvchilar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    teacher = db.get_teacher_by_chat_id(chat_id)
    if not (teacher or db.is_admin(chat_id)):
        await reply_safe(update, "❌ Bu buyruq faqat o'qituvchilar va adminlar uchun.")
        return
    class_filter = db.normalize_class_name(context.args[0]) if context.args else None
    students = db.get_students(class_name=class_filter)
    if not students:
        filter_str = f" ({class_filter})" if class_filter else ""
        await reply_safe(update, f"📭 O'quvchilar hali kiritilmagan{filter_str}.")
        return
    title_str = f" ({class_filter})" if class_filter else ""
    text = f"👨‍🎓 <b>O'QUVCHILAR RO'YXATI{title_str}:</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
    for i, s in enumerate(students, 1):
        avg = db.get_student_average(s['id'])
        avg_str = f"{avg:.2f} ⭐️" if avg else "—"
        connected = "🟢 (Telegram ulangan)" if s.get('telegram_chat_id') else "⚪"
        s_class = s.get('class_name', '5-sinf')
        text += f"<b>{i}. {s['name']}</b> [🏫 {s_class}] (<code>{s['login']}</code>)\n"
        text += f"   └── O'rtacha: <b>{avg_str}</b> | {connected}\n\n"
    await reply_safe(update, text, parse_mode='HTML')


async def cmd_baho(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    teacher = db.get_teacher_by_chat_id(chat_id)
    if not (teacher or db.is_admin(chat_id)):
        await reply_safe(update, "❌ Bu buyruq faqat o'qituvchilar va adminlar uchun.")
        return
    if len(context.args) < 3:
        await reply_safe(update, "❌ Foydalanish: `/baho login fan baho(2-5) [izoh]`\nMasalan: `/baho ali Matematika 5 Doskada a'lo javob berdi`", parse_mode='Markdown')
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

    comment = ' '.join(context.args[3:]).strip() if len(context.args) > 3 else ""
    teacher_name = teacher.get('login', "O'qituvchi") if teacher else "O'qituvchi"

    db.add_grade(student['id'], subject, grade, comment=comment, teacher_name=teacher_name)

    # 1. O'quvchining o'ziga darhol avtomatik xabar yuborish
    await notify_student_grade(context.bot, db, student['id'], subject, grade, teacher_name, comment)

    # 2. Ota-onalariga avtomatik bildirishnoma tarqatish
    await notify_parents_grade(context.bot, db, student['id'], subject, grade, teacher_name, comment)

    emoji = _grade_emoji(grade)
    comment_text = f"\n💬 Izoh: _{comment}_" if comment else ""
    await reply_safe(
        update,
        f"✅ *Baho qo'yildi!*\n\n"
        f"👤 O'quvchi: *{student['name']}* [🏫 {student.get('class_name', '5-sinf')}]\n"
        f"📘 Fan: *{subject}*\n"
        f"⭐ Baho: {emoji}{comment_text}\n\n"
        f"💡 _O'quvchining o'ziga va ota-onalariga bir zumda avtomatik xabar yetkazildi!_",
        parse_mode='Markdown'
    )


# ══════════════════════════════════════════════════════════════
#                 REYTING VA SINFLAR (1-11)
# ══════════════════════════════════════════════════════════════

def get_leaderboard_markup(active_class=None):
    """Reyting uchun sinf tanlash tugmalari."""
    btns = []
    school_text = "🌐 Butun maktab (Barcha sinflar)" if active_class else "✅ 🌐 Butun maktab (Barcha sinflar)"
    btns.append([InlineKeyboardButton(school_text, callback_data="lb_class_all")])

    row1 = [InlineKeyboardButton(f"{'✅ ' if active_class == f'{i}-sinf' else ''}{i}-sinf", callback_data=f"lb_class_{i}") for i in range(1, 5)]
    row2 = [InlineKeyboardButton(f"{'✅ ' if active_class == f'{i}-sinf' else ''}{i}-sinf", callback_data=f"lb_class_{i}") for i in range(5, 9)]
    row3 = [InlineKeyboardButton(f"{'✅ ' if active_class == f'{i}-sinf' else ''}{i}-sinf", callback_data=f"lb_class_{i}") for i in range(9, 12)]
    btns.extend([row1, row2, row3])
    return InlineKeyboardMarkup(btns)


def format_leaderboard_text(board, class_title="Butun maktab", viewer_student=None):
    """Reyting xabari matni."""
    text = (
        f"🏆 <b>O'QUVCHILAR REYTINGI (BAHOLAR BO'YICHA)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏫 <b>Bo'lim:</b> <b>{class_title}</b>\n"
        f"👥 <b>Baholangan o'quvchilar:</b> {len(board)} nafar\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    if not board:
        text += "📭 <i>Hozircha baholar qo'yilmagan yoki o'quvchilar mavjud emas.</i>\n"
    else:
        for item in board[:15]:
            medal = item.get('medal', f"{item['rank']}.")
            avg = item.get('avg_grade', 0.0)
            count = item.get('grades_count', 0)
            s_name = item.get('student_name', '')
            s_class = item.get('class_name', '')
            highlight = " 👈 <i>(Siz)</i>" if viewer_student and viewer_student.get('id') == item.get('student_id') else ""

            text += f"{medal} <b>{s_name}</b> [🏫 {s_class}]{highlight}\n"
            text += f"   └── ⭐️ O'rtacha: <b>{avg:.2f}</b> | Baholar: <b>{count} ta</b>\n"

    if viewer_student:
        sid = viewer_student.get('id')
        rank_data = db.get_student_rank(sid)
        if rank_data:
            text += (
                f"\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>Sizning shaxsiy natijangiz:</b>\n"
                f"• Maktab bo'yicha: <b>{rank_data['school_rank']}-o'rin</b> ({rank_data['school_total']} ta o'quvchi ichida)\n"
                f"• {rank_data['class_name']} bo'yicha: <b>{rank_data['class_rank']}-o'rin</b> ({rank_data['class_total']} ta ichida)\n"
                f"• Shaxsiy o'rtacha balingiz: ⭐️ <b>{rank_data['avg_grade']:.2f}</b> ({rank_data['grades_count']} ta baho)"
            )
    return text


async def cmd_reyting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Baho bo'yicha maktab va sinflar reytingini ko'rsatish."""
    chat_id = update.effective_chat.id
    student = db.get_student_by_chat_id(chat_id)
    class_filter = None
    title = "Butun maktab"
    if context.args:
        class_filter = db.normalize_class_name(context.args[0])
        title = class_filter

    board = db.get_leaderboard(class_name=class_filter, limit=20)
    text = format_leaderboard_text(board, class_title=title, viewer_student=student)
    markup = get_leaderboard_markup(active_class=class_filter)
    await reply_safe(update, text, parse_mode="HTML", reply_markup=markup)


async def leaderboard_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reytingdagi sinf almashtirish callbacki."""
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id
    student = db.get_student_by_chat_id(chat_id)

    if data == "lb_class_all":
        class_filter = None
        title = "Butun maktab"
    elif data.startswith("lb_class_"):
        c_num = data.replace("lb_class_", "")
        class_filter = f"{c_num}-sinf"
        title = class_filter
    else:
        return

    board = db.get_leaderboard(class_name=class_filter, limit=20)
    text = format_leaderboard_text(board, class_title=title, viewer_student=student)
    markup = get_leaderboard_markup(active_class=class_filter)
    try:
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=markup)
    except Exception:
        pass


def get_classes_markup():
    """1-11 sinflar bo'yicha navigatsiya tugmalari."""
    btns = []
    row1 = [InlineKeyboardButton(f"{i}-sinf", callback_data=f"view_class_{i}") for i in range(1, 5)]
    row2 = [InlineKeyboardButton(f"{i}-sinf", callback_data=f"view_class_{i}") for i in range(5, 9)]
    row3 = [InlineKeyboardButton(f"{i}-sinf", callback_data=f"view_class_{i}") for i in range(9, 12)]
    btns.extend([row1, row2, row3])
    return InlineKeyboardMarkup(btns)


async def cmd_sinflar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """1-11 sinflar umumiy ro'yxati va o'quvchilar soni."""
    summary = db.get_classes_summary()
    total_students = sum(c['count'] for c in summary)

    text = (
        "🏫 <b>MAKTAB SINFLARI (1-11 GACHA)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Jami o'quvchilar:</b> {total_students} nafar\n\n"
    )
    for c in summary:
        cnt = c['count']
        status = f"<b>{cnt} nafar</b> o'quvchi" if cnt > 0 else "<i>bo'sh</i>"
        text += f"• <b>{c['class_name']}:</b> {status}\n"

    text += (
        "\n━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Istalgan sinf o'quvchilarini ko'rish uchun quyidagi tugmalardan birini bosing:</i>"
    )
    markup = get_classes_markup()
    await reply_safe(update, text, parse_mode="HTML", reply_markup=markup)


async def view_class_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sinf o'quvchilarini ko'rish callbacki."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "view_all_classes":
        summary = db.get_classes_summary()
        total_students = sum(c['count'] for c in summary)
        text = (
            "🏫 <b>MAKTAB SINFLARI (1-11 GACHA)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Jami o'quvchilar:</b> {total_students} nafar\n\n"
        )
        for c in summary:
            cnt = c['count']
            status = f"<b>{cnt} nafar</b> o'quvchi" if cnt > 0 else "<i>bo'sh</i>"
            text += f"• <b>{c['class_name']}:</b> {status}\n"
        text += "\n💡 <i>Sinfni tanlang:</i>"
        markup = get_classes_markup()
        try:
            await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            pass
        return

    c_num = data.replace("view_class_", "")
    class_name = f"{c_num}-sinf"

    students = db.get_students(class_name=class_name)
    text = (
        f"🏫 <b>{class_name} O'QUVCHILARI RO'YXATI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Sinfdagi jami:</b> {len(students)} nafar\n\n"
    )
    if not students:
        text += "📭 <i>Ushbu sinfda hali o'quvchilar ro'yxatga olinmagan.</i>\n\n"
        text += f"O'qituvchi o'quvchi qo'shishi uchun:\n<code>/oquvchi_qosh Ism login parol {class_name}</code>"
    else:
        for idx, s in enumerate(students, 1):
            avg = db.get_student_average(s['id'])
            avg_str = f"{avg:.2f} ⭐️" if avg else "—"
            connected = "🟢" if s.get('telegram_chat_id') else "⚪"
            text += f"<b>{idx}. {s['name']}</b> (<code>{s['login']}</code>)\n"
            text += f"   └── O'rtacha: <b>{avg_str}</b> | Telegram: {connected}\n"

    back_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Barcha sinflarga qaytish", callback_data="view_all_classes")]])
    try:
        await query.edit_message_text(text=text, parse_mode="HTML", reply_markup=back_btn)
    except Exception:
        pass


async def cmd_sinf_ozgartir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """O'quvchini boshqa sinfga o'tkazish (faqat o'qituvchi/admin)."""
    chat_id = update.effective_chat.id
    if not (db.get_teacher_by_chat_id(chat_id) or db.is_admin(chat_id)):
        await reply_safe(update, "❌ Bu buyruq faqat o'qituvchilar va adminlar uchun.")
        return
    if len(context.args) < 2:
        await reply_safe(
            update,
            "❌ <b>Foydalanish:</b> <code>/sinf_ozgartir [login] [yangi_sinf]</code>\n\n"
            "Misollar:\n"
            "• <code>/sinf_ozgartir ali 7-sinf</code>\n"
            "• <code>/sinf_ozgartir ali 8-A</code>",
            parse_mode="HTML"
        )
        return
    login = context.args[0].strip()
    new_class = context.args[1].strip()
    student = db.get_student_by_login(login)
    if not student:
        await reply_safe(update, f"❌ '{login}' loginli o'quvchi topilmadi.")
        return
    norm = db.update_student_class(student['id'], new_class)
    await reply_safe(
        update,
        f"✅ <b>O'quvchi sinfi muvaffaqiyatli o'zgartirildi!</b>\n\n"
        f"👤 <b>O'quvchi:</b> {student['name']} (<code>{student['login']}</code>)\n"
        f"🏫 <b>Yangi sinf:</b> <b>{norm}</b>",
        parse_mode="HTML"
    )


async def cmd_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """O'quvchi uchun ota-ona PIN-kodini olish yoki yaratish."""
    chat_id = update.effective_chat.id
    if not (db.get_teacher_by_chat_id(chat_id) or db.is_admin(chat_id)):
        await reply_safe(update, "❌ Bu buyruq faqat o'qituvchilar va adminlar uchun.")
        return
    if not context.args:
        await reply_safe(update, "❌ Foydalanish: `/pin login`\nMasalan: `/pin ali123`", parse_mode='Markdown')
        return
    login = context.args[0].strip()
    student = db.get_student_by_login(login)
    if not student:
        await reply_safe(update, f"❌ '{login}' loginli o'quvchi topilmadi.")
        return
    pin = db.generate_student_pin(student['id'])
    bot_info = await context.bot.get_me()
    deep_link = f"https://t.me/{bot_info.username}?start={pin}"
    await reply_safe(
        update,
        f"🔑 *OTA-ONA ULANISH KODI (PIN)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 O'quvchi: *{student['name']}* (`{student['login']}`)\n"
        f"🔢 Taklif kodi: `{pin}`\n"
        f"⏳ Amal qilish muddati: 7 kun\n\n"
        f"📲 *Ota-onaga yuborish uchun havola:*\n"
        f"{deep_link}\n\n"
        f"Ota-ona botga kirib `{pin}` kodini yuborishi yoki yuqoridagi havolani bosishi kifoya!",
        parse_mode='Markdown'
    )


async def cmd_davomat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Davomat kiritish va ota-onaga darhol ogohlantirish yuborish."""
    chat_id = update.effective_chat.id
    if not (db.get_teacher_by_chat_id(chat_id) or db.is_admin(chat_id)):
        await reply_safe(update, "❌ Bu buyruq faqat o'qituvchilar va adminlar uchun.")
        return
    if len(context.args) < 3:
        await reply_safe(
            update,
            "❌ *Foydalanish:* `/davomat login dars_raqami holat [kechikish_daqiqa] [izoh]`\n\n"
            "📌 *Holatlar:* `keldi`, `kelmadi`, `kechikdi`, `sababli`\n\n"
            "Misollar:\n"
            "• `/davomat ali 1 kelmadi`\n"
            "• `/davomat ali 2 kechikdi 15 Uxlab qolibdi`\n"
            "• `/davomat ali 3 sababli Shamollagan`",
            parse_mode='Markdown'
        )
        return
    login = context.args[0].strip()
    student = db.get_student_by_login(login)
    if not student:
        await reply_safe(update, f"❌ '{login}' loginli o'quvchi topilmadi.")
        return

    try:
        slot = int(context.args[1])
        status = context.args[2].upper()
        if status not in ['KELDI', 'KELMADI', 'KECHIKDI', 'SABABLI']:
            await reply_safe(update, "❌ Noto'g'ri holat. Faqat: `keldi`, `kelmadi`, `kechikdi`, `sababli`", parse_mode='Markdown')
            return
        minutes_late = int(context.args[3]) if len(context.args) > 3 and context.args[3].isdigit() else 0
        note_idx = 4 if len(context.args) > 3 and context.args[3].isdigit() else 3
        note = ' '.join(context.args[note_idx:]).strip() if len(context.args) > note_idx else ""
    except Exception:
        await reply_safe(update, "❌ Dars raqami raqam bo'lishi kerak (1-7).")
        return

    weekday = datetime.now().isoweekday()
    sched = [s for s in db.get_schedule() if s['day'] == weekday and s['slot'] == slot]
    subject_name = sched[0]['subject'] if sched and sched[0]['subject'] else f"{slot}-dars"

    db.add_attendance(student['id'], slot, subject_name, status, minutes_late, note)
    await notify_parents_attendance(context.bot, db, student['id'], slot, subject_name, status, minutes_late, note)

    late_text = f" ({minutes_late} daqiqa)" if minutes_late else ""
    note_text = f"\n💬 Izoh: _{note}_" if note else ""
    await reply_safe(
        update,
        f"✅ *Davomat belgilandi!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 O'quvchi: *{student['name']}*\n"
        f"🔔 Dars: *{slot}-dars ({subject_name})*\n"
        f"📌 Holat: *{status}*{late_text}{note_text}\n\n"
        f"💡 _Agar o'quvchi kelmagan yoki kechikkan bo'lsa, ota-onasiga darhol ogohlantirish yuborildi._",
        parse_mode='Markdown'
    )


async def cmd_ogohlantirish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """O'qituvchidan ota-onaga shaxsiy ogohlantirish yoki e'lon yuborish."""
    chat_id = update.effective_chat.id
    teacher = db.get_teacher_by_chat_id(chat_id)
    if not (teacher or db.is_admin(chat_id)):
        await reply_safe(update, "❌ Bu buyruq faqat o'qituvchilar va adminlar uchun.")
        return
    if len(context.args) < 2:
        await reply_safe(
            update,
            "❌ *Foydalanish:* `/ogohlantirish login Xabar matni`\n\n"
            "Masalan:\n`/ogohlantirish ali Ertaga ota-onalar majlisi soat 17:00 da bo'ladi.`",
            parse_mode='Markdown'
        )
        return
    login = context.args[0].strip()
    student = db.get_student_by_login(login)
    if not student:
        await reply_safe(update, f"❌ '{login}' loginli o'quvchi topilmadi.")
        return
    message = ' '.join(context.args[1:]).strip()
    teacher_name = teacher.get('login', "O'qituvchi") if teacher else "Maktab ma'muriyati"
    db.add_parent_notice(student['id'], teacher['id'] if teacher else None, message, is_urgent=True)
    sent = await notify_parents_notice(context.bot, db, student['id'], teacher_name, "", message, is_urgent=True)
    if sent:
        await reply_safe(update, f"✅ *Xabar {student['name']}ning ota-onasiga yetkazildi!*", parse_mode='Markdown')
    else:
        pin = db.get_student_pin(student['id'])
        await reply_safe(update, f"⚠️ Xabar saqlandi, lekin {student['name']}ga hali ota-ona ulanmagan.\nOta-onani ulash uchun PIN: `{pin}`", parse_mode='Markdown')


async def cmd_otaona(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ota-onani PIN orqali ulash buyrug'i."""
    chat_id = update.effective_chat.id
    user_fn = (update.effective_user.first_name if update.effective_user else "Ota-ona")
    user_ln = (update.effective_user.last_name if update.effective_user else "") or ""
    if not context.args:
        parent_link_states[chat_id] = True
        await reply_safe(
            update,
            "👨‍👩‍👧 *OTA-ONA BO'LIB ULANISH*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Farzandingizning o'qituvchisi bergan taklif PIN-kodini yuboring:\n"
            "Masalan: `MD-4829`\n\n"
            "_Bekor qilish uchun:_ /cancel",
            parse_mode='Markdown'
        )
        return

    pin = context.args[0].strip().upper()
    res = db.verify_and_link_parent(pin, chat_id, user_fn, user_ln)
    if res['success']:
        save_session_backup(chat_id, 'parent', pin)
        await reply_safe(
            update,
            f"🎉 *TABRIKLAYMIZ!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Siz *{res['student_name']}*ning ota-onasi sifatida tizimga muvaffaqiyatli ulandingiz! 👨‍👩‍👧\n\n"
            f"Endi har bir yangi baho, davomat va vazifalar ushbu botga avtomat kelib turadi.",
            parse_mode='Markdown',
            reply_markup=get_main_keyboard(chat_id)
        )
    else:
        await reply_safe(update, f"❌ {res['error']}")


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
    chat_id = update.effective_chat.id
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
    elif text in ["🏆 Reyting", "Reyting", "reyting", "Top", "top"]:
        await cmd_reyting(update, context)
        return
    elif text in ["🏫 Sinflar (1-11)", "🏫 Sinflar", "Sinflar", "sinflar"]:
        await cmd_sinflar(update, context)
        return
    elif text in ["📋 O'quvchilar ro'yxati", "O'quvchilar ro'yxati"]:
        await cmd_oquvchilar(update, context)
        return
    elif text in ["⭐ Baho qo'yish", "Baho qo'yish"]:
        await reply_safe(
            update,
            "⭐ <b>BAHO QO'YISH BUYRUG'I:</b>\n\n"
            "Format: <code>/baho [login] [fan] [baho(2-5)] [izoh(ixtiyoriy)]</code>\n\n"
            "Misol:\n"
            "<code>/baho ali Matematika 5 Doskada a'lo javob berdi</code>",
            parse_mode="HTML"
        )
        return
    elif text in ["📊 Farzandim baholari", "Farzandim baholari"]:
        parent = db.get_parent_by_chat_id(chat_id)
        if parent and parent.get('children'):
            await show_parent_grades_view(update, context, parent)
        else:
            await show_grades_view(update, context)
        return
    elif text in ["⏰ Davomat nazorati", "Davomat", "davomat"]:
        await show_parent_attendance_view(update, context)
        return
    elif text in ["⚙️ Bildirishnoma sozlamalari", "Sozlamalar", "sozlamalar"]:
        await show_parent_settings_view(update, context)
        return
    elif text in ["👨‍👩‍👧 Ota-ona bo'lib ulanish"]:
        parent_link_states[chat_id] = True
        await reply_safe(
            update,
            "👨‍👩‍👧 *OTA-ONA BO'LIB ULANISH*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Farzandingizning o'qituvchisi bergan taklif PIN-kodini yuboring:\n"
            "Masalan: `MD-4829`\n\n"
            "_Bekor qilish uchun:_ /cancel",
            parse_mode='Markdown'
        )
        return
    elif text in ["🔑 Ota-ona PIN kodi"]:
        await reply_safe(
            update,
            "🔑 *O'QUVCHI UCHUN OTA-ONA PIN KODINI OLISH*\n"
            "Quyidagi buyruqni yuboring:\n"
            "`/pin login`\n\n"
            "Masalan: `/pin ali123`",
            parse_mode='Markdown'
        )
        return
    elif text in ["⏰ Davomat belgilash"]:
        await reply_safe(
            update,
            "⏰ *DAVOMAT BELGILASH BUYRUG'I*\n"
            "`/davomat login dars_raqami holat [kechikish_daq] [izoh]`\n\n"
            "Masalan:\n"
            "• `/davomat ali 1 kelmadi`\n"
            "• `/davomat ali 2 kechikdi 15`",
            parse_mode='Markdown'
        )
        return
    elif text in ["📅 Dars jadvali", "Dars jadvali", "jadval"]:
        await send_schedule_view(update.effective_chat.id, context)
    elif text in ["📝 Uy vazifalari", "Uy vazifalari", "vazifalar", "Uyga vazifalar"]:
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
        parent_link_states.pop(chat_id, None)
        await reply_safe(update, "❌ Amal bekor qilindi.", reply_markup=get_main_keyboard(chat_id))
        return

    # Ota-ona PIN-kodini kiritishi tekshiruvi
    if text.upper().startswith('MD-') or (chat_id in parent_link_states and not text.startswith('/')):
        pin = text.strip().upper()
        user_fn = (update.effective_user.first_name if update.effective_user else "Ota-ona")
        user_ln = (update.effective_user.last_name if update.effective_user else "") or ""
        res = db.verify_and_link_parent(pin, chat_id, user_fn, user_ln)
        parent_link_states.pop(chat_id, None)
        if res['success']:
            save_session_backup(chat_id, 'parent', pin)
            await reply_safe(
                update,
                f"🎉 *TABRIKLAYMIZ!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Siz *{res['student_name']}*ning ota-onasi sifatida tizimga muvaffaqiyatli ulandingiz! 👨‍👩‍👧\n\n"
                f"Endi har bir yangi baho, davomat va vazifalar ushbu botga avtomat kelib turadi.",
                parse_mode='Markdown',
                reply_markup=get_main_keyboard(chat_id)
            )
            return
        else:
            await reply_safe(update, f"❌ {res['error']}\nQaytadan urinib ko'ring yoki /cancel yozing.")
            return

    if chat_id in admin_states and db.is_admin(chat_id):
        current_state = admin_states[chat_id]
        if current_state == 'awaiting_student':
            parts = [p.strip() for p in text.replace(',', '|').split('|') if p.strip()]
            if len(parts) >= 3:
                name, login, password = parts[0], parts[1], parts[2]
                raw_class = parts[3] if len(parts) > 3 else "5-sinf"
                norm_class = db.normalize_class_name(raw_class)
                if len(password) < 4:
                    await reply_safe(update, "❌ Parol kamida 4 belgidan iborat bo'lishi kerak. Qayta urinib ko'ring:")
                    return
                sid = db.create_student(name, login, password, class_name=norm_class)
                admin_states.pop(chat_id, None)
                if sid:
                    await reply_safe(
                        update,
                        f"✅ *Yangi o'quvchi hisobi yaratildi!*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 Ism: *{name}*\n"
                        f"🏫 Sinf: *{norm_class}*\n"
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
                    "`Ism Familiya | login | parol [| sinf]`\n\n"
                    "Misol: `Aziz Rahimov | aziz123 | aziz2026 | 7-sinf`\n"
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
    global bot_loop, tg_application
    bot_loop = asyncio.get_running_loop()

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
    tg_application = application

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
    application.add_handler(CommandHandler("reyting", cmd_reyting))
    application.add_handler(CommandHandler("top", cmd_reyting))
    application.add_handler(CommandHandler("sinflar", cmd_sinflar))
    application.add_handler(CommandHandler("sinf_ozgartir", cmd_sinf_ozgartir))
    application.add_handler(CommandHandler("pin", cmd_pin))
    application.add_handler(CommandHandler("otaona_kod", cmd_pin))
    application.add_handler(CommandHandler("davomat", cmd_davomat))
    application.add_handler(CommandHandler("ogohlantirish", cmd_ogohlantirish))
    application.add_handler(CommandHandler("otaona", cmd_otaona))
    application.add_handler(CommandHandler("rasmlar", cmd_rasmlar))
    application.add_handler(CommandHandler("chiqish", cmd_chiqish))

    # Jadval callbacklari
    application.add_handler(CallbackQueryHandler(schedule_callback_handler, pattern="^sched_"))
    # Ota-ona bildirishnoma sozlamalari callbacklari
    application.add_handler(CallbackQueryHandler(parent_settings_callback_handler, pattern="^toggle_notify_"))
    # Reyting callbacklari
    application.add_handler(CallbackQueryHandler(leaderboard_callback_handler, pattern="^lb_class_"))
    # Sinflar callbacklari
    application.add_handler(CallbackQueryHandler(view_class_callback_handler, pattern="^(view_class_|view_all_classes)"))

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
            BotCommand("reyting", "🏆 Baholar bo'yicha reyting"),
            BotCommand("sinflar", "🏫 1-11 sinflar ro'yxati"),
            BotCommand("sinf_ozgartir", "🔄 O'quvchi sinfini o'zgartirish"),
            BotCommand("otaona", "👪 Ota-ona kabineti"),
            BotCommand("pin", "🔑 Ota-onani ulash PIN-kodi"),
            BotCommand("davomat", "📋 Davomat kiritish (kelmadi/kechikdi)"),
            BotCommand("ogohlantirish", "⚠️ Ota-onalarga ogohlantirish"),
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

    # ── APScheduler ──
    tz = pytz.timezone('Asia/Tashkent')
    scheduler = AsyncIOScheduler(timezone=tz)
    # 14:00 O'quvchilarga kundalik baholar
    scheduler.add_job(
        send_daily_grades,
        CronTrigger(hour=14, minute=0, timezone=tz),
        args=[application.bot],
        id='daily_grades',
        replace_existing=True
    )
    # 18:00 Ota-onalarga kundalik xulosa (baholar, davomat, vazifalar)
    scheduler.add_job(
        send_daily_digests_job,
        CronTrigger(hour=18, minute=0, timezone=tz),
        args=[application.bot, db],
        id='daily_parent_digest',
        replace_existing=True
    )
    scheduler.start()
    logger.info("✅ Kundalik bildirishnomalar (14:00 o'quvchilar, 18:00 ota-onalar - Toshkent) sozlandi.")

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
