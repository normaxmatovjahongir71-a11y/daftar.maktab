"""
Raqamli Maktab Daftari — Ota-onalar Nazorati (Bildirishnomalar va Xabarlar)
Telegram HTML formatidagi shablonlar va xabarnomalar xizmati.
"""

import logging
from datetime import datetime, date
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#              1. TELEGRAM HTML XABAR SHABLONLARI
# ══════════════════════════════════════════════════════════════

def format_grade_alert(student_name: str, subject: str, grade: int, teacher_name: str = "", comment: str = None) -> str:
    """Yangi baho qo'yilganda yuboriladigan HTML shablon."""
    grade_emojis = {
        5: "5️⃣ 🟢 (A'lo)",
        4: "4️⃣ 🔵 (Yaxshi)",
        3: "3️⃣ 🟡 (Qoniqarli)",
        2: "2️⃣ 🔴 (Qoniqarsiz)"
    }
    grade_badge = grade_emojis.get(grade, f"⭐ {grade}")
    t_name = teacher_name or "O'qituvchi"

    html = (
        "📊 <b>YANGI BAHO QO'YILDI!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Farzandingiz:</b> {student_name}\n"
        f"📘 <b>Fan nomi:</b> <b>{subject}</b>\n"
        f"⭐️ <b>Olingan baho:</b> <b>{grade_badge}</b>\n"
        f"👨‍🏫 <b>O'qituvchi:</b> {t_name}\n"
    )
    if comment and comment.strip():
        html += f"💬 <b>O'qituvchi izohi:</b> <i>«{comment.strip()}»</i>\n"
    html += (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📱 <i>«Raqamli Maktab Daftari» tizimi</i>"
    )
    return html


def format_attendance_alert(student_name: str, subject: str, slot: int, status: str, minutes_late: int = 0, note: str = None) -> str:
    """Davomat ogohlantirishi (kelmadi yoki kechikdi) HTML shabloni."""
    status_upper = status.upper().strip()
    if status_upper == 'KELMADI':
        header = "🚨 <b>DIQQAT: DARSDA YO'Q!</b>"
        status_line = "❌ <b>Holat:</b> <b>Darsga umuman kelmadi</b>"
    elif status_upper == 'KECHIKDI':
        header = "⏰ <b>OGOHLANTIRISH: DARSGA KECHIKISH</b>"
        late_text = f"{minutes_late} daqiqa" if minutes_late > 0 else "bir necha daqiqa"
        status_line = f"⏳ <b>Holat:</b> <b>Darsga {late_text} kechikib kirdi</b>"
    elif status_upper == 'SABABLI':
        header = "📝 <b>DAVOMAT: SABABLI DARS QOLDIRISH</b>"
        status_line = "📋 <b>Holat:</b> Sababli qatnashmadi"
    else:
        header = "📋 <b>DAVOMAT MA'LUMOTI</b>"
        status_line = f"ℹ️ <b>Holat:</b> {status}"

    html = (
        f"{header}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Farzandingiz:</b> {student_name}\n"
        f"🔔 <b>Dars:</b> {slot}-dars ({subject or 'Dars'})\n"
        f"{status_line}\n"
        f"📅 <b>Sana:</b> Bugun ({date.today().strftime('%d.%m.%Y')})\n"
    )
    if note and note.strip():
        html += f"💬 <b>Izoh:</b> <i>«{note.strip()}»</i>\n"
    html += (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Iltimos, farzandingiz dars qoldirish sababini sinf rahbariga ma'lum qiling.</i>"
    )
    return html


