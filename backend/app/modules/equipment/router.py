import hashlib
import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.deps import get_db, get_principal, require
from app.modules.auth.principal import Principal
from app.modules.equipment import imports, planning, rates, service
from app.modules.equipment.models import EquipmentImportBatch
from app.modules.equipment.schemas import (
    AssignmentCheck,
    AssignmentCreate,
    AssignmentCreated,
    AssignmentOut,
    AssignmentPatch,
    BatchOut,
    CalendarOut,
    ContractCreate,
    ContractOut,
    ContractPatch,
    CounterpartyRef,
    DocumentCreate,
    DocumentOut,
    ImportIssue,
    ImportPayload,
    ImportReport,
    MaintenanceCreate,
    MaintenanceOut,
    MaintenancePatch,
    OperatorCreate,
    OperatorOut,
    OwnRateCreate,
    OwnRateOut,
    RateCreate,
    RateOut,
    RatePatch,
    ReasonIn,
    RequestCreate,
    RequestOut,
    RequestPatch,
    TypeCreate,
    TypeOut,
    UnitCard,
    UnitCreate,
    UnitListOut,
    UnitOut,
    UnitPatch,
)
from app.modules.idempotency.service import IdempotencyKeyHeader, idempotent_create
from app.modules.rbac.enums import Action, Module

router = APIRouter(prefix="/equipment", tags=["equipment"])

Db = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[Principal, Depends(require(Module.EQUIPMENT, Action.READ))]
Writer = Annotated[Principal, Depends(require(Module.EQUIPMENT, Action.WRITE))]
RatesReader = Annotated[
    Principal, Depends(require(Module.EQUIPMENT, Action.READ, qualifier="rates"))
]
RateApprover = Annotated[
    Principal, Depends(require(Module.EQUIPMENT, Action.APPROVE, qualifier="rate"))
]
RequestWriter = Annotated[
    Principal, Depends(require(Module.EQUIPMENT, Action.WRITE, qualifier="request"))
]
Assigner = Annotated[
    Principal, Depends(require(Module.EQUIPMENT, Action.APPROVE, qualifier="assign"))
]
MaintenanceWriter = Annotated[
    Principal, Depends(require(Module.EQUIPMENT, Action.WRITE, qualifier="maintenance"))
]
FinanceWriter = Annotated[Principal, Depends(require(Module.FINANCE, Action.WRITE))]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")


