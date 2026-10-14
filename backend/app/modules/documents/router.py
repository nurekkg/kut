import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, internal_only, require
from app.modules.auth.principal import Principal
from app.modules.documents import service
from app.modules.documents.schemas import (
    AmountIn,
    DocumentCreate,
    DocumentDetail,
    DocumentOut,
    DownloadOut,
    KindOut,
    PresignOut,
    PresignRequest,
    VersionConfirm,
    VersionOut,
)
from app.modules.documents.types import KINDS
from app.modules.idempotency.service import IdempotencyKeyHeader, fingerprint, run_idempotent
from app.modules.rbac.enums import Action, Module

router = APIRouter(prefix="/documents", tags=["documents"])

Db = Annotated[AsyncSession, Depends(get_db)]
# Чтение доступно и внешним ролям: их видимость задаёт БД (ADR-015 часть 2 раздел 7).
Reader = Annotated[Principal, Depends(require(Module.DOCUMENTS, Action.READ))]
# Запись и архив - только внутренние роли.
Writer = Annotated[Principal, Depends(internal_only(require(Module.DOCUMENTS, Action.WRITE)))]
FinancialWriter = Annotated[
    Principal,
    Depends(internal_only(require(Module.DOCUMENTS, Action.READ, qualifier="financial"))),
]
Archiver = Annotated[
    Principal,
    Depends(internal_only(require(Module.DOCUMENTS, Action.WRITE, qualifier="archive"))),
]


def _no_store(response: Response) -> None:
    """Подписанные ссылки живут недолго: ответы с ними не должны оседать в кэше браузера."""
    response.headers["Cache-Control"] = "no-store"


@router.get("/kinds", response_model=list[KindOut])
async def kinds(_principal: Reader) -> list[KindOut]:
    """Справочник видов ADR-015: префикс номера, область, финансовый признак, обязательные связи."""
    return [
        KindOut(
            code=code,
            prefix=spec.prefix,
            scope=spec.scope.value,
            financial=spec.financial,
            counterparty_required=spec.counterparty,
            contract_required=spec.contract,
            links=sorted(link.value for link in spec.links),
        )
        for code, spec in KINDS.items()
    ]


MAX_OFFSET = 1_000_000  # выше значения БД даёт ошибку 500, а не пустой список


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    session: Db,
    principal: Reader,
    project_id: uuid.UUID | None = None,
    kind: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> list[DocumentOut]:
    return await service.list_documents(
        session, principal, project_id=project_id, kind=kind, limit=limit, offset=offset
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DocumentDetail)
async def create_document(
    body: DocumentCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: Writer,
    session: Db,
) -> Response:
    """Черновик документа (номер присваивается сразу); повтор с тем же ключом - тот же ответ."""

    async def handler() -> tuple[int, DocumentDetail]:
        return status.HTTP_201_CREATED, await service.create_document(session, principal, body)

    result = await run_idempotent(
        session,
        user_id=principal.user_id,
        key=key,
        request_hash=fingerprint(request.method, request.url.path, body.model_dump(mode="json")),
        handler=handler,
    )
    return result.to_response()


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: uuid.UUID, session: Db, principal: Reader) -> DocumentDetail:
    return await service.get_document(session, principal, document_id)


@router.put("/{document_id}/amount", status_code=status.HTTP_204_NO_CONTENT)
async def set_amount(
    document_id: uuid.UUID, body: AmountIn, session: Db, principal: FinancialWriter
) -> Response:
    await service.set_amount(session, principal, document_id, body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{document_id}/versions/presign", response_model=PresignOut, dependencies=[Depends(_no_store)]
)
async def presign(
    document_id: uuid.UUID, body: PresignRequest, session: Db, _principal: Writer
) -> PresignOut:
    """Ссылка на загрузку: файл уходит прямо в S3 с контрольной суммой SHA-256."""
    return await service.presign(session, document_id, body)


@router.post(
    "/{document_id}/versions/confirm",
    status_code=status.HTTP_201_CREATED,
    response_model=VersionOut,
)
async def confirm(
    document_id: uuid.UUID,
    body: VersionConfirm,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: Writer,
    session: Db,
) -> Response:
    """Подтверждение версии: проверка файла в S3 и запись версии (повтор - тот же ответ)."""

    async def handler() -> tuple[int, VersionOut]:
        created = await service.confirm(session, document_id, body)
        return status.HTTP_201_CREATED, created

    result = await run_idempotent(
        session,
        user_id=principal.user_id,
        key=key,
        request_hash=fingerprint(request.method, request.url.path, body.model_dump(mode="json")),
        handler=handler,
    )
    return result.to_response()


@router.get(
    "/{document_id}/versions/{version_id}/download-url",
    response_model=DownloadOut,
    dependencies=[Depends(_no_store)],
)
async def download_url(
    document_id: uuid.UUID, version_id: uuid.UUID, session: Db, _principal: Reader
) -> DownloadOut:
    return await service.download_url(session, document_id, version_id)


@router.post("/{document_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
async def archive(document_id: uuid.UUID, session: Db, _principal: Archiver) -> Response:
    await service.archive(session, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
