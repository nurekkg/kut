import asyncio
import base64
import io
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status
from openpyxl import Workbook  # type: ignore[import-untyped]
from openpyxl.styles import Font  # type: ignore[import-untyped]
from sqlalchemy import Date, Select, cast, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.rules import db_rules
from app.modules.auth.principal import Principal
from app.modules.documents import files
from app.modules.documents.models import (
    Document,
    DocumentLink,
    DocumentStatusEvent,
    DocumentVersion,
    DocumentVersionAmount,
)
from app.modules.documents.schemas import (
    AmountIn,
    CounterpartyOut,
    DocumentCreate,
    DocumentDetail,
    DocumentOut,
    DownloadOut,
    EventOut,
    LinkOut,
    PresignOut,
    PresignRequest,
    VersionConfirm,
    VersionOut,
)
from app.modules.documents.types import (
    EQUIPMENT_KINDS,
    EXTENSIONS,
    INSPECTION_KIND,
    INVENTORY_KIND,
    KIND_TITLES,
    KINDS,
    MAX_FILE_BYTES,
    MONEY_EQUIPMENT_KINDS,
    STATUS_TITLES,
    STOCK_KINDS,
    Link,
    Scope,
)
from app.modules.objects.models import Project
from app.modules.org.models import Counterparty, LegalEntity
from app.modules.rbac.enums import Action, Module

UPLOAD_TTL_SECONDS = 900
DOWNLOAD_TTL_SECONDS = 120
EXPORT_MAX_ROWS = 5000
EVENTS_LIMIT = 1000
COUNTERPARTIES_LIMIT = 50
LOCAL_ZONE = "Asia/Bishkek"
# Текст, который Excel примет за формулу, выгружается с апострофом (OWASP, инъекция формул).
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
XLSX_HEADERS = (
    "Регистрационный номер",
    "Вид",
    "Название",
    "Объект",
    "Контрагент",
    "Статус последней версии",
    "Дата",
)
# Суммы в сомах (KGS). В интерфейсе - суффикс «сом» (formatMoney), в выгрузке - в заголовке колонки.
XLSX_AMOUNT_HEADERS = ("Текущая сумма, сом (KGS)", "Версия текущей суммы")
VERSION_AMOUNT_HEADERS = (
    "Регистрационный номер",
    "Версия",
    "Статус версии",
    "Сумма версии, сом (KGS)",
)
# Лист выгрузки: название, заголовки, строки, номера денежных колонок (с 1).
Sheet = tuple[str, list[str], list[list[Any]], tuple[int, ...]]
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_EXTENSIONS = "|".join(sorted(set(EXTENSIONS.values())))
_KEY = re.compile(
    rf"(?:project/{_UUID}|legal_entity/{_UUID})/document/{_UUID}/{_UUID}\.(?:{_EXTENSIONS})"
)
_LINK_COLUMN = {
    Link.PROJECT_NODE: "project_node_id",
    Link.WORK: "work_id",
    Link.WORK_ACTUAL: "work_actual_id",
    Link.EQUIPMENT_UNIT: "equipment_unit_id",
}

# Колонки для внутренних ролей и для портала: портал получает только грантованный список
# (миграция 2633d20e6f23), иначе запрос падает с permission denied.
_DOC_INTERNAL = (
    Document.id,
    Document.kind,
    Document.financial,
    Document.title,
    Document.project_id,
    Document.counterparty_id,
    Document.created_at,
    Document.registration_no,
    Document.legal_entity_id,
    Document.contract_id,
    Document.archived_at,
    Document.current_version_id,
)
_DOC_EXTERNAL = (
    Document.id,
    Document.kind,
    Document.financial,
    Document.title,
    Document.project_id,
    Document.counterparty_id,
    Document.created_at,
    Document.current_version_id,
)
_VER_INTERNAL = (
    DocumentVersion.id,
    DocumentVersion.document_id,
    DocumentVersion.version_no,
    DocumentVersion.status,
    DocumentVersion.content_type,
    DocumentVersion.size_bytes,
    DocumentVersion.created_at,
    # Комментарий - по праву на деньги вида (миграция c9e2d4b7a1f6): колонка у kut_app не читается.
    func.app.version_comment(DocumentVersion.id).label("comment"),
    DocumentVersion.checksum_sha256,
)
_VER_EXTERNAL = (
    DocumentVersion.id,
    DocumentVersion.document_id,
    DocumentVersion.version_no,
    DocumentVersion.status,
    DocumentVersion.content_type,
    DocumentVersion.size_bytes,
    DocumentVersion.created_at,
)


