from redis.asyncio import Redis

from app.core.config import get_settings


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
