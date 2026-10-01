import hashlib
import uuid
from collections.abc import Sequence

from app.core.config import get_settings
from app.core.redis import get_redis


def login_key(identifier: str) -> str:
    # Ключ от хэша: ни в Redis, ни в логах нет самого логина (телефона или email).
    return "rl:login:" + hashlib.sha256(identifier.encode()).hexdigest()


def ip_key(ip: str) -> str:
    return f"rl:ip:{ip}"


def mfa_key(user_id: uuid.UUID) -> str:
    return f"rl:mfa:{user_id}"


def mfa_ip_key(ip: str) -> str:
    return f"rl:mfa-ip:{ip}"


def password_key(user_id: uuid.UUID) -> str:
    return f"rl:pwd:{user_id}"


async def blocked_for(keys: Sequence[str]) -> int:
    """Сколько секунд осталось блокировки (0, если можно пробовать).

    Считаем неудачные попытки: на пятой подряд за окно доступ закрывается и для верного пароля.
    """
    settings = get_settings()
    redis = get_redis()
    remaining = 0
    for key in keys:
        count = await redis.get(key)
        if count is not None and int(count) >= settings.login_rate_limit_attempts:
            remaining = max(remaining, max(await redis.ttl(key), 1))
    return remaining


async def record_failure(keys: Sequence[str]) -> None:
    settings = get_settings()
    redis = get_redis()
    for key in keys:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, settings.login_rate_limit_window_seconds)


async def reset(key: str) -> None:
    await get_redis().delete(key)