def format_daily_digest(student_name: str, date_str: str, grades_list: list, tasks_list: list, attendance_records: list) -> str:
    """Kun yakunidagi oylik/haftalik/kunlik hisobot shabloni."""
    html = (
        f"📑 <b>KUNLIK YAKUNIY HISOBOT — {date_str}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>O'quvchi:</b> <b>{student_name}</b>\n\n"
    )

    # 1. Davomat qismi
    html += "📋 <b>Bugungi davomat:</b>\n"
    if attendance_records:
        for a in attendance_records:
            icon = "✅" if a['status'] == 'KELDI' else ("❌" if a['status'] == 'KELMADI' else "⏰")
            late_str = f" ({a['minutes_late']} daq.)" if a.get('minutes_late') and a['status'] == 'KECHIKDI' else ""
            html += f" • {a['slot_number']}-dars ({a['subject_name']}): {icon} <b>{a['status']}</b>{late_str}\n"
    else:
        html += " • <i>Barcha darslarda ishtirok etdi (to'liq).</i>\n"

    # 2. Baholar qismi
    html += "\n⭐ <b>Bugun olingan baholar:</b>\n"
    if grades_list:
        for g in grades_list:
            comment_str = f" <i>(«{g['comment']}»)</i>" if g.get('comment') else ""
            html += f" • {g['subject']}: <b>{g['grade']}</b>{comment_str}\n"
    else:
        html += " • <i>Bugun yangi baholar qo'yilmadi.</i>\n"

    # 3. Uy vazifalari qismi
    html += "\n📝 <b>Ertangi/Yaqin kunlarga vazifalar:</b>\n"
    if tasks_list:
        for idx, t in enumerate(tasks_list[:5], 1):
            html += f" <b>{idx}.</b> {t['text']} <i>(Muddat: {t['deadline']})</i>\n"
    else:
        html += " • <i>Faol uy vazifalari yo'q.</i>\n"

    html += (
        "\n━━━━━━━━━━━━━━━━━━━━━━\n"
        "🌟 <i>Farzandingiz ta'limiga befarq bo'lmaganingiz uchun tashakkur!</i>"
    )
    return html


def format_teacher_notice(student_name: str, teacher_name: str, subject: str, message: str, is_urgent: bool = False) -> str:
    """O'qituvchidan ota-onaga shaxsiy xabar shabloni."""
    icon = "🚨 <b>SHOSHILINCH OGOHLANTIRISH!</b>" if is_urgent else "📨 <b>O'QITUVCHIDAN XABAR</b>"
    t_name = teacher_name or "O'qituvchi"
    subj_str = f" ({subject})" if subject else ""

    html = (
        f"{icon}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Farzandingiz:</b> {student_name}\n"
        f"👨‍🏫 <b>Yuboruvchi:</b> {t_name}{subj_str}\n\n"
        f"💬 <b>Xabar matni:</b>\n"
        f"<i>«{message}»</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📞 <i>Zarur bo'lsa o'qituvchi bilan bog'laning.</i>"
    )
    return html


# ══════════════════════════════════════════════════════════════
#           2. OTA-ONA SOZLAMALARI INLINE KLAVIATURASI
# ══════════════════════════════════════════════════════════════

def get_parent_settings_markup(settings: dict) -> InlineKeyboardMarkup:
    """Ota-ona bildirishnomalarini yoqish/o'chirish tugmalari."""
    def _status(val):
        return "✅ Yoqilgan" if val else "❌ O'chirilgan"

    btn_grades = InlineKeyboardButton(
        f"🔔 Baholar: {_status(settings.get('notify_grades', 1))}",
        callback_data="toggle_notify_grades"
    )
    btn_attendance = InlineKeyboardButton(
        f"🚨 Davomat: {_status(settings.get('notify_attendance', 1))}",
        callback_data="toggle_notify_attendance"
    )
    btn_digest = InlineKeyboardButton(
        f"📑 Kunlik hisobot: {_status(settings.get('notify_daily_digest', 1))}",
        callback_data="toggle_notify_daily_digest"
    )
    btn_homework = InlineKeyboardButton(
        f"📝 Vazifalar: {_status(settings.get('notify_homework', 1))}",
        callback_data="toggle_notify_homework"
    )

    return InlineKeyboardMarkup([
        [btn_grades],
        [btn_attendance],
        [btn_digest],
        [btn_homework]
    ])


# ══════════════════════════════════════════════════════════════
#               3. NOTIFICATION DISPATCHER SERVICE
# ══════════════════════════════════════════════════════════════

