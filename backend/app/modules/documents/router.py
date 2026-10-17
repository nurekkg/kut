import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, get_principal, internal_only, require
from app.modules.auth.principal import Principal
from app.modules.documents import service
from app.modules.documents.schemas import (
    AmountIn,
    CounterpartyOut,
    DocumentCreate,
    DocumentDetail,
    DocumentOut,
    DownloadOut,
    EventOut,
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


async def _reader(principal: Principal = Depends(get_principal)) -> Principal:
    """Чтение: право на документы или на документы техники (R2, equipment:read:documents). Вид
    отбирает БД: без документного права видны только виды техники."""
    if principal.has(Module.DOCUMENTS, Action.READ) or principal.has(
        Module.EQUIPMENT, Action.READ, "documents"
    ):
        return principal
    raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")


# Чтение доступно и внешним ролям: их видимость задаёт БД (ADR-015 часть 2 раздел 7).
Reader = Annotated[Principal, Depends(_reader)]
# Журнал статусов - только внутренним; право на конкретный вид проверяет сервис и БД.
Viewer = Annotated[Principal, Depends(internal_only(require(Module.DOCUMENTS, Action.READ)))]
Exporter = Annotated[Principal, Depends(internal_only(require(Module.DOCUMENTS, Action.EXPORT)))]
# Запись: любой внутренний пользователь; право на вид (документы или техника) проверяет сервис, а
# БД - политика.
Internal = Annotated[Principal, Depends(internal_only(get_principal))]
Archiver = Annotated[
    Principal,
    Depends(internal_only(require(Module.DOCUMENTS, Action.WRITE, qualifier="archive"))),
]
VersionStatus = Literal["draft", "on_approval", "approved", "signed", "archived"]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _no_store(response: Response) -> None:
    """Подписанные ссылки живут недолго: ответы с ними не должны оседать в кэше браузера."""
    response.headers["Cache-Control"] = "no-store"


def registry_filters(
    project_id: uuid.UUID | None = None,
    kind: Annotated[str | None, Query(max_length=32)] = None,
    status: VersionStatus | None = None,
    counterparty_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(max_length=64)] = None,
    equipment_unit_id: uuid.UUID | None = None,
    project_node_id: uuid.UUID | None = None,
) -> service.RegistryFilters:
    """Фильтры реестра и выгрузки: один набор параметров на оба маршрута."""
    return service.RegistryFilters(
        project_id=project_id,
        kind=kind,
        status=status,
        counterparty_id=counterparty_id,
        date_from=date_from,
        date_to=date_to,
        q=(q or "").strip() or None,
        equipment_unit_id=equipment_unit_id,
        project_node_id=project_node_id,
    )


Filters = Annotated[service.RegistryFilters, Depends(registry_filters)]
MAX_OFFSET = 1_000_000  # выше значения БД даёт ошибку 500, а не пустой список


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


@router.get("/counterparties", response_model=list[CounterpartyOut])
async def counterparties(
    session: Db, _principal: Viewer, q: Annotated[str | None, Query(max_length=64)] = None
) -> list[CounterpartyOut]:
    """Контрагенты для выбора в документах: id и название (RLS справочника)."""
    return await service.list_counterparties(session, q)


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    session: Db,
    principal: Reader,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> list[DocumentOut]:
    return await service.list_documents(session, principal, filters, limit=limit, offset=offset)


@router.get("/export.xlsx")
async def export_documents(session: Db, principal: Exporter, filters: Filters) -> Response:
    """Реестр в XLSX (право documents:export). Суммы - только с documents:read:financial."""
    content = await service.export_xlsx(session, principal, filters)
    return Response(
        content=content,
        media_type=XLSX,
        headers={
            "Content-Disposition": 'attachment; filename="documents.xlsx"',
            "Cache-Control": "no-store",
        },
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DocumentDetail)
async def create_document(
    body: DocumentCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: Internal,
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


@router.get("/{document_id}/events", response_model=list[EventOut])
async def document_events(
    document_id: uuid.UUID, session: Db, _principal: Viewer
) -> list[EventOut]:
    """Журнал переходов статуса по версиям документа (только внутренним)."""
    return await service.list_events(session, document_id)


@router.put("/{document_id}/versions/{version_id}/amount", status_code=status.HTTP_204_NO_CONTENT)
async def set_version_amount(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    body: AmountIn,
    session: Db,
    principal: Internal,
) -> Response:
    """Сумма черновой версии (PO 2026-10-17, R1). После выхода из черновика - 409
    document.version_not_draft."""
    await service.set_version_amount(session, principal, document_id, version_id, body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{document_id}/versions/presign", response_model=PresignOut, dependencies=[Depends(_no_store)]
)
async def presign(
    document_id: uuid.UUID, body: PresignRequest, session: Db, _principal: Internal
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
    principal: Internal,
    session: Db,
) -> Response:
    """Подтверждение версии: проверка файла в S3 и запись версии (повтор - тот же ответ)."""

    async def handler() -> tuple[int, VersionOut]:
        created = await service.confirm(session, principal, document_id, body)
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
