"""Маршруты только для тестов: к приложению подключает их фикстура app, в боевой сборке их нет."""

import asyncio
import uuid
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import Table, insert, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, require
from app.modules.auth.principal import Principal
from app.modules.idempotency.service import IdempotencyKeyHeader, fingerprint, run_idempotent
from app.modules.objects.models import Project
from app.modules.rbac.enums import Action, Module

router = APIRouter(prefix="/api/v1/_test", tags=["test-only"])
PROJECT = cast(Table, Project.__table__)


class NewProject(BaseModel):
    name: str
    legal_entity_id: uuid.UUID
    delay_ms: int = 0
    fail: bool = False


@router.get("/whoami")
async def whoami(session: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, str | None]:
    row = (
        await session.execute(
            text(
                "SELECT current_user AS db_user, current_setting('app.user_id', true) AS user_id,"
                " current_setting('app.is_internal', true) AS internal,"
                " current_setting('app.sees_all_users', true) AS all_users,"
                " current_setting('app.request_id', true) AS request_id"
            )
        )
    ).one()
    return dict(row._mapping)


@router.post("/projects", status_code=201)
async def create_project(
    body: NewProject,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: Annotated[Principal, Depends(require(Module.OBJECTS, Action.WRITE))],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    async def handler() -> tuple[int, Any]:
        project_id = uuid.uuid4()
        await session.execute(
            insert(PROJECT).values(
                id=project_id,
                name=body.name,
                legal_entity_id=body.legal_entity_id,
                created_by=principal.user_id,
                updated_by=principal.user_id,
            )
        )
        if body.delay_ms:
            await asyncio.sleep(body.delay_ms / 1000)
        if body.fail:
            raise HTTPException(400, "failed after the insert")
        return 201, {"id": project_id, "name": body.name}

    result = await run_idempotent(
        session,
        user_id=principal.user_id,
        key=key,
        request_hash=fingerprint(request.method, request.url.path, body.model_dump(mode="json")),
        handler=handler,
    )
    return result.to_response()
