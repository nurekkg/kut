import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping

from app.core.db import check_db
from app.core.redis import check_redis
from app.core.storage import check_s3
from app.modules.health.schemas import CheckStatus, ReadyResponse

logger = logging.getLogger(__name__)

Check = Callable[[], Awaitable[None]]

DEFAULT_CHECKS: Mapping[str, Check] = {"db": check_db, "redis": check_redis, "s3": check_s3}


async def _run_check(name: str, check: Check, timeout_seconds: float) -> tuple[str, CheckStatus]:
    try:
        async with asyncio.timeout(timeout_seconds):
            await check()
    except Exception:
        logger.warning("readiness check %s failed", name, exc_info=True)
        return name, "fail"
    return name, "ok"


async def run_readiness(checks: Mapping[str, Check], timeout_seconds: float) -> ReadyResponse:
    results = dict(
        await asyncio.gather(*(_run_check(n, c, timeout_seconds) for n, c in checks.items()))
    )
    healthy = all(value == "ok" for value in results.values())
    return ReadyResponse(status="ok" if healthy else "degraded", checks=results)
