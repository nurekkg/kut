from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.config import get_settings
from app.modules.health.schemas import LiveResponse, ReadyResponse
from app.modules.health.service import DEFAULT_CHECKS, Check, run_readiness

router = APIRouter(prefix="/health", tags=["health"])


def get_checks() -> Mapping[str, Check]:
    return DEFAULT_CHECKS


@router.get("")
async def live(response: Response) -> LiveResponse:
    response.headers["Cache-Control"] = "no-store"
    return LiveResponse()


@router.get("/ready", responses={503: {"model": ReadyResponse}})
async def ready(
    response: Response, checks: Annotated[Mapping[str, Check], Depends(get_checks)]
) -> ReadyResponse:
    result = await run_readiness(checks, get_settings().health_check_timeout_seconds)
    response.headers["Cache-Control"] = "no-store"
    if result.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
