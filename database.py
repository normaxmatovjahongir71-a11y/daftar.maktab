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
        ''')
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

    def is_admin(self, telegram_chat_id):
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

    # ==================== O'quvchilar ====================

    def create_student(self, name, login, password):
        pw_hash, salt = hash_password(password)
        conn = self._conn()
        try:
            conn.execute(
                'INSERT INTO students (name, login, password_hash, salt) VALUES (?, ?, ?, ?)',
                (name, login, pw_hash, salt)
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

    def get_students(self):
        conn = self._conn()
        rows = conn.execute(
            'SELECT id, name, login, telegram_chat_id FROM students ORDER BY name'
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_student_by_id(self, student_id):
        conn = self._conn()
        row = conn.execute(
            'SELECT id, name, login, telegram_chat_id FROM students WHERE id = ?',
            (student_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_student_by_login(self, login):
        conn = self._conn()
        row = conn.execute(
            'SELECT id, name, login, telegram_chat_id FROM students WHERE login = ?',
            (login,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

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
            'SELECT id, name, login FROM students WHERE telegram_chat_id = ?',
            (chat_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

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
            '''SELECT g.id, g.student_id, g.subject, g.grade, g.created_at,
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
            '''SELECT g.id, g.student_id, g.subject, g.grade, g.created_at,
                      s.name as student_name, s.login as student_login
               FROM grades g
               JOIN students s ON g.student_id = s.id
               ORDER BY g.created_at DESC'''
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_grade(self, student_id, subject, grade):
        conn = self._conn()
        conn.execute(
            'INSERT INTO grades (student_id, subject, grade) VALUES (?, ?, ?)',
            (student_id, subject, grade)
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
