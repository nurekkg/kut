from typing import Literal

from pydantic import BaseModel

CheckStatus = Literal["ok", "fail"]


class LiveResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, CheckStatus]
