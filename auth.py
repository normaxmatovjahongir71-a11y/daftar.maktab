"""
Raqamli Daftar — Autentifikatsiya moduli
PBKDF2-SHA256 bilan parol shifrlash va token boshqaruvi.
"""

import hashlib
import os
import hmac
import time
import json
import base64


_SECRET_KEY = None


def _get_secret():
    """Bot tokenidan maxfiy kalit hosil qilish."""
    global _SECRET_KEY
    if _SECRET_KEY is None:
        bot_token = os.getenv('BOT_TOKEN', 'fallback-secret-key')
        _SECRET_KEY = hashlib.sha256(f"daftar-maktab-{bot_token}".encode()).digest()
    return _SECRET_KEY


def hash_password(password: str) -> tuple:
    """
    Parolni PBKDF2-SHA256 bilan shifrlash.
    Qaytaradi: (password_hash_hex, salt_hex)
    """
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 260000)
    return key.hex(), salt.hex()


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Parolni tekshirish. True/False qaytaradi."""
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        bytes.fromhex(salt),
        260000
    )
    return hmac.compare_digest(key.hex(), password_hash)


def create_token(user_id: int, role: str, login: str = '', name: str = '') -> str:
    """
    Autentifikatsiya tokeni yaratish (7 kunlik).
    role: 'teacher' yoki 'student'
    """
    payload = {
        'uid': user_id,
        'role': role,
        'login': login,
        'name': name,
        'exp': int(time.time()) + 86400 * 7
    }
    data = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False).encode()
    ).decode().rstrip('=')
    sig = hmac.new(_get_secret(), data.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{data}.{sig}"


def verify_token(token: str):
    """
    Tokenni tekshirish.
    Muvaffaqiyatli bo'lsa payload dict qaytaradi, aks holda None.
    """
    try:
        parts = token.rsplit('.', 1)
        if len(parts) != 2:
            return None
        data, sig = parts
        expected = hmac.new(_get_secret(), data.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        padding = 4 - len(data) % 4
        if padding != 4:
            data += '=' * padding
        payload = json.loads(base64.urlsafe_b64decode(data))
        if payload.get('exp', 0) < time.time():
            return None
        return payload
    except Exception:
        return None
