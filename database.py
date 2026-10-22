"""
Raqamli Daftar — Ma'lumotlar bazasi moduli
SQLite bilan barcha CRUD operatsiyalar.
"""

import sqlite3
import os
from datetime import date
from auth import hash_password, verify_password

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'daftar.db')


class Database:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout = 30000")
            conn.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
        return conn

    def init_db(self):
        """Jadvallarni yaratish va boshlang'ich ma'lumotlarni qo'shish."""
        conn = self._conn()
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS teachers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                login TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                telegram_chat_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                login TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                telegram_chat_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day INTEGER NOT NULL,
                slot INTEGER NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                UNIQUE(day, slot)
            );

            CREATE TABLE IF NOT EXISTS exam (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                deadline TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS grades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                grade INTEGER NOT NULL CHECK(grade BETWEEN 2 AND 5),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS task_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                student_id INTEGER NOT NULL,
                image_path TEXT NOT NULL,
                original_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_chat_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS parents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_chat_id INTEGER UNIQUE NOT NULL,
                first_name TEXT NOT NULL,
                last_name TEXT DEFAULT '',
                phone_number TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS student_parents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                parent_id INTEGER,
                relationship_type TEXT DEFAULT 'OTA_ONA',
                invite_pin TEXT UNIQUE NOT NULL,
                pin_expires_at TIMESTAMP NOT NULL,
                is_verified BOOLEAN DEFAULT 0,
                linked_at TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
                FOREIGN KEY (parent_id) REFERENCES parents(id) ON DELETE CASCADE,
                UNIQUE(student_id, parent_id)
            );

            CREATE TABLE IF NOT EXISTS parent_notification_settings (
                parent_id INTEGER PRIMARY KEY,
                notify_grades BOOLEAN DEFAULT 1,
                notify_attendance BOOLEAN DEFAULT 1,
                notify_homework BOOLEAN DEFAULT 1,
                notify_daily_digest BOOLEAN DEFAULT 1,
                quiet_hours_start TEXT DEFAULT '22:00',
                quiet_hours_end TEXT DEFAULT '07:00',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (parent_id) REFERENCES parents(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                lesson_date TEXT NOT NULL DEFAULT (DATE('now')),
                slot_number INTEGER NOT NULL,
                subject_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'KELDI',
                minutes_late INTEGER DEFAULT 0,
                note TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
                UNIQUE(student_id, lesson_date, slot_number)
            );

            CREATE TABLE IF NOT EXISTS parent_notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                teacher_id INTEGER,
                message TEXT NOT NULL,
                is_urgent BOOLEAN DEFAULT 0,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
            );
        ''')

        # Grades jadvaliga qo'shimcha ustunlarni tekshirish va qo'shish
        try:
            cols = [c[1] for c in conn.execute('PRAGMA table_info(grades)').fetchall()]
            if 'comment' not in cols:
                conn.execute("ALTER TABLE grades ADD COLUMN comment TEXT DEFAULT ''")
            if 'teacher_name' not in cols:
                conn.execute("ALTER TABLE grades ADD COLUMN teacher_name TEXT DEFAULT ''")
        except Exception:
            pass

        # Students jadvaliga class_name (sinf) ustunini tekshirish va qo'shish (1-11 sinflar)
        try:
            s_cols = [c[1] for c in conn.execute('PRAGMA table_info(students)').fetchall()]
            if 'class_name' not in s_cols:
                conn.execute("ALTER TABLE students ADD COLUMN class_name TEXT DEFAULT '5-sinf'")
        except Exception:
            pass
        # Jadval kataklarini yaratish (Dush-Shanba, 7 dars)
        for day in range(1, 7):
            for slot in range(1, 8):
                conn.execute(
                    'INSERT OR IGNORE INTO schedule (day, slot, subject) VALUES (?, ?, ?)',
                    (day, slot, '')
                )
        # Imtihon yozuvini yaratish
        if conn.execute('SELECT COUNT(*) FROM exam').fetchone()[0] == 0:
            conn.execute("INSERT INTO exam (name, date) VALUES ('', '')")

        # Bosh administratorni (Jahongircmbk) doimiy saqlash
        conn.execute(
            'INSERT OR IGNORE INTO admins (telegram_chat_id, username) VALUES (?, ?)',
            (7050627545, 'Jahongircmbk')
        )
        conn.execute(
            "UPDATE teachers SET telegram_chat_id = ? WHERE login = 'admin' AND (telegram_chat_id IS NULL OR telegram_chat_id = '')",
            (7050627545,)
        )

        # Sessiyalar zaxirasini tiklash (agar mavjud bo'lsa)
        try:
            backup_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sessions_backup.json')
            if os.path.exists(backup_file):
                import json
                with open(backup_file, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                for cid_str, info in saved.items():
                    cid = int(cid_str)
                    if info.get('role') == 'student':
                        conn.execute("UPDATE students SET telegram_chat_id = ? WHERE login = ?", (cid, info.get('login')))
                    elif info.get('role') == 'teacher':
                        conn.execute("UPDATE teachers SET telegram_chat_id = ? WHERE login = ?", (cid, info.get('login')))
                    elif info.get('role') == 'parent':
                        conn.execute("INSERT OR IGNORE INTO parents (telegram_chat_id, first_name) VALUES (?, ?)", (cid, 'Ota-ona'))
        except Exception:
            pass

        conn.commit()
        conn.close()

    # ==================== O'qituvchilar ====================

    def has_teacher(self):
        conn = self._conn()
        count = conn.execute('SELECT COUNT(*) FROM teachers').fetchone()[0]
        conn.close()
        return count > 0

    def create_teacher(self, login, password):
        pw_hash, salt = hash_password(password)
        conn = self._conn()
        try:
            conn.execute(
                'INSERT INTO teachers (login, password_hash, salt) VALUES (?, ?, ?)',
                (login, pw_hash, salt)
            )
            conn.commit()
            tid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            conn.close()
            return tid
        except sqlite3.IntegrityError:
            conn.close()
            return None

    def create_teacher_if_not_exists(self, login, password):
        if not self.has_teacher():
            return self.create_teacher(login, password)
        return None

    def verify_teacher(self, login, password):
        conn = self._conn()
        row = conn.execute(
            'SELECT * FROM teachers WHERE login = ?', (login,)
        ).fetchone()
        conn.close()
        if row and verify_password(password, row['password_hash'], row['salt']):
            return dict(row)
        return None

    def set_teacher_chat_id(self, login, chat_id):
        conn = self._conn()
        conn.execute(
            'UPDATE teachers SET telegram_chat_id = ? WHERE login = ?',
            (chat_id, login)
        )
        conn.commit()
        conn.close()

    def get_teacher_by_chat_id(self, chat_id):
        conn = self._conn()
        row = conn.execute(
            'SELECT * FROM teachers WHERE telegram_chat_id = ?', (chat_id,)
        ).fetchone()
        if not row and self.is_admin(chat_id):
            row = conn.execute("SELECT * FROM teachers WHERE login = 'admin' LIMIT 1").fetchone()
            if row:
                conn.execute("UPDATE teachers SET telegram_chat_id = ? WHERE id = ?", (chat_id, row['id']))
                conn.commit()
        conn.close()
        return dict(row) if row else None

    def get_teachers(self):
        conn = self._conn()
        rows = conn.execute(
            'SELECT id, login, telegram_chat_id, created_at FROM teachers ORDER BY id'
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def delete_teacher(self, teacher_id):
        conn = self._conn()
        conn.execute('DELETE FROM teachers WHERE id = ?', (teacher_id,))
        conn.commit()
        conn.close()

    # ==================== Adminlar ====================

    def add_admin(self, telegram_chat_id, username=None):
        conn = self._conn()
        try:
            conn.execute(
                'INSERT OR IGNORE INTO admins (telegram_chat_id, username) VALUES (?, ?)',
                (telegram_chat_id, username)
            )
            conn.commit()
            conn.close()
            return True
        except Exception:
            conn.close()
            return False

    SUPER_ADMINS = [7050627545]  # Doimiy bosh admin (Jahongircmbk)

    def is_admin(self, telegram_chat_id):
        if telegram_chat_id and int(telegram_chat_id) in self.SUPER_ADMINS:
            return True
        conn = self._conn()
        row = conn.execute(
            'SELECT id FROM admins WHERE telegram_chat_id = ?', (telegram_chat_id,)
        ).fetchone()
        conn.close()
        return row is not None

    def get_admins(self):
        conn = self._conn()
        rows = conn.execute('SELECT * FROM admins ORDER BY id').fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def delete_admin(self, telegram_chat_id):
        conn = self._conn()
        conn.execute('DELETE FROM admins WHERE telegram_chat_id = ?', (telegram_chat_id,))
        conn.commit()
        conn.close()

    def get_stats(self):
        conn = self._conn()
        students_count = conn.execute('SELECT COUNT(*) FROM students').fetchone()[0]
        teachers_count = conn.execute('SELECT COUNT(*) FROM teachers').fetchone()[0]
        grades_count = conn.execute('SELECT COUNT(*) FROM grades').fetchone()[0]
        tasks_count = conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
        images_count = conn.execute('SELECT COUNT(*) FROM task_images').fetchone()[0]
        admins_count = conn.execute('SELECT COUNT(*) FROM admins').fetchone()[0]
        conn.close()
        return {
            'students': students_count,
            'teachers': teachers_count,
            'grades': grades_count,
            'tasks': tasks_count,
            'images': images_count,
            'admins': admins_count
        }

    # ==================== O'quvchilar va Sinflar (1-11) ====================

    @staticmethod
    def normalize_class_name(raw_class):
        """Sinf nomini 1-11 gacha standart formatga keltiradi (masalan: 7 -> 7-sinf, 7a -> 7-A)."""
        if not raw_class:
            return "5-sinf"
        s = str(raw_class).strip()
        if s.isdigit():
            n = int(s)
            if 1 <= n <= 11:
                return f"{n}-sinf"
            return "5-sinf"
        import re
        m = re.match(r'^(\d{1,2})\s*[-_]?\s*([a-zA-Zа-яА-Я]?|sinf)$', s, re.IGNORECASE)
        if m:
            num = int(m.group(1))
            suffix = m.group(2)
            if 1 <= num <= 11:
                if not suffix or suffix.lower() == 'sinf':
                    return f"{num}-sinf"
                return f"{num}-{suffix.upper()}"
        return s[:15] if s else "5-sinf"

    def create_student(self, name, login, password, class_name='5-sinf'):
        pw_hash, salt = hash_password(password)
        normalized_class = self.normalize_class_name(class_name)
        conn = self._conn()
        try:
            conn.execute(
                'INSERT INTO students (name, login, password_hash, salt, class_name) VALUES (?, ?, ?, ?, ?)',
                (name, login, pw_hash, salt, normalized_class)
            )
            conn.commit()
            sid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            conn.close()
            return sid
        except sqlite3.IntegrityError:
            conn.close()
            return None

    def verify_student(self, login, password):
        conn = self._conn()
        row = conn.execute(
            'SELECT * FROM students WHERE login = ?', (login,)
        ).fetchone()
        conn.close()
        if row and verify_password(password, row['password_hash'], row['salt']):
            return dict(row)
        return None

    def get_students(self, class_name=None):
        conn = self._conn()
        if class_name:
            norm = self.normalize_class_name(class_name)
            rows = conn.execute(
                '''SELECT id, name, login, COALESCE(class_name, '5-sinf') as class_name, telegram_chat_id 
                   FROM students 
                   WHERE class_name = ? OR class_name LIKE ?
                   ORDER BY name''',
                (norm, f"{norm}%")
            ).fetchall()
        else:
            rows = conn.execute(
                '''SELECT id, name, login, COALESCE(class_name, '5-sinf') as class_name, telegram_chat_id 
                   FROM students 
                   ORDER BY class_name, name'''
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_student_by_id(self, student_id):
        conn = self._conn()
        row = conn.execute(
            '''SELECT id, name, login, COALESCE(class_name, '5-sinf') as class_name, telegram_chat_id 
               FROM students WHERE id = ?''',
            (student_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_student_by_login(self, login):
        conn = self._conn()
        row = conn.execute(
            '''SELECT id, name, login, COALESCE(class_name, '5-sinf') as class_name, telegram_chat_id 
               FROM students WHERE login = ?''',
            (login,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def update_student_class(self, student_id_or_login, new_class_name):
        """O'quvchining sinfini yangilash (1-11 sinflar)."""
        norm_class = self.normalize_class_name(new_class_name)
        conn = self._conn()
        if isinstance(student_id_or_login, int) or str(student_id_or_login).isdigit():
            conn.execute('UPDATE students SET class_name = ? WHERE id = ?', (norm_class, int(student_id_or_login)))
        else:
            conn.execute('UPDATE students SET class_name = ? WHERE login = ?', (norm_class, str(student_id_or_login)))
        conn.commit()
        conn.close()
        return norm_class

    def get_classes_summary(self):
        """1-11 sinflarning umumiy ro'yxati va o'quvchilar soni."""
        conn = self._conn()
        # Barcha o'quvchilar sinflari bo'yicha hisoblash
        rows = conn.execute(
            '''SELECT COALESCE(class_name, '5-sinf') as class_name, COUNT(*) as count 
               FROM students 
               GROUP BY class_name 
               ORDER BY class_name'''
        ).fetchall()
        class_counts = {r['class_name']: r['count'] for r in rows}
        conn.close()

        # 1-11 standart sinflarni kafolatlash
        standard_classes = [f"{i}-sinf" for i in range(1, 12)]
        result = []
        for c in standard_classes:
            result.append({
                'class_name': c,
                'count': class_counts.get(c, 0)
            })
        # Standartdan tashqari boshqa sinflar (masalan 7-A, 9-B) bo'lsa qo'shish
        for c, cnt in class_counts.items():
            if c not in standard_classes:
                result.append({'class_name': c, 'count': cnt})
        return result

    def delete_student(self, student_id):
        conn = self._conn()
        conn.execute('DELETE FROM students WHERE id = ?', (student_id,))
        conn.commit()
        conn.close()

    def update_student_password(self, student_id, new_password):
        pw_hash, salt = hash_password(new_password)
        conn = self._conn()
        conn.execute(
            'UPDATE students SET password_hash = ?, salt = ? WHERE id = ?',
            (pw_hash, salt, student_id)
        )
        conn.commit()
        conn.close()

    def set_student_chat_id(self, login, chat_id):
        conn = self._conn()
        conn.execute(
            'UPDATE students SET telegram_chat_id = ? WHERE login = ?',
            (chat_id, login)
        )
        conn.commit()
        conn.close()

    def get_student_by_chat_id(self, chat_id):
        conn = self._conn()
        row = conn.execute(
            '''SELECT id, name, login, COALESCE(class_name, '5-sinf') as class_name 
               FROM students WHERE telegram_chat_id = ?''',
            (chat_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    # ==================== Baholar bo'yicha Reyting (Leaderboard) ====================

    def get_leaderboard(self, class_name=None, limit=50):
        """
        O'quvchilar reytingi (o'rtacha ball, baholar soni va a'lo baholar bo'yicha).
        class_name ko'rsatilsa faqat shu sinf reytingi olinadi.
        """
        conn = self._conn()
        where_clause = ""
        params = []
        if class_name:
            norm = self.normalize_class_name(class_name)
            where_clause = "WHERE s.class_name = ? OR s.class_name LIKE ?"
            params = [norm, f"{norm}%"]

        sql = f'''
            SELECT 
                s.id as student_id,
                s.name as student_name,
                s.login as student_login,
                COALESCE(s.class_name, '5-sinf') as class_name,
                s.telegram_chat_id,
                COUNT(g.id) as grades_count,
                COALESCE(ROUND(AVG(g.grade), 2), 0.0) as avg_grade,
                COALESCE(SUM(g.grade), 0) as total_score,
                SUM(CASE WHEN g.grade = 5 THEN 1 ELSE 0 END) as fives_count,
                SUM(CASE WHEN g.grade = 4 THEN 1 ELSE 0 END) as fours_count,
                SUM(CASE WHEN g.grade = 3 THEN 1 ELSE 0 END) as threes_count,
                SUM(CASE WHEN g.grade = 2 THEN 1 ELSE 0 END) as twos_count
            FROM students s
            LEFT JOIN grades g ON s.id = g.student_id
            {where_clause}
            GROUP BY s.id
            ORDER BY 
                avg_grade DESC, 
                grades_count DESC, 
                fives_count DESC, 
                total_score DESC, 
                s.name ASC
            LIMIT {int(limit)}
        '''
        rows = conn.execute(sql, params).fetchall()
        conn.close()

        result = []
        for idx, r in enumerate(rows, 1):
            row_dict = dict(r)
            row_dict['rank'] = idx
            if idx == 1:
                row_dict['medal'] = '🥇'
            elif idx == 2:
                row_dict['medal'] = '🥈'
            elif idx == 3:
                row_dict['medal'] = '🥉'
            else:
                row_dict['medal'] = f"{idx}."
            result.append(row_dict)
        return result

    def get_student_rank(self, student_id):
        """Bitta o'quvchining maktab va o'z sinfidagi aniq o'rni."""
        student = self.get_student_by_id(student_id)
        if not student:
            return None

        # Maktab bo'yicha reyting
        school_board = self.get_leaderboard(limit=500)
        school_rank = None
        school_total = len(school_board)
        student_data = None
        for item in school_board:
            if item['student_id'] == student_id:
                school_rank = item['rank']
                student_data = item
                break

        # Sinf bo'yicha reyting
        class_board = self.get_leaderboard(class_name=student.get('class_name'), limit=200)
        class_rank = None
        class_total = len(class_board)
        for item in class_board:
            if item['student_id'] == student_id:
                class_rank = item['rank']
                break

        return {
            'student_id': student_id,
            'student_name': student['name'],
            'class_name': student.get('class_name', '5-sinf'),
            'school_rank': school_rank or school_total,
            'school_total': school_total,
            'class_rank': class_rank or class_total,
            'class_total': class_total,
            'avg_grade': student_data['avg_grade'] if student_data else 0.0,
            'grades_count': student_data['grades_count'] if student_data else 0,
            'medal': student_data['medal'] if student_data else ''
        }

    # ==================== Jadval ====================

    def get_schedule(self):
        conn = self._conn()
        rows = conn.execute(
            'SELECT day, slot, subject FROM schedule ORDER BY day, slot'
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def set_schedule(self, day, slot, subject):
        conn = self._conn()
        conn.execute(
            'INSERT OR REPLACE INTO schedule (day, slot, subject) VALUES (?, ?, ?)',
            (day, slot, subject)
        )
        conn.commit()
        conn.close()

    # ==================== Imtihon ====================

    def get_exam(self):
        conn = self._conn()
        row = conn.execute(
            'SELECT name, date FROM exam ORDER BY id LIMIT 1'
        ).fetchone()
        conn.close()
        return dict(row) if row else {'name': '', 'date': ''}

    def set_exam(self, name, exam_date):
        conn = self._conn()
        count = conn.execute('SELECT COUNT(*) FROM exam').fetchone()[0]
        if count == 0:
            conn.execute(
                'INSERT INTO exam (name, date) VALUES (?, ?)',
                (name, exam_date)
            )
        else:
            conn.execute(
                'UPDATE exam SET name = ?, date = ? WHERE id = (SELECT MIN(id) FROM exam)',
                (name, exam_date)
            )
        conn.commit()
        conn.close()

    # ==================== Vazifalar ====================

    def get_tasks(self):
        conn = self._conn()
        rows = conn.execute(
            'SELECT * FROM tasks ORDER BY deadline ASC'
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_task(self, text, deadline):
        conn = self._conn()
        conn.execute(
            'INSERT INTO tasks (text, deadline) VALUES (?, ?)',
            (text, deadline)
        )
        conn.commit()
        tid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        conn.close()
        return tid

    def delete_task(self, task_id):
        conn = self._conn()
        conn.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
        conn.commit()
        conn.close()

    # ==================== Baholar ====================

    def get_grades(self, student_id):
        conn = self._conn()
        rows = conn.execute(
            '''SELECT g.id, g.student_id, g.subject, g.grade, g.comment, g.teacher_name, g.created_at,
                      s.name as student_name
               FROM grades g
               JOIN students s ON g.student_id = s.id
               WHERE g.student_id = ?
               ORDER BY g.created_at DESC''',
            (student_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_grades(self):
        conn = self._conn()
        rows = conn.execute(
            '''SELECT g.id, g.student_id, g.subject, g.grade, g.comment, g.teacher_name, g.created_at,
                      s.name as student_name, s.login as student_login
               FROM grades g
               JOIN students s ON g.student_id = s.id
               ORDER BY g.created_at DESC'''
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_grade(self, student_id, subject, grade, comment='', teacher_name=''):
        conn = self._conn()
        conn.execute(
            'INSERT INTO grades (student_id, subject, grade, comment, teacher_name) VALUES (?, ?, ?, ?, ?)',
            (student_id, subject, grade, comment or '', teacher_name or '')
        )
        conn.commit()
        gid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        conn.close()
        return gid

    def delete_grade(self, grade_id):
        conn = self._conn()
        conn.execute('DELETE FROM grades WHERE id = ?', (grade_id,))
        conn.commit()
        conn.close()

    def get_today_grades(self, student_id):
        today = date.today().isoformat()
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM grades WHERE student_id = ? AND DATE(created_at) = ?",
            (student_id, today)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_student_average(self, student_id):
        conn = self._conn()
        row = conn.execute(
            'SELECT AVG(grade) as avg_grade FROM grades WHERE student_id = ?',
            (student_id,)
        ).fetchone()
        conn.close()
        avg = row['avg_grade'] if row else None
        return round(avg, 2) if avg else None

    # ==================== Rasmlar ====================

    def save_image(self, task_id, student_id, image_path, original_name=None):
        conn = self._conn()
        conn.execute(
            'INSERT INTO task_images (task_id, student_id, image_path, original_name) VALUES (?, ?, ?, ?)',
            (task_id, student_id, image_path, original_name)
        )
        conn.commit()
        iid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        conn.close()
        return iid

    def get_images(self, task_id=None, student_id=None):
        conn = self._conn()
        query = '''SELECT ti.id, ti.task_id, ti.student_id, ti.image_path,
                          ti.original_name, ti.created_at, s.name as student_name
                   FROM task_images ti
                   JOIN students s ON ti.student_id = s.id
                   WHERE 1=1'''
        params = []
        if task_id is not None:
            query += ' AND ti.task_id = ?'
            params.append(task_id)
        if student_id is not None:
            query += ' AND ti.student_id = ?'
            params.append(student_id)
        query += ' ORDER BY ti.created_at DESC'
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_images(self):
        conn = self._conn()
        rows = conn.execute(
            '''SELECT ti.id, ti.task_id, ti.student_id, ti.image_path,
                      ti.original_name, ti.created_at, s.name as student_name
               FROM task_images ti
               JOIN students s ON ti.student_id = s.id
               ORDER BY ti.created_at DESC'''
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_students_with_chat_id(self):
        conn = self._conn()
        rows = conn.execute(
            'SELECT id, name, login, telegram_chat_id FROM students WHERE telegram_chat_id IS NOT NULL'
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ==================== Ota-onalar Nazorati (Parental Control) ====================

    def generate_student_pin(self, student_id):
        """O'quvchi uchun ota-onani ulash PIN-kodini yaratish (7 kunlik)."""
        import random
        from datetime import datetime, timedelta
        conn = self._conn()
        # Mavjud faol kodni tekshirish
        now_str = datetime.now().isoformat()
        row = conn.execute(
            '''SELECT invite_pin FROM student_parents 
               WHERE student_id = ? AND is_verified = 0 AND pin_expires_at > ?
               LIMIT 1''',
            (student_id, now_str)
        ).fetchone()
        if row:
            conn.close()
            return row['invite_pin']

        # Yangi noyob PIN generatsiya qilish
        pin = f"MD-{random.randint(1000, 9999)}"
        expires_at = (datetime.now() + timedelta(days=7)).isoformat()
        
        # Eski ishlatilmagan kodlarni tozalash va yangi kod kiritish
        conn.execute('DELETE FROM student_parents WHERE student_id = ? AND is_verified = 0', (student_id,))
        conn.execute(
            '''INSERT INTO student_parents (student_id, invite_pin, pin_expires_at, is_verified)
               VALUES (?, ?, ?, 0)''',
            (student_id, pin, expires_at)
        )
        conn.commit()
        conn.close()
        return pin

    def get_student_pin(self, student_id):
        return self.generate_student_pin(student_id)

    def verify_and_link_parent(self, invite_pin, telegram_chat_id, first_name, last_name='', phone_number=''):
        """Ota-onani PIN-kod orqali farzandiga bog'lash."""
        from datetime import datetime
        pin = invite_pin.strip().upper()
        now_str = datetime.now().isoformat()
        conn = self._conn()

        rel = conn.execute(
            '''SELECT sp.id, sp.student_id, sp.pin_expires_at, sp.is_verified, s.name as student_name
               FROM student_parents sp
               JOIN students s ON sp.student_id = s.id
               WHERE sp.invite_pin = ?''',
            (pin,)
        ).fetchone()

        if not rel:
            conn.close()
            return {'success': False, 'error': "PIN-kod noto'g'ri. Qaytadan tekshirib yozing."}

        if rel['is_verified']:
            conn.close()
            return {'success': False, 'error': "Ushbu taklif kodi allaqachon ishlatilgan!"}

        if rel['pin_expires_at'] < now_str:
            conn.close()
            return {'success': False, 'error': "Ushbu PIN-kodning amal qilish muddati tugagan. O'qituvchidan yangi kod oling."}

        # Ota-ona hisobini yaratish yoki olish
        parent = conn.execute(
            'SELECT id FROM parents WHERE telegram_chat_id = ?', (telegram_chat_id,)
        ).fetchone()
        if not parent:
            conn.execute(
                '''INSERT INTO parents (telegram_chat_id, first_name, last_name, phone_number)
                   VALUES (?, ?, ?, ?)''',
                (telegram_chat_id, first_name, last_name or '', phone_number or '')
            )
            parent_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        else:
            parent_id = parent['id']
            conn.execute(
                'UPDATE parents SET first_name = ?, last_name = ? WHERE id = ?',
                (first_name, last_name or '', parent_id)
            )

        # Xabarnoma sozlamalarini yaratish
        conn.execute(
            '''INSERT OR IGNORE INTO parent_notification_settings (parent_id)
               VALUES (?)''',
            (parent_id,)
        )

        # Aloqani tasdiqlash
        conn.execute(
            '''UPDATE student_parents
               SET parent_id = ?, is_verified = 1, linked_at = ?
               WHERE id = ?''',
            (parent_id, now_str, rel['id'])
        )
        conn.commit()
        conn.close()

        return {
            'success': True,
            'student_id': rel['student_id'],
            'student_name': rel['student_name'],
            'parent_id': parent_id
        }

    def get_parent_by_chat_id(self, telegram_chat_id):
        """Telegram chat ID orqali ota-ona va uning farzandlarini olish."""
        conn = self._conn()
        parent = conn.execute(
            'SELECT * FROM parents WHERE telegram_chat_id = ?', (telegram_chat_id,)
        ).fetchone()
        if not parent:
            conn.close()
            return None

        children = conn.execute(
            '''SELECT s.id, s.name, s.login, sp.relationship_type, sp.linked_at
               FROM student_parents sp
               JOIN students s ON sp.student_id = s.id
               WHERE sp.parent_id = ? AND sp.is_verified = 1''',
            (parent['id'],)
        ).fetchall()
        conn.close()

        res = dict(parent)
        res['children'] = [dict(c) for c in children]
        return res

    def get_parents_for_student(self, student_id):
        """O'quvchiga biriktirilgan barcha ota-onalarni sozlamalari bilan olish."""
        conn = self._conn()
        rows = conn.execute(
            '''SELECT p.id as parent_id, p.telegram_chat_id, p.first_name, s.name as student_name,
                      ns.notify_grades, ns.notify_attendance, ns.notify_homework, ns.notify_daily_digest,
                      ns.quiet_hours_start, ns.quiet_hours_end
               FROM student_parents sp
               JOIN parents p ON sp.parent_id = p.id
               JOIN students s ON sp.student_id = s.id
               LEFT JOIN parent_notification_settings ns ON ns.parent_id = p.id
               WHERE sp.student_id = ? AND sp.is_verified = 1''',
            (student_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_parent_settings(self, parent_id):
        """Ota-onaning xabarnoma sozlamalarini olish."""
        conn = self._conn()
        row = conn.execute(
            'SELECT * FROM parent_notification_settings WHERE parent_id = ?', (parent_id,)
        ).fetchone()
        if not row:
            conn.execute(
                'INSERT INTO parent_notification_settings (parent_id) VALUES (?)', (parent_id,)
            )
            conn.commit()
            row = conn.execute(
                'SELECT * FROM parent_notification_settings WHERE parent_id = ?', (parent_id,)
            ).fetchone()
        conn.close()
        return dict(row)

    def toggle_parent_setting(self, parent_id, setting_key):
        """Xabarnoma sozlamasini yoqish/o'chirish."""
        allowed_keys = ['notify_grades', 'notify_attendance', 'notify_homework', 'notify_daily_digest']
        if setting_key not in allowed_keys:
            return None
        conn = self._conn()
        conn.execute(
            f'''UPDATE parent_notification_settings
                SET {setting_key} = CASE WHEN {setting_key} = 1 THEN 0 ELSE 1 END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE parent_id = ?''',
            (parent_id,)
        )
        conn.commit()
        updated = conn.execute(
            'SELECT * FROM parent_notification_settings WHERE parent_id = ?', (parent_id,)
        ).fetchone()
        conn.close()
        return dict(updated) if updated else None

    # ==================== Davomat (Attendance) ====================

    def add_attendance(self, student_id, slot_number, subject_name, status, minutes_late=0, note='', lesson_date=None):
        """Davomat yozuvini kiritish yoki yangilash."""
        from datetime import date
        ldate = lesson_date or date.today().isoformat()
        status = status.upper().strip()
        if status not in ['KELDI', 'KELMADI', 'KECHIKDI', 'SABABLI']:
            status = 'KELDI'
        conn = self._conn()
        conn.execute(
            '''INSERT INTO attendance (student_id, lesson_date, slot_number, subject_name, status, minutes_late, note)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(student_id, lesson_date, slot_number) DO UPDATE SET
                   subject_name = excluded.subject_name,
                   status = excluded.status,
                   minutes_late = excluded.minutes_late,
                   note = excluded.note,
                   created_at = CURRENT_TIMESTAMP''',
            (student_id, ldate, slot_number, subject_name, status, minutes_late, note)
        )
        conn.commit()
        aid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        conn.close()
        return aid

    def get_student_attendance(self, student_id, lesson_date=None):
        """O'quvchining davomatini olish (sana bo'yicha yoki oxirgi kunlar)."""
        conn = self._conn()
        if lesson_date:
            rows = conn.execute(
                '''SELECT a.*, s.name as student_name
                   FROM attendance a
                   JOIN students s ON a.student_id = s.id
                   WHERE a.student_id = ? AND a.lesson_date = ?
                   ORDER BY a.slot_number ASC''',
                (student_id, lesson_date)
            ).fetchall()
        else:
            rows = conn.execute(
                '''SELECT a.*, s.name as student_name
                   FROM attendance a
                   JOIN students s ON a.student_id = s.id
                   WHERE a.student_id = ?
                   ORDER BY a.lesson_date DESC, a.slot_number ASC
                   LIMIT 20''',
                (student_id,)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ==================== Ota-onaga Eslatmalar (Parent Notices) ====================

    def add_parent_notice(self, student_id, teacher_id, message, is_urgent=False):
        """O'qituvchidan ota-onaga shaxsiy ogohlantirish yoki xabar saqlash."""
        conn = self._conn()
        conn.execute(
            '''INSERT INTO parent_notices (student_id, teacher_id, message, is_urgent)
               VALUES (?, ?, ?, ?)''',
            (student_id, teacher_id, message, 1 if is_urgent else 0)
        )
        conn.commit()
        nid = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        conn.close()
        return nid

    def get_parent_notices(self, student_id):
        conn = self._conn()
        rows = conn.execute(
            '''SELECT pn.*, t.login as teacher_login
               FROM parent_notices pn
               LEFT JOIN teachers t ON pn.teacher_id = t.id
               WHERE pn.student_id = ?
               ORDER BY pn.sent_at DESC
               LIMIT 10''',
            (student_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_active_parents(self):
        """Barcha tasdiqlangan ota-onalar va ularning farzandlari (Kunlik hisobot uchun)."""
        conn = self._conn()
        rows = conn.execute(
            '''SELECT p.id as parent_id, p.telegram_chat_id, p.first_name,
                      s.id as student_id, s.name as student_name,
                      ns.notify_daily_digest, ns.quiet_hours_start, ns.quiet_hours_end
               FROM student_parents sp
               JOIN parents p ON sp.parent_id = p.id
               JOIN students s ON sp.student_id = s.id
               LEFT JOIN parent_notification_settings ns ON ns.parent_id = p.id
               WHERE sp.is_verified = 1'''
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
