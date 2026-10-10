import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, internal_only, require
from app.modules.auth.principal import Principal
from app.modules.idempotency.service import IdempotencyKeyHeader, fingerprint, run_idempotent
from app.modules.photos import service
from app.modules.photos.schemas import (
    DownloadUrl,
    PhotoConfirm,
    PhotoOut,
    PresignRequest,
    PresignResponse,
)
from app.modules.rbac.enums import Action, Module

router = APIRouter(prefix="/projects/{project_id}/photos", tags=["photos"])

Db = Annotated[AsyncSession, Depends(get_db)]


def _no_store(response: Response) -> None:
    """Подписанные ссылки живут недолго: ответы с ними не должны оседать в кэше браузера."""
    response.headers["Cache-Control"] = "no-store"


# Фото в MVP видят только внутренние роли: заказчику и субподрядчику они не отдаются
# (на снимках люди, техника и состояние площадки; открыть их внешним - отдельное решение PO).
Reader = Annotated[
    Principal,
    Depends(internal_only(require(Module.WORK_ACTUAL, Action.READ, project_param="project_id"))),
]
Writer = Annotated[
    Principal,
    Depends(internal_only(require(Module.WORK_ACTUAL, Action.WRITE, project_param="project_id"))),
]


@router.post("/presign", response_model=PresignResponse, dependencies=[Depends(_no_store)])
async def presign(
    project_id: uuid.UUID, body: PresignRequest, session: Db, _principal: Writer
) -> PresignResponse:
    """Ссылки на загрузку: файлы уходят прямо в S3, через API они не проходят."""
    return await service.presign(session, project_id, body)


@router.post("/confirm", status_code=status.HTTP_201_CREATED, response_model=list[PhotoOut])
async def confirm(
    project_id: uuid.UUID,
    body: PhotoConfirm,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: Writer,
    session: Db,
) -> Response:
    """Подтверждение загрузки: после проверки объектов в S3 создаются записи о фото (повтор с тем
    же Idempotency-Key отдаёт прежний ответ)."""

    async def handler() -> tuple[int, list[PhotoOut]]:
        created = await service.confirm(session, project_id, body, principal.user_id)
        return status.HTTP_201_CREATED, created

    result = await run_idempotent(
        session,
        user_id=principal.user_id,
        key=key,
        request_hash=fingerprint(request.method, request.url.path, body.model_dump(mode="json")),
        handler=handler,
    )
    return result.to_response()


MAX_OFFSET = 1_000_000  # выше значения БД даёт ошибку 500, а не пустой список


@router.get("", response_model=list[PhotoOut], dependencies=[Depends(_no_store)])
async def list_photos(
    project_id: uuid.UUID,
    session: Db,
    _principal: Reader,
    work_actual_id: Annotated[
        list[uuid.UUID] | None,
        Query(max_length=100, description="один или несколько фактов (параметр повторяется)"),
    ] = None,
    node_id: uuid.UUID | None = None,
    subtree: Annotated[bool, Query(description="узел и все его потомки")] = False,
    newest_first: Annotated[bool, Query(description="сначала новые")] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
    with_urls: Annotated[
        bool, Query(description="добавить короткоживущие ссылки на скачивание")
    ] = False,
) -> list[PhotoOut]:
    return await service.list_photos(
        session,
        project_id,
        actual_ids=work_actual_id,
        node_id=node_id,
        subtree=subtree,
        newest_first=newest_first,
        limit=limit,
        offset=offset,
        with_urls=with_urls,
    )


@router.get(
    "/{photo_id}/download-url", response_model=DownloadUrl, dependencies=[Depends(_no_store)]
)
async def download_url(
    project_id: uuid.UUID, photo_id: uuid.UUID, session: Db, _principal: Reader
) -> DownloadUrl:
    return await service.download_url(session, project_id, photo_id)


@router.delete("/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_photo(
    project_id: uuid.UUID, photo_id: uuid.UUID, session: Db, principal: Writer
) -> Response:
    await service.delete_photo(session, project_id, photo_id, principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