async def contract_reader(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    """Шапка аренды: деньги по технике или ведение реестра (ADR-014 часть 2 §5)."""
    if principal.has(Module.EQUIPMENT, Action.READ, "rates") or principal.has(
        Module.EQUIPMENT, Action.WRITE
    ):
        return principal
    raise _forbidden()


async def request_actor(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    """Отмена заявки: автор (write:request) или утверждающий (approve:assign)."""
    if principal.has(Module.EQUIPMENT, Action.WRITE, "request") or principal.has(
        Module.EQUIPMENT, Action.APPROVE, "assign"
    ):
        return principal
    raise _forbidden()


ContractReader = Annotated[Principal, Depends(contract_reader)]
RequestActor = Annotated[Principal, Depends(request_actor)]


async def _body(request: Request) -> bytes:
    """Файл импорта - тело запроса: multipart требует python-multipart, которого в образе нет."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > imports.MAX_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "equipment.import_too_large"
            )
        chunks.append(chunk)
    return b"".join(chunks)


FileName = Annotated[str, Query(min_length=1, max_length=200)]


# --- виды техники ------------------------------------------------------------------------------


@router.get(
    "/types",
    response_model=list[TypeOut],
    dependencies=[Depends(require(Module.EQUIPMENT, Action.READ))],
)
async def list_types(session: Db) -> list[TypeOut]:
    return await service.list_types(session)


@router.post("/types", status_code=status.HTTP_201_CREATED, response_model=TypeOut)
async def create_type(
    body: TypeCreate, request: Request, key: IdempotencyKeyHeader, principal: Writer, session: Db
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: service.create_type(session, body),
    )


# --- реестр ------------------------------------------------------------------------------------


@router.get("/units", response_model=list[UnitListOut])
async def list_units(
    _principal: Reader,
    session: Db,
    type_id: uuid.UUID | None = None,
    ownership: Literal["own", "rented"] | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    status_filter: Annotated[
        Literal["available", "assigned", "day_full", "in_repair", "doc_warning", "doc_expired"]
        | None,
        Query(alias="status"),
    ] = None,
    project_id: uuid.UUID | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> list[UnitListOut]:
    return await service.list_units(
        session,
        type_id=type_id,
        ownership=ownership,
        query=q,
        status_filter=status_filter,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )


@router.post("/units", status_code=status.HTTP_201_CREATED, response_model=UnitOut)
async def create_unit(
    body: UnitCreate, request: Request, key: IdempotencyKeyHeader, principal: Writer, session: Db
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: service.create_unit(session, body),
    )


@router.get("/units/{unit_id}", response_model=UnitCard)
async def get_unit(unit_id: uuid.UUID, _principal: Reader, session: Db) -> UnitCard:
    return await service.unit_card(session, unit_id)


@router.patch("/units/{unit_id}", response_model=UnitOut)
async def update_unit(
    unit_id: uuid.UUID, body: UnitPatch, _principal: Writer, session: Db
) -> UnitOut:
    return await service.update_unit(session, unit_id, body)


@router.get("/units/{unit_id}/documents", response_model=list[DocumentOut])
async def list_documents(unit_id: uuid.UUID, _principal: Reader, session: Db) -> list[DocumentOut]:
    return await service.list_documents(session, unit_id)


@router.post(
    "/units/{unit_id}/documents", status_code=status.HTTP_201_CREATED, response_model=DocumentOut
)
async def add_document(
    unit_id: uuid.UUID,
    body: DocumentCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: MaintenanceWriter,
    session: Db,
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: service.add_document(session, unit_id, body),
    )


# --- ТО и ремонты ------------------------------------------------------------------------------


@router.get("/maintenance", response_model=list[MaintenanceOut])
async def list_maintenance(
    _principal: Reader,
    session: Db,
    unit_id: uuid.UUID | None = None,
    status_filter: Annotated[
        Literal["planned", "in_progress", "done", "cancelled"] | None, Query(alias="status")
    ] = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> list[MaintenanceOut]:
    return await service.list_maintenance(
        session, unit_id=unit_id, status_filter=status_filter, limit=limit, offset=offset
    )


@router.post("/maintenance", status_code=status.HTTP_201_CREATED, response_model=MaintenanceOut)
async def create_maintenance(
    body: MaintenanceCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: MaintenanceWriter,
    session: Db,
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: service.create_maintenance(session, body),
    )


@router.patch("/maintenance/{maintenance_id}", response_model=MaintenanceOut)
async def transition_maintenance(
    maintenance_id: uuid.UUID, body: MaintenancePatch, _principal: MaintenanceWriter, session: Db
) -> MaintenanceOut:
    return await service.transition_maintenance(session, maintenance_id, body)


# --- нормативы своей техники (деньги) ----------------------------------------------------------


@router.get(
    "/own-rates",
    response_model=list[OwnRateOut],
    dependencies=[Depends(require(Module.EQUIPMENT, Action.READ, qualifier="rates"))],
)
async def list_own_rates(
    session: Db,
    legal_entity_id: uuid.UUID | None = None,
    type_id: uuid.UUID | None = None,
) -> list[OwnRateOut]:
    return await rates.list_own_rates(session, legal_entity_id=legal_entity_id, type_id=type_id)


@router.post(
    "/own-rates",
    status_code=status.HTTP_201_CREATED,
    response_model=OwnRateOut,
    dependencies=[Depends(require(Module.EQUIPMENT, Action.READ, qualifier="rates"))],
)
async def propose_own_rate(
    body: OwnRateCreate, request: Request, key: IdempotencyKeyHeader, principal: Writer, session: Db
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: rates.propose_own_rate(session, body, principal.user_id),
    )


@router.post("/own-rates/{rate_id}/approve", response_model=OwnRateOut)
async def approve_own_rate(rate_id: uuid.UUID, principal: RateApprover, session: Db) -> OwnRateOut:
    return await rates.approve_own_rate(session, rate_id, principal.user_id)


# --- договоры аренды и тарифы ------------------------------------------------------------------


@router.get("/contracts", response_model=list[ContractOut])
async def list_contracts(
    _principal: ContractReader,
    session: Db,
    legal_entity_id: uuid.UUID | None = None,
    status_filter: Annotated[
        Literal["draft", "active", "closed", "terminated"] | None, Query(alias="status")
    ] = None,
) -> list[ContractOut]:
    return await rates.list_contracts(
        session, legal_entity_id=legal_entity_id, status_filter=status_filter
    )


@router.post("/contracts", status_code=status.HTTP_201_CREATED, response_model=ContractOut)
async def create_contract(
    body: ContractCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: FinanceWriter,
    session: Db,
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: rates.create_contract(session, body),
    )


@router.patch("/contracts/{contract_id}", response_model=ContractOut)
async def patch_contract(
    contract_id: uuid.UUID, body: ContractPatch, _principal: FinanceWriter, session: Db
) -> ContractOut:
    return await rates.patch_contract(session, contract_id, body)


@router.get(
    "/contracts/{contract_id}/rates",
    response_model=list[RateOut],
    dependencies=[Depends(require(Module.EQUIPMENT, Action.READ, qualifier="rates"))],
)
async def list_rates(contract_id: uuid.UUID, session: Db) -> list[RateOut]:
    return await rates.list_rates(session, contract_id)


@router.post(
    "/contracts/{contract_id}/rates",
    status_code=status.HTTP_201_CREATED,
    response_model=RateOut,
    dependencies=[Depends(require(Module.EQUIPMENT, Action.READ, qualifier="rates"))],
)
async def add_rate(
    contract_id: uuid.UUID,
    body: RateCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: FinanceWriter,
    session: Db,
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: rates.add_rate(session, contract_id, body),
    )


@router.patch(
    "/contracts/{contract_id}/rates/{rate_id}",
    response_model=RateOut,
    dependencies=[Depends(require(Module.EQUIPMENT, Action.READ, qualifier="rates"))],
)
async def patch_rate(
    contract_id: uuid.UUID,
    rate_id: uuid.UUID,
    body: RatePatch,
    _principal: FinanceWriter,
    session: Db,
) -> RateOut:
    """Окончание тарифа (valid_to): выбор по дате работы его больше не берёт."""
    return await rates.patch_rate(session, contract_id, rate_id, body)


@router.delete(
    "/contracts/{contract_id}/rates/{rate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require(Module.EQUIPMENT, Action.READ, qualifier="rates"))],
)
async def delete_rate(
    contract_id: uuid.UUID, rate_id: uuid.UUID, _principal: FinanceWriter, session: Db
) -> Response:
    """Мягкое удаление тарифа; с проводками - 409 equipment.rate_has_postings (БД)."""
    await rates.delete_rate(session, contract_id, rate_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- машинисты ---------------------------------------------------------------------------------


@router.get("/counterparties", response_model=list[CounterpartyRef])
async def list_counterparties(_principal: ContractReader, session: Db) -> list[CounterpartyRef]:
    return await rates.list_counterparties(session)


@router.get("/operators", response_model=list[OperatorOut])
async def list_operators(
    _principal: Reader, session: Db, legal_entity_id: uuid.UUID | None = None
) -> list[OperatorOut]:
    return await rates.list_operators(session, legal_entity_id=legal_entity_id)


@router.post("/operators", status_code=status.HTTP_201_CREATED, response_model=OperatorOut)
async def create_operator(
    body: OperatorCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: Writer,
    session: Db,
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: rates.create_operator(session, body),
    )


# --- заявки ------------------------------------------------------------------------------------


@router.get("/requests", response_model=list[RequestOut])
async def list_requests(
    _principal: Reader,
    session: Db,
    project_id: uuid.UUID | None = None,
    status_filter: Annotated[
        Literal["draft", "submitted", "assigned", "rejected", "cancelled", "closed"] | None,
        Query(alias="status"),
    ] = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> list[RequestOut]:
    return await planning.list_requests(
        session, project_id=project_id, status_filter=status_filter, limit=limit, offset=offset
    )


@router.post("/requests", status_code=status.HTTP_201_CREATED, response_model=RequestOut)
async def create_request(
    body: RequestCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: RequestWriter,
    session: Db,
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: planning.create_request(session, body),
    )


@router.patch("/requests/{request_id}", response_model=RequestOut)
async def patch_request(
    request_id: uuid.UUID, body: RequestPatch, principal: RequestWriter, session: Db
) -> RequestOut:
    return await planning.patch_request(session, request_id, body, principal.user_id)


@router.post("/requests/{request_id}/submit", response_model=RequestOut)
async def submit_request(
    request_id: uuid.UUID, principal: RequestWriter, session: Db
) -> RequestOut:
    return await planning.submit_request(session, request_id, principal.user_id)


@router.post("/requests/{request_id}/reopen", response_model=RequestOut)
async def reopen_request(
    request_id: uuid.UUID, principal: RequestWriter, session: Db
) -> RequestOut:
    return await planning.reopen_request(session, request_id, principal.user_id)


@router.post("/requests/{request_id}/reject", response_model=RequestOut)
async def reject_request(
    request_id: uuid.UUID, body: ReasonIn, _principal: Assigner, session: Db
) -> RequestOut:
    return await planning.reject_request(session, request_id, body.reason)


@router.post("/requests/{request_id}/cancel", response_model=RequestOut)
async def cancel_request(request_id: uuid.UUID, principal: RequestActor, session: Db) -> RequestOut:
    approver = principal.has(Module.EQUIPMENT, Action.APPROVE, "assign")
    return await planning.cancel_request(session, request_id, principal.user_id, approver=approver)


# --- назначения и календарь --------------------------------------------------------------------


@router.get("/assignments", response_model=list[AssignmentOut])
async def list_assignments(
    _principal: Reader,
    session: Db,
    unit_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> list[AssignmentOut]:
    return await planning.list_assignments(
        session,
        unit_id=unit_id,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


@router.post("/assignments", status_code=status.HTTP_201_CREATED, response_model=AssignmentCreated)
async def create_assignment(
    body: AssignmentCreate,
    request: Request,
    key: IdempotencyKeyHeader,
    principal: Assigner,
    session: Db,
) -> Response:
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=body,
        handler=lambda: planning.create_assignment(session, body),
    )


@router.post("/assignments/check", response_model=AssignmentCheck)
async def check_assignment(
    body: AssignmentCreate, _principal: Assigner, session: Db
) -> AssignmentCheck:
    """Проверка назначения без записи: правила те же, что при создании. Код причины - в ответе."""
    return await planning.check_assignment(session, body)


@router.patch("/assignments/{assignment_id}", response_model=AssignmentOut)
async def patch_assignment(
    assignment_id: uuid.UUID, body: AssignmentPatch, _principal: Assigner, session: Db
) -> AssignmentOut:
    return await planning.patch_assignment(session, assignment_id, body)


@router.post("/assignments/{assignment_id}/finish", response_model=AssignmentOut)
async def finish_assignment(
    assignment_id: uuid.UUID, _principal: Assigner, session: Db
) -> AssignmentOut:
    return await planning.finish_assignment(session, assignment_id)


@router.post("/assignments/{assignment_id}/cancel", response_model=AssignmentOut)
async def cancel_assignment(
    assignment_id: uuid.UUID, body: ReasonIn, _principal: Assigner, session: Db
) -> AssignmentOut:
    return await planning.cancel_assignment(session, assignment_id, body.reason)


@router.get("/calendar", response_model=CalendarOut)
async def get_calendar(
    _principal: Reader,
    session: Db,
    date_from: date,
    date_to: date,
    legal_entity_id: uuid.UUID | None = None,
    type_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 1000,
) -> CalendarOut:
    return await planning.calendar(
        session,
        date_from=date_from,
        date_to=date_to,
        legal_entity_id=legal_entity_id,
        type_id=type_id,
        limit=limit,
    )


# --- импорт реестра ----------------------------------------------------------------------------


@router.get("/import/template")
async def import_template(
    _principal: Writer, format_: Annotated[Literal["csv", "xlsx"], Query(alias="format")] = "csv"
) -> Response:
    if format_ == "xlsx":
        return Response(
            content=imports.template_xlsx(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="equipment_units.xlsx"'},
        )
    return Response(
        content="﻿" + imports.template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="equipment_units.csv"'},
    )


@router.post("/import/check", response_model=ImportReport)
async def check_import(
    request: Request, filename: FileName, _principal: Writer, session: Db
) -> ImportReport:
    """Проверка без записи в реестр. Строка журнала пишется: проверка - тоже событие."""
    data = await _body(request)
    file_sha256 = hashlib.sha256(data).hexdigest()
    checked = await imports.check(session, imports.parse(filename, data))
    created = updated = unchanged = 0
    if not checked.issues:
        created, updated, unchanged = await imports.diff_counts(session, checked.records)
    batch_status = "rejected" if checked.issues else "checked"
    batch_id = await imports.journal(
        session,
        file_sha256=file_sha256,
        status_value=batch_status,
        issues=checked.issues,
        counts=(created, updated, unchanged),
    )
    await session.commit()
    return ImportReport(
        batch_id=batch_id,
        status=batch_status,
        rows_total=checked.rows_total,
        created=created,
        updated=updated,
        unchanged=unchanged,
        errors=checked.issues,
    )


@router.post("/import", status_code=status.HTTP_201_CREATED, response_model=ImportReport)
async def commit_import(
    request: Request,
    filename: FileName,
    key: IdempotencyKeyHeader,
    principal: Writer,
    session: Db,
) -> Response:
    """Всё или ничего: при ошибках строк файл не грузится, отчёт по строкам возвращается с 422."""
    data = await _body(request)
    file_sha256 = hashlib.sha256(data).hexdigest()
    checked = await imports.check(session, imports.parse(filename, data))
    if checked.issues:
        await imports.journal(
            session, file_sha256=file_sha256, status_value="rejected", issues=checked.issues
        )
        await session.commit()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=ImportReport(
                batch_id=None,
                status="rejected",
                rows_total=checked.rows_total,
                created=0,
                updated=0,
                unchanged=0,
                errors=checked.issues,
            ).model_dump(mode="json"),
        )
    return await idempotent_create(
        session,
        request=request,
        user_id=principal.user_id,
        key=key,
        payload=ImportPayload(file_sha256=file_sha256),
        handler=lambda: imports.apply(
            session,
            checked.records,
            file_sha256=file_sha256,
            rows_total=checked.rows_total,
            actor=principal.user_id,
        ),
    )


@router.get("/import/batches", response_model=list[BatchOut])
async def list_batches(_principal: Writer, session: Db, limit: Limit = 50) -> list[BatchOut]:
    rows = await session.scalars(
        select(EquipmentImportBatch)
        .order_by(EquipmentImportBatch.created_at.desc(), EquipmentImportBatch.id)
        .limit(limit)
    )
    return [
        BatchOut(
            id=row.id,
            kind=row.kind,
            file_sha256=row.file_sha256,
            status=row.status,
            created_count=row.created_count,
            updated_count=row.updated_count,
            unchanged_count=row.unchanged_count,
            error_count=row.error_count,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get("/import/batches/{batch_id}/errors.csv")
async def batch_errors(batch_id: uuid.UUID, _principal: Writer, session: Db) -> Response:
    batch = await session.scalar(
        select(EquipmentImportBatch).where(EquipmentImportBatch.id == batch_id)
    )
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    issues = [ImportIssue.model_validate(item) for item in batch.errors]
    return Response(
        content="﻿" + imports.issues_csv(issues),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="import-{batch_id}-errors.csv"'},
    )