def is_quiet_hours(start_str: str, end_str: str) -> bool:
    """Tungi bezovta qilmaslik soatini tekshirish."""
    try:
        if not start_str or not end_str:
            return False
        now_time = datetime.now().time()
        start_t = datetime.strptime(start_str.strip(), "%H:%M").time()
        end_t = datetime.strptime(end_str.strip(), "%H:%M").time()
        if start_t < end_t:
            return start_t <= now_time <= end_t
        return now_time >= start_t or now_time <= end_t
    except Exception:
        return False


async def notify_parents_grade(bot, db, student_id: int, subject: str, grade: int, teacher_name: str = "", comment: str = None):
    """Baho qo'yilganda ota-onalarga xabar yuborish."""
    parents = db.get_parents_for_student(student_id)
    if not parents:
        return

    for p in parents:
        if not p.get('notify_grades', 1):
            continue

        disable_sound = is_quiet_hours(p.get('quiet_hours_start'), p.get('quiet_hours_end'))
        text = format_grade_alert(p['student_name'], subject, grade, teacher_name, comment)

        try:
            await bot.send_message(
                chat_id=p['telegram_chat_id'],
                text=text,
                parse_mode="HTML",
                disable_notification=disable_sound
            )
            logger.info(f"Baho xabarnomasi yuborildi: chat {p['telegram_chat_id']}")
        except Exception as e:
            logger.warning(f"Ota-onaga baho xabarini yuborishda xato ({p['telegram_chat_id']}): {e}")


async def notify_parents_attendance(bot, db, student_id: int, slot: int, subject: str, status: str, minutes_late: int = 0, note: str = None):
    """Davomat (kelmadi/kechikdi) ota-onaga ogohlantirish yuborish."""
    if status.upper() == 'KELDI':
        return

    parents = db.get_parents_for_student(student_id)
    if not parents:
        return

    for p in parents:
        if not p.get('notify_attendance', 1):
            continue

        text = format_attendance_alert(p['student_name'], subject, slot, status, minutes_late, note)
        try:
            await bot.send_message(
                chat_id=p['telegram_chat_id'],
                text=text,
                parse_mode="HTML",
                disable_notification=False  # Davomat doimo ovozli signal bilan boradi!
            )
            logger.info(f"Davomat xabarnomasi yuborildi: chat {p['telegram_chat_id']}")
        except Exception as e:
            logger.warning(f"Ota-onaga davomat xabarini yuborishda xato ({p['telegram_chat_id']}): {e}")


async def notify_parents_notice(bot, db, student_id: int, teacher_name: str, subject: str, message: str, is_urgent: bool = False):
    """O'qituvchidan ota-onaga shaxsiy xabar yuborish."""
    parents = db.get_parents_for_student(student_id)
    if not parents:
        return False

    sent_any = False
    for p in parents:
        text = format_teacher_notice(p['student_name'], teacher_name, subject, message, is_urgent)
        try:
            await bot.send_message(
                chat_id=p['telegram_chat_id'],
                text=text,
                parse_mode="HTML",
                disable_notification=not is_urgent
            )
            sent_any = True
        except Exception as e:
            logger.warning(f"Ota-onaga shaxsiy xabar yuborishda xato ({p['telegram_chat_id']}): {e}")
    return sent_any


async def send_daily_digests_job(bot, db):
    """Har kuni 18:00 da ota-onalar va o'quvchilarga kunlik xulosani yuborish."""
    logger.info("Kunlik ota-onalar dayjesti (18:00) ishga tushdi.")
    try:
        parents = db.get_all_active_parents()
        today_str = date.today().isoformat()
        today_formatted = date.today().strftime('%d.%m.%Y')

        for p in parents:
            if not p.get('notify_daily_digest', 1):
                continue

            sid = p['student_id']
            grades = db.get_today_grades(sid)
            tasks = db.get_tasks()
            attendance = db.get_student_attendance(sid, lesson_date=today_str)

            text = format_daily_digest(p['student_name'], today_formatted, grades, tasks, attendance)
            try:
                await bot.send_message(
                    chat_id=p['telegram_chat_id'],
                    text=text,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Ota-ona kunlik hisobotida xato ({p['telegram_chat_id']}): {e}")
    except Exception as err:
        logger.error(f"send_daily_digests_job xatoligi: {err}")