@dataclass(frozen=True, slots=True)
class _Doc:
    id: uuid.UUID
    project_id: uuid.UUID | None
    legal_entity_id: uuid.UUID
    kind: str
    financial: bool


def _unprocessable(code: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, code)


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


def _forbidden() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")


def _storage_unavailable() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "storage_unavailable")


def _prefix(document: _Doc) -> str:
    root = (
        f"project/{document.project_id}"
        if document.project_id
        else (f"legal_entity/{document.legal_entity_id}")
    )
    return f"{root}/document/{document.id}/"


def _kind_allowed(principal: Principal, kind: str) -> bool:
    """Право на создание и загрузку вида (PO 2026-10-17, R2): виды техники - по праву техники,
    технический осмотр - ещё и по обслуживанию; остальные - по documents:write."""
    if kind in EQUIPMENT_KINDS:
        if principal.has(Module.EQUIPMENT, Action.WRITE, "documents"):
            return True
        return kind == INSPECTION_KIND and principal.has(
            Module.EQUIPMENT, Action.WRITE, "maintenance"
        )
    if kind == "material_certificate" and principal.has(Module.MATERIALS, Action.WRITE, "batch"):
        return True
    if kind == INVENTORY_KIND:
        # Акт пересчёта ведёт только кладовщик (ADR-016 часть 5): документного доступа мало.
        return principal.has(Module.MATERIALS, Action.WRITE, "stock")
    if kind in STOCK_KINDS and principal.has(Module.MATERIALS, Action.WRITE, "stock"):
        return True
    return principal.has(Module.DOCUMENTS, Action.WRITE)


def _money_allowed(principal: Principal, kind: str) -> bool:
    """Право на сумму вида (R1, R3): денежные виды техники - по equipment:read:rates, прочие
    финансовые - по documents:read:financial. Проверку повторяет БД (политика версии и гард)."""
    if kind in MONEY_EQUIPMENT_KINDS:
        return principal.has(Module.EQUIPMENT, Action.READ, "rates")
    return principal.has(Module.DOCUMENTS, Action.READ, "financial")


def _version_out(row: Any, internal: bool, names: dict[uuid.UUID, str] | None = None) -> VersionOut:
    data = row._asdict()
    if not internal:
        data = {**data, "comment": None, "checksum_sha256": None}
    else:
        author_id = data.pop("author_id", None)
        data["author_name"] = names.get(author_id) if names and author_id else None
    return VersionOut.model_validate(data)


