import hashlib
import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, get_principal, internal_only, require
from app.modules.auth.principal import Principal
from app.modules.documents import lines, lines_import, routes, service
from app.modules.documents.schemas import (
    AmountIn,
    CounterpartyOut,
    DecisionIn,
    DelegationIn,
    DelegationOut,
    DocumentCreate,
    DocumentDetail,
    DocumentOut,
    DownloadOut,
    EventOut,
    KindOut,
    LinesIn,
    LinesOut,
    PresignOut,
    PresignRequest,
    PricesIn,
    QueueItemOut,
    RouteOut,
    SignedScanIn,
    TemplateIn,
    TemplateOut,
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
# Шаблоны маршрута (S6a-2): право documents:write:routes (владелец, техдиректор).
RoutesWriter = Annotated[
    Principal,
    Depends(internal_only(require(Module.DOCUMENTS, Action.WRITE, qualifier="routes"))),
]
# Позиции версии: право documents:write:lines (ADR-015 часть 4); деньги проверяют сервис и БД.
LinesWriter = Annotated[
    Principal,
    Depends(internal_only(require(Module.DOCUMENTS, Action.WRITE, qualifier="lines"))),
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
            lines=spec.lines,
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


# --- маршруты согласования (S6a-2): списки и шаблоны ---------------------------------------------


@router.get("/approvals", response_model=list[QueueItemOut])
async def approvals_queue(session: Db, _principal: Viewer) -> list[QueueItemOut]:
    """Очередь «На согласовании»: версии, где текущий шаг мой (роль или замещение) и я вправе
    решать."""
    return await routes.queue(session)


@router.get("/route-templates", response_model=list[TemplateOut])
async def route_templates(session: Db, _principal: Viewer) -> list[TemplateOut]:
    return await routes.templates(session)


@router.get("/delegations", response_model=list[DelegationOut])
async def list_delegations(session: Db, _principal: Viewer) -> list[DelegationOut]:
    """Замещения, в которых участвую я (делегирующий или замещающий), действующие и отозванные
    недавно."""
    return await routes.delegations(session)


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
async def archive(document_id: uuid.UUID, session: Db, principal: Archiver) -> Response:
    await service.archive(session, principal, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _body(request: Request) -> bytes:
    """Файл импорта - тело запроса; больше лимита - 413 уже при чтении потока."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > lines_import.MAX_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "document.lines_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/lines/template.xlsx")
async def lines_template(_principal: Reader) -> Response:
    """Шаблон импорта позиций: строка заголовка, без формул и макросов."""
    return Response(
        content=lines_import.template_xlsx(),
        media_type=XLSX,
        headers={
            "Content-Disposition": 'attachment; filename="document-lines-template.xlsx"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{document_id}/versions/{version_id}/lines", response_model=LinesOut)
async def version_lines(
    document_id: uuid.UUID, version_id: uuid.UUID, session: Db, _principal: Reader
) -> LinesOut:
    """Позиции видны всем, кто видит версию; цена и сумма - только с правом на деньги вида."""
    return await lines.list_lines(session, document_id, version_id)


@router.put("/{document_id}/versions/{version_id}/lines", response_model=LinesOut)
async def replace_version_lines(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    body: LinesIn,
    request: Request,
    key: IdempotencyKeyHeader,
    session: Db,
    principal: LinesWriter,
) -> Response:
    """Полный список позиций черновика; повтор с тем же ключом - тот же ответ."""

    async def handler() -> tuple[int, LinesOut]:
        return status.HTTP_200_OK, await lines.replace_lines(
            session, principal, document_id, version_id, body
        )

    result = await run_idempotent(
        session,
        user_id=principal.user_id,
        key=key,
        request_hash=fingerprint(request.method, request.url.path, body.model_dump(mode="json")),
        handler=handler,
    )
    return result.to_response()


@router.put("/{document_id}/versions/{version_id}/line-prices", response_model=LinesOut)
async def set_version_prices(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    body: PricesIn,
    request: Request,
    key: IdempotencyKeyHeader,
    session: Db,
    principal: Internal,
) -> Response:
    """Цены черновика; без права на деньги вида - 403. Повтор с тем же ключом - тот же ответ."""

    async def handler() -> tuple[int, LinesOut]:
        return status.HTTP_200_OK, await lines.set_prices(
            session, principal, document_id, version_id, body
        )

    result = await run_idempotent(
        session,
        user_id=principal.user_id,
        key=key,
        request_hash=fingerprint(request.method, request.url.path, body.model_dump(mode="json")),
        handler=handler,
    )
    return result.to_response()


@router.post("/{document_id}/versions/{version_id}/lines/import", response_model=LinesOut)
async def import_version_lines(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    key: IdempotencyKeyHeader,
    session: Db,
    principal: LinesWriter,
) -> Response:
    """Импорт XLSX в черновик: полная замена позиций. Файл - тело запроса, до 2 МБ."""
    data = await _body(request)

    async def handler() -> tuple[int, LinesOut]:
        return status.HTTP_200_OK, await lines.import_lines(
            session, principal, document_id, version_id, data
        )

    result = await run_idempotent(
        session,
        user_id=principal.user_id,
        key=key,
        request_hash=fingerprint(
            request.method, request.url.path, {"sha256": hashlib.sha256(data).hexdigest()}
        ),
        handler=handler,
    )
    return result.to_response()


# --- маршруты согласования (S6a-2): отправка, решения, замещение, подписанный скан ---------------


@router.put("/route-templates/{code}", response_model=list[TemplateOut])
async def save_route_template(
    code: str, body: TemplateIn, session: Db, _principal: RoutesWriter
) -> list[TemplateOut]:
    """Шаги шаблона целиком, по порядку. Владелец и техдиректор; уже отправленные маршруты не
    меняются."""
    return await routes.save_template(session, code, list(body.roles))


@router.get("/{document_id}/versions/{version_id}/route", response_model=RouteOut)
async def version_route(
    document_id: uuid.UUID, version_id: uuid.UUID, session: Db, _principal: Viewer
) -> RouteOut:
    return await routes.route(session, document_id, version_id)


@router.post("/{document_id}/versions/{version_id}/send", response_model=RouteOut)
async def send_for_approval(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    key: IdempotencyKeyHeader,
    session: Db,
    principal: Internal,
) -> Response:
    """Отправка версии на согласование: снимок шагов шаблона, попытка N, версия на согласовании."""

    async def handler() -> tuple[int, RouteOut]:
        return status.HTTP_200_OK, await routes.send(session, document_id, version_id)

    result = await run_idempotent(
        session,
        user_id=principal.user_id,
        key=key,
        request_hash=fingerprint(request.method, request.url.path, {}),
        handler=handler,
    )
    return result.to_response()


@router.post("/{document_id}/versions/{version_id}/decisions", response_model=RouteOut)
async def decide_step(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    body: DecisionIn,
    request: Request,
    key: IdempotencyKeyHeader,
    session: Db,
    principal: Internal,
) -> Response:
    """Решение по текущему шагу. Повтор того же решения по тому же шагу - no-op, ответ 200."""

    async def handler() -> tuple[int, RouteOut]:
        return status.HTTP_200_OK, await routes.decide(session, document_id, version_id, body)

    result = await run_idempotent(
        session,
        user_id=principal.user_id,
        key=key,
        request_hash=fingerprint(request.method, request.url.path, body.model_dump(mode="json")),
        handler=handler,
    )
    return result.to_response()


@router.post("/delegations", status_code=status.HTTP_201_CREATED, response_model=DelegationOut)
async def create_delegation(
    body: DelegationIn,
    request: Request,
    key: IdempotencyKeyHeader,
    session: Db,
    principal: Internal,
) -> Response:
    """Замещение на шаге: делегирует держатель роли, на период не раньше сегодня."""

    async def handler() -> tuple[int, DelegationOut]:
        return status.HTTP_201_CREATED, await routes.create_delegation(session, body)

    result = await run_idempotent(
        session,
        user_id=principal.user_id,
        key=key,
        request_hash=fingerprint(request.method, request.url.path, body.model_dump(mode="json")),
        handler=handler,
    )
    return result.to_response()


@router.post("/delegations/{delegation_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_delegation(
    delegation_id: uuid.UUID, session: Db, _principal: Internal
) -> Response:
    await routes.revoke_delegation(session, delegation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{document_id}/versions/{version_id}/signed",
    status_code=status.HTTP_201_CREATED,
    response_model=VersionOut,
    dependencies=[Depends(internal_only(require(Module.DOCUMENTS, Action.APPROVE)))],
)
async def upload_signed_scan(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    body: SignedScanIn,
    request: Request,
    key: IdempotencyKeyHeader,
    session: Db,
    principal: Internal,
) -> Response:
    """Подписанный скан к согласованной версии: новая версия `signed`, она действующая."""

    async def handler() -> tuple[int, VersionOut]:
        return status.HTTP_201_CREATED, await routes.sign_scan(
            session, principal, document_id, version_id, body
        )

    result = await run_idempotent(
        session,
        user_id=principal.user_id,
        key=key,
        request_hash=fingerprint(request.method, request.url.path, body.model_dump(mode="json")),
        handler=handler,
    )
    return result.to_response()
