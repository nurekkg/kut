import uuid
from collections.abc import Iterable

from app.core.config import get_settings
from app.core.redis import get_redis


def _key(sid: uuid.UUID) -> str:
    return f"deny:sid:{sid}"


async def deny(sids: Iterable[uuid.UUID]) -> None:
    """Отзыв access-токенов до истечения: запись живёт не меньше самого токена (ADR-001 §2)."""
    ttl = get_settings().access_ttl_seconds + 30
    redis = get_redis()
    async with redis.pipeline(transaction=False) as pipeline:
        for sid in sids:
            pipeline.set(_key(sid), "1", ex=ttl)
        await pipeline.execute()


async def is_denied(sid: uuid.UUID) -> bool:
    return bool(await get_redis().exists(_key(sid)))
