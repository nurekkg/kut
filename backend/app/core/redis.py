from functools import lru_cache

from redis.asyncio import Redis

from app.core.config import get_settings


@lru_cache
def get_redis() -> Redis:
    """Общий клиент для кэша, denylist и лимитов; check_redis ниже ходит отдельным соединением."""
    client: Redis = Redis.from_url(
        get_settings().redis_url.get_secret_value(),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    return client


async def close_redis() -> None:
    if get_redis.cache_info().currsize:
        await get_redis().aclose()
        get_redis.cache_clear()


async def check_redis() -> None:
    client = Redis.from_url(
        get_settings().redis_url.get_secret_value(),
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()