async def document_names(
    session: AsyncSession, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Имена авторов и решивших по документам: только внутренним, видящим документ (см. БД)."""
    rows = (
        await session.execute(
            text(
                "SELECT o_user_id, o_full_name FROM "
                "app.document_person_names(CAST(:docs AS uuid[]))"
            ),
            {"docs": [str(item) for item in document_ids]},
        )
    ).all()
    return {row.o_user_id: row.o_full_name for row in rows}


def _versions(internal: bool) -> Select[Any]:
    """Версии с суммой версии (RLS отбирает видимые суммы) и с автором - только внутренним."""
    amount = DocumentVersionAmount.amount.label("amount")
    source = func.app.version_source(DocumentVersion.id)
    source_no = func.app.version_source_no(DocumentVersion.id).label("source_no")
    if not internal:
        return select(*_VER_EXTERNAL, amount, source_no).outerjoin(
            DocumentVersionAmount, DocumentVersionAmount.version_id == source
        )
    return select(
        *_VER_INTERNAL, DocumentVersion.created_by.label("author_id"), amount, source_no
    ).outerjoin(DocumentVersionAmount, DocumentVersionAmount.version_id == source)


def _latest(column: Any) -> Any:
    """Последняя видимая версия документа: версии отбирает RLS вызывающего."""
    return (
        select(column)
        .where(DocumentVersion.document_id == Document.id)
        .order_by(DocumentVersion.version_no.desc())
        .limit(1)
        .correlate(Document)
        .scalar_subquery()
    )


def _local_date() -> Any:
    return cast(func.timezone(LOCAL_ZONE, Document.created_at), Date)


async def _writable(session: AsyncSession, document_id: uuid.UUID) -> _Doc:
    row = (
        await session.execute(
            select(
                Document.id,
                Document.project_id,
                Document.legal_entity_id,
                Document.kind,
                Document.financial,
                Document.archived_at,
            ).where(Document.id == document_id)
        )
    ).one_or_none()
    if row is None:
        raise _not_found()
    if not await session.scalar(text("SELECT app.document_writable(:id)"), {"id": document_id}):
        raise _forbidden()
    if row.archived_at is not None:
        # Снятый с действия документ новых версий не принимает (ADR-015 часть 1 раздел 3).
        raise HTTPException(status.HTTP_409_CONFLICT, "document.archived")
    return _Doc(row.id, row.project_id, row.legal_entity_id, row.kind, row.financial)


async def _lock_live(session: AsyncSession, document_id: uuid.UUID) -> None:
    """Блокировка строки документа до конца транзакции. Архивация ждёт, статус читается
    после блокировки: архивный документ не принимает версии и суммы, даже в гонке с архивацией."""
    archived = await session.scalar(
        select(Document.archived_at).where(Document.id == document_id).with_for_update()
    )
    if archived is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "document.archived")


async def _assemble(
    session: AsyncSession, principal: Principal, rows: list[Any]
) -> list[DocumentOut]:
    """Документы с версиями, текущей суммой, связями и названием юрлица: по одному запросу на вид
    данных."""
    internal = principal.is_internal
    ids = [row.id for row in rows]
    if not ids:
        return []
    versions_by_document: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for version in await session.execute(
        _versions(internal)
        .where(DocumentVersion.document_id.in_(ids))
        .order_by(DocumentVersion.document_id, DocumentVersion.version_no)
    ):
        versions_by_document[version.document_id].append(version)
    names = await document_names(session, ids) if internal else {}
    links: dict[uuid.UUID, list[LinkOut]] = defaultdict(list)
    legal_names: dict[uuid.UUID, str] = {}
    if internal:
        for link in (
            await session.execute(select(DocumentLink).where(DocumentLink.document_id.in_(ids)))
        ).scalars():
            for kind, column in _LINK_COLUMN.items():
                target = getattr(link, column)
                if target is not None:
                    links[link.document_id].append(LinkOut(kind=kind.value, id=target))
        legal_ids = {row.legal_entity_id for row in rows}
        legal_names = {
            legal.id: legal.name
            for legal in await session.execute(
                select(LegalEntity.id, LegalEntity.name).where(LegalEntity.id.in_(legal_ids))
            )
        }
    result = []
    for row in rows:
        data = row._asdict()
        if not internal:
            data = {
                **data,
                "registration_no": None,
                "legal_entity_id": None,
                "contract_id": None,
                "archived_at": None,
            }
        versions = versions_by_document.get(row.id, [])
        # Действующая версия - последняя утверждённая или подписанная (R1). Текущая сумма - её
        # сумма, иначе сумма последней видимой версии.
        effective = [version for version in versions if version.status in ("approved", "signed")]
        effective_row = effective[-1] if effective else None
        current_row = effective_row or (versions[-1] if versions else None)
        current_version = next(
            (
                _version_out(version, internal, names)
                for version in versions
                if version.id == row.current_version_id
            ),
            None,
        )
        result.append(
            DocumentOut.model_validate(
                {
                    **data,
                    "legal_entity_name": legal_names.get(row.legal_entity_id) if internal else None,
                    "current_version": current_version,
                    "amount": current_row.amount if current_row is not None else None,
                    "amount_version_no": (
                        current_row.source_no
                        if current_row is not None and current_row.amount is not None
                        else None
                    ),
                    "effective_version_no": (
                        effective_row.version_no if effective_row is not None else None
                    ),
                    "links": links.get(row.id, []),
                }
            )
        )
    return result


@dataclass(frozen=True, slots=True)
class RegistryFilters:
    project_id: uuid.UUID | None = None
    kind: str | None = None
    status: str | None = None
    counterparty_id: uuid.UUID | None = None
    date_from: date | None = None
    date_to: date | None = None
    q: str | None = None
    equipment_unit_id: uuid.UUID | None = None
    project_node_id: uuid.UUID | None = None


def _registry(principal: Principal) -> Select[Any]:
    """Одна выборка для реестра и для выгрузки: колонки, связи и видимость те же."""
    columns = _DOC_INTERNAL if principal.is_internal else _DOC_EXTERNAL
    return (
        select(
            *columns,
            Counterparty.name.label("counterparty_name"),
            _latest(DocumentVersion.version_no).label("latest_version_no"),
            _latest(DocumentVersion.status).label("latest_status"),
            Project.name.label("project_name"),
            _local_date().label("local_date"),
        )
        .outerjoin(Counterparty, Counterparty.id == Document.counterparty_id)
        .outerjoin(Project, Project.id == Document.project_id)
    )


def _filtered(statement: Select[Any], filters: RegistryFilters, *, internal: bool) -> Select[Any]:
    if filters.project_id is not None:
        statement = statement.where(Document.project_id == filters.project_id)
    if filters.kind is not None:
        statement = statement.where(Document.kind == filters.kind)
    if filters.counterparty_id is not None:
        statement = statement.where(Document.counterparty_id == filters.counterparty_id)
    if filters.status is not None:
        statement = statement.where(_latest(DocumentVersion.status) == filters.status)
    if filters.date_from is not None:
        statement = statement.where(_local_date() >= filters.date_from)
    if filters.date_to is not None:
        statement = statement.where(_local_date() <= filters.date_to)
    if filters.q:
        # Внешним регистрационный номер не выдан (колоночный грант портала): ищут по названию.
        column = Document.registration_no if internal else Document.title
        pattern = filters.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(column.ilike(f"%{pattern}%", escape="\\"))
    if filters.equipment_unit_id is not None:
        linked = select(DocumentLink.document_id).where(
            DocumentLink.equipment_unit_id == filters.equipment_unit_id
        )
        statement = statement.where(Document.id.in_(linked))
    if filters.project_node_id is not None:
        linked = select(DocumentLink.document_id).where(
            DocumentLink.project_node_id == filters.project_node_id
        )
        statement = statement.where(Document.id.in_(linked))
    return statement


async def list_documents(
    session: AsyncSession,
    principal: Principal,
    filters: RegistryFilters,
    *,
    limit: int,
    offset: int,
) -> list[DocumentOut]:
    if not principal.is_internal and (
        filters.equipment_unit_id is not None or filters.project_node_id is not None
    ):
        # Связи документов внешним не отдаются (ADR часть 2 раздел 7.5): фильтр по связям - пусто.
        return []
    statement = _filtered(_registry(principal), filters, internal=principal.is_internal)
    rows = (
        await session.execute(
            statement.order_by(Document.created_at.desc(), Document.id).limit(limit).offset(offset)
        )
    ).all()
    return await _assemble(session, principal, list(rows))


async def get_document(
    session: AsyncSession, principal: Principal, document_id: uuid.UUID
) -> DocumentDetail:
    row = (
        await session.execute(_registry(principal).where(Document.id == document_id))
    ).one_or_none()
    if row is None:
        raise _not_found()
    [document] = await _assemble(session, principal, [row])
    names = await document_names(session, [document_id]) if principal.is_internal else {}
    versions = [
        _version_out(version, principal.is_internal, names)
        for version in await session.execute(
            _versions(principal.is_internal)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_no)
        )
    ]
    return DocumentDetail.model_validate({**document.model_dump(), "versions": versions})


async def list_counterparties(session: AsyncSession, q: str | None) -> list[CounterpartyOut]:
    """Контрагенты для выбора в документах: только id и название; видимость - RLS справочника."""
    statement = select(Counterparty.id, Counterparty.name).order_by(Counterparty.name)
    if q:
        pattern = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(Counterparty.name.ilike(f"%{pattern}%", escape="\\"))
    rows = (await session.execute(statement.limit(COUNTERPARTIES_LIMIT))).all()
    return [CounterpartyOut(id=row.id, name=row.name) for row in rows]


async def list_events(session: AsyncSession, document_id: uuid.UUID) -> list[EventOut]:
    """Журнал переходов статуса по всем версиям документа (только внутренним, см. роутер)."""
    if await session.scalar(select(Document.id).where(Document.id == document_id)) is None:
        raise _not_found()
    # Лимит отрезает самые старые строки только при выборке по убыванию: новые события не теряются.
    rows = (
        await session.execute(
            select(
                DocumentVersion.version_no,
                DocumentStatusEvent.id,
                DocumentStatusEvent.from_status,
                DocumentStatusEvent.to_status,
                func.app.event_comment(DocumentStatusEvent.id).label("comment"),
                DocumentStatusEvent.created_at,
                DocumentStatusEvent.actor_id,
            )
            .join(DocumentVersion, DocumentVersion.id == DocumentStatusEvent.document_version_id)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentStatusEvent.created_at.desc(), DocumentStatusEvent.id.desc())
            .limit(EVENTS_LIMIT)
        )
    ).all()
    names = await document_names(session, [document_id])
    return [
        EventOut.model_validate({**row._asdict(), "actor_name": names.get(row.actor_id)})
        for row in reversed(rows)
    ]


# Символы, которые XLSX не принимает: openpyxl на них бросает IllegalCharacterError (выгрузка 500).
ILLEGAL_XLSX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _cell(value: Any) -> Any:
    if isinstance(value, str):
        value = ILLEGAL_XLSX.sub("", value)
        if value.startswith(FORMULA_PREFIXES):
            return f"'{value}"
    return value


def _workbook(sheets: list[Sheet]) -> bytes:
    """Сборка книги синхронная и тяжёлая: вызывается в потоке, не в цикле событий. Денежные колонки
    (номера с 1) получают формат с двумя знаками."""
    book = Workbook()
    book.remove(book.active)
    for title, headers, table, money in sheets:
        sheet = book.create_sheet(title)
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        for values in table:
            sheet.append(values)
        for column in money:
            for (cell,) in sheet.iter_rows(min_row=2, min_col=column, max_col=column):
                cell.number_format = "#,##0.00"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def _version_table(session: AsyncSession, rows: list[Any]) -> list[list[Any]]:
    """Суммы по версиям выгрузки: строка на видимую версию, в порядке реестра."""
    versions: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for version in await session.execute(
        _versions(True)
        .where(DocumentVersion.document_id.in_([row.id for row in rows]))
        .order_by(DocumentVersion.version_no)
    ):
        versions[version.document_id].append(version)
    return [
        [
            _cell(value)
            for value in (
                row.registration_no,
                version.version_no,
                STATUS_TITLES.get(version.status, ""),
                float(version.amount) if version.amount is not None else None,
            )
        ]
        for row in rows
        for version in versions.get(row.id, [])
    ]


async def export_xlsx(
    session: AsyncSession, principal: Principal, filters: RegistryFilters
) -> bytes:
    """Выгрузка реестра: те же фильтры и выборка, что у реестра. Суммы - только с правом
    documents:read:financial: без него нет ни колонок, ни листа сумм версий."""
    statement = (
        _filtered(_registry(principal), filters, internal=principal.is_internal)
        .order_by(Document.created_at.desc(), Document.id)
        .limit(EXPORT_MAX_ROWS + 1)
    )
    rows = list((await session.execute(statement)).all())
    if len(rows) > EXPORT_MAX_ROWS:
        raise _unprocessable("document.export_too_large")
    documents = await _assemble(session, principal, rows)
    with_amount = principal.has(Module.DOCUMENTS, Action.READ, "financial") or principal.has(
        Module.EQUIPMENT, Action.READ, "rates"
    )
    headers = [*XLSX_HEADERS, *(XLSX_AMOUNT_HEADERS if with_amount else ())]
    table: list[list[Any]] = []
    for row, document in zip(rows, documents, strict=True):
        values: list[Any] = [
            document.registration_no,
            KIND_TITLES[document.kind],
            document.title,
            row.project_name,
            document.counterparty_name,
            STATUS_TITLES.get(document.latest_status or "", ""),
            row.local_date,
        ]
        if with_amount:
            values.append(float(document.amount) if document.amount is not None else None)
            values.append(document.amount_version_no)
        table.append([_cell(value) for value in values])
    money = (len(XLSX_HEADERS) + 1,) if with_amount else ()
    sheets: list[Sheet] = [("Документы", headers, table, money)]
    if with_amount:
        sheets.append(
            (
                "Суммы версий",
                list(VERSION_AMOUNT_HEADERS),
                await _version_table(session, rows),
                (len(VERSION_AMOUNT_HEADERS),),
            )
        )
    return await asyncio.to_thread(_workbook, sheets)


async def create_document(
    session: AsyncSession, principal: Principal, body: DocumentCreate
) -> DocumentDetail:
    """Черновик документа: номер берётся счётчиком в той же транзакции (откат не оставляет дыр)."""
    spec = KINDS.get(body.kind)
    if spec is None:
        raise _unprocessable("document.kind_invalid")
    if not _kind_allowed(principal, body.kind):
        raise _forbidden()
    if spec.scope is Scope.OBJECT and body.project_id is None:
        raise _unprocessable("document.scope_invalid")
    if body.kind in STOCK_KINDS and body.project_id is None:
        raise _unprocessable("document.scope_invalid")
    if spec.scope is Scope.LEGAL_ENTITY and body.project_id is not None:
        raise _unprocessable("document.scope_invalid")
    if spec.counterparty and body.counterparty_id is None:
        raise _unprocessable("document.counterparty_required")
    if spec.contract and body.contract_id is None:
        raise _unprocessable("document.contract_required")
    given = {link.kind for link in body.links}
    if not {link.value for link in spec.links} <= given:
        raise _unprocessable("document.link_required")
    legal = (
        await session.execute(
            select(LegalEntity.id, LegalEntity.code).where(LegalEntity.id == body.legal_entity_id)
        )
    ).one_or_none()
    if legal is None:
        raise _not_found()
    if legal.code is None:
        raise _unprocessable("document.legal_entity_code_missing")
    # Отказ БД (право на вид, юрлицо) переводим в 403, а не в 500: счётчик номера - внутри db_rules.
    async with db_rules(session):
        year = await session.scalar(text("SELECT EXTRACT(YEAR FROM app.local_today())::int"))
        seq = await session.scalar(
            text("SELECT app.next_document_number(:legal, :kind, :year)"),
            {"legal": body.legal_entity_id, "kind": body.kind, "year": year},
        )
    document = Document(
        legal_entity_id=body.legal_entity_id,
        project_id=body.project_id,
        counterparty_id=body.counterparty_id,
        contract_id=body.contract_id,
        kind=body.kind,
        financial=spec.financial,
        number_year=year,
        number_seq=seq,
        registration_no=f"{spec.prefix}-{legal.code}-{year}-{seq:04d}",
        title=body.title,
    )
    session.add(document)
    async with db_rules(session):
        await session.flush()
        for link in body.links:
            session.add(
                DocumentLink(
                    document_id=document.id,
                    legal_entity_id=body.legal_entity_id,
                    **{_LINK_COLUMN[Link(link.kind)]: link.id},
                )
            )
        if not spec.file_required:
            version_no = await session.scalar(
                text("SELECT app.document_next_version(:document)"), {"document": document.id}
            )
            session.add(
                DocumentVersion(
                    document_id=document.id,
                    legal_entity_id=document.legal_entity_id,
                    project_id=document.project_id,
                    version_no=version_no,
                    status="draft",
                )
            )
        await session.flush()
    return await get_document(session, principal, document.id)


def make_key(document: _Doc, content_type: str) -> str:
    """Ключ задаёт сервер: клиент не выбирает, куда в хранилище попадёт файл."""
    return f"{_prefix(document)}{uuid.uuid4()}.{EXTENSIONS[content_type]}"


async def presign(
    session: AsyncSession, document_id: uuid.UUID, body: PresignRequest
) -> PresignOut:
    document = await _writable(session, document_id)
    if body.content_type not in EXTENSIONS:
        raise _unprocessable("document.type_not_allowed")
    key = make_key(document, body.content_type)
    url = storage.presign_put(
        key, body.content_type, body.size_bytes, UPLOAD_TTL_SECONDS, body.checksum_sha256
    )
    headers = {
        "Content-Type": body.content_type,
        "x-amz-checksum-sha256": body.checksum_sha256,
        **storage.WRITE_ONCE_HEADERS,
    }
    return PresignOut(key=key, upload_url=url, headers=headers, expires_in=UPLOAD_TTL_SECONDS)


async def stored_upload(document: _Doc, key: str) -> Any:
    """Файл, загруженный по ключу документа: тип, размер и хэш - из S3, содержимое - files.inspect.
    Общий путь черновика версии и подписанного скана (S6a-2)."""
    if not key.startswith(_prefix(document)) or not _KEY.fullmatch(key):
        raise _unprocessable("document.key_invalid")
    try:
        stored = await storage.head_object(key)
    except (ClientError, BotoCoreError) as error:
        raise _storage_unavailable() from error
    if stored is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "document.not_uploaded")
    if stored.content_type not in EXTENSIONS:
        raise _unprocessable("document.type_not_allowed")
    if not key.endswith(f".{EXTENSIONS[stored.content_type]}"):
        raise _unprocessable("document.type_mismatch")
    if not 0 < stored.size <= MAX_FILE_BYTES:
        raise _unprocessable("document.too_large")
    if stored.checksum_sha256 is None:
        raise _unprocessable("document.checksum_missing")
    await files.inspect(key, stored.size, stored.content_type)
    return stored


async def confirm(
    session: AsyncSession, principal: Principal, document_id: uuid.UUID, body: VersionConfirm
) -> VersionOut:
    """Без COMMIT: вызывается внутри идемпотентной транзакции. Тип, размер и хэш берутся из S3,
    а не из слов клиента; содержимое проверяет files.inspect до записи версии. Сумма версии пишется
    в той же транзакции (R1): новая сумма - новая версия, гард БД пускает её только в черновике."""
    document = await _writable(session, document_id)
    if not body.key.startswith(_prefix(document)) or not _KEY.fullmatch(body.key):
        raise _unprocessable("document.key_invalid")
    if document.kind == INVENTORY_KIND:
        # Корректировки утверждённой или согласуемой версии опираются на её снимок: новая версия
        # сторнировала бы их и пересчитала расхождение заново. Поэтому её не принимаем.
        locked = await session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM document_version WHERE document_id = :d"
                " AND status IN ('on_approval', 'approved', 'signed'))"
            ),
            {"d": document_id},
        )
        if locked:
            raise HTTPException(status.HTTP_409_CONFLICT, "inventory.version_locked")
    if body.amount is not None:
        if not document.financial:
            raise _unprocessable("document.amount_not_financial")
        if not _money_allowed(principal, document.kind):
            raise _forbidden()
    stored = await stored_upload(document, body.key)
    await _lock_live(session, document_id)
    version_no = await session.scalar(
        text("SELECT app.document_next_version(:document)"), {"document": document_id}
    )
    version = DocumentVersion(
        document_id=document_id,
        legal_entity_id=document.legal_entity_id,
        project_id=document.project_id,
        version_no=version_no,
        s3_key=body.key,
        content_type=stored.content_type,
        size_bytes=stored.size,
        checksum_sha256=base64.b64decode(stored.checksum_sha256).hex(),
        comment=body.comment,
        status="draft",
    )
    session.add(version)
    async with db_rules(session):
        await session.flush()
        if body.amount is not None:
            session.add(
                DocumentVersionAmount(
                    version_id=version.id,
                    document_id=document_id,
                    legal_entity_id=document.legal_entity_id,
                    project_id=document.project_id,
                    amount=body.amount,
                )
            )
            await session.flush()
    names = await document_names(session, [version.document_id])
    return _version_out(
        (await session.execute(_versions(True).where(DocumentVersion.id == version.id))).one(),
        internal=True,
        names=names,
    )


async def set_version_amount(
    session: AsyncSession,
    principal: Principal,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    body: AmountIn,
) -> None:
    """Сумма черновой версии (R1): правка и первая запись. После выхода версии из черновика гард
    БД даёт ошибку (409 document.version_not_draft); право на сумму проверяет RLS (чужая строка -
    отказ)."""
    version = (
        await session.execute(
            select(
                DocumentVersion.id,
                DocumentVersion.legal_entity_id,
                DocumentVersion.project_id,
                Document.archived_at,
            )
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id == version_id, DocumentVersion.document_id == document_id)
        )
    ).one_or_none()
    if version is None:
        raise _not_found()
    if version.archived_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "document.archived")
    async with db_rules(session):
        await _lock_live(session, document_id)
        existing = await session.scalar(
            select(DocumentVersionAmount.id).where(DocumentVersionAmount.version_id == version_id)
        )
        if existing is None:
            session.add(
                DocumentVersionAmount(
                    version_id=version_id,
                    document_id=document_id,
                    legal_entity_id=version.legal_entity_id,
                    project_id=version.project_id,
                    amount=body.amount,
                )
            )
            await session.flush()
        else:
            updated = await session.scalar(
                update(DocumentVersionAmount)
                .where(DocumentVersionAmount.version_id == version_id)
                .values(amount=body.amount, updated_by=principal.user_id, updated_at=func.now())
                .returning(DocumentVersionAmount.id)
                .execution_options(synchronize_session=False)
            )
            if updated is None:
                raise _forbidden()
    await session.commit()


async def download_url(
    session: AsyncSession, document_id: uuid.UUID, version_id: uuid.UUID
) -> DownloadOut:
    """Ссылка живёт 120 с. Видимость версии и право на файл вида (денежные виды техники - по
    тарифам, остальные финансовые - по documents:read:financial) проверяет БД."""
    row = (
        await session.execute(
            select(DocumentVersion.content_type, DocumentVersion.version_no, Document.kind)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id == version_id, DocumentVersion.document_id == document_id)
        )
    ).one_or_none()
    if row is None or row.content_type is None:
        raise _not_found()
    key = await session.scalar(
        text("SELECT app.document_version_storage_key(:version)"), {"version": version_id}
    )
    if key is None:
        raise _forbidden()
    filename = f"{row.kind}-v{row.version_no}.{EXTENSIONS[row.content_type]}"
    url = storage.presign_download(key, row.content_type, filename, DOWNLOAD_TTL_SECONDS)
    return DownloadOut(url=url, expires_in=DOWNLOAD_TTL_SECONDS)


async def archive(session: AsyncSession, principal: Principal, document_id: uuid.UUID) -> None:
    """Снятие с действия: архивная дата. Документ и версии не удаляются (ТЗ §5.4).
    Вид техники снимает с действия только право записи в документы техники (R2)."""
    kind = await session.scalar(select(Document.kind).where(Document.id == document_id))
    if kind is None:
        raise _not_found()
    if kind in EQUIPMENT_KINDS and not principal.has(Module.EQUIPMENT, Action.WRITE, "documents"):
        raise _forbidden()
    async with db_rules(session):
        await session.execute(
            text("SELECT app.document_archive(:document)"), {"document": document_id}
        )
    await session.commit()
