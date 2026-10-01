import asyncio
import hashlib
import re
import secrets
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128

# argon2-cffi по умолчанию: argon2id с параметрами RFC 9106 (low-memory профиль).
_hasher = PasswordHasher()


@lru_cache
def _dummy_hash() -> str:
    # Хэш для несуществующего пользователя: ответ занимает столько же, сколько проверка настоящего.
    return _hasher.hash(secrets.token_urlsafe(24))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Всегда выполняет одну проверку argon2, даже если пользователя или хэша нет."""
    target = stored_hash or _dummy_hash()
    try:
        matched = _hasher.verify(target, password)
    except (VerificationError, InvalidHashError):
        return False
    return bool(matched) and stored_hash is not None


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(stored_hash: str | None, password: str) -> bool:
    return await asyncio.to_thread(verify_password, stored_hash, password)


def sha256(value: str | bytes) -> bytes:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).digest()


def new_opaque_token() -> str:
    """256 бит случайности: в БД хранится только SHA-256 (ADR-001 §2)."""
    return secrets.token_urlsafe(32)


def normalize_login(value: str) -> str:
    """Email в нижнем регистре или телефон в виде +цифры; БД хранит ровно такие значения."""
    cleaned = value.strip()
    if "@" in cleaned:
        return cleaned.lower()
    digits = re.sub(r"[\s\-()]", "", cleaned)
    if digits and not digits.startswith("+") and digits.isdigit():
        digits = "+" + digits
    return digits
