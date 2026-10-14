import base64
import io
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
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
    DocumentAmount,
    DocumentLink,
    DocumentStatusEvent,
    DocumentVersion,
)
from app.modules.documents.schemas import (
    AmountIn,
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
    EXTENSIONS,
    KIND_TITLES,
    KINDS,
    MAX_FILE_BYTES,
    STATUS_TITLES,
    Link,
    Scope,
)
from app.modules.identity.models import User
from app.modules.objects.models import Project
from app.modules.org.models import Counterparty, LegalEntity
from app.modules.rbac.enums import Action, Module

UPLOAD_TTL_SECONDS = 900
DOWNLOAD_TTL_SECONDS = 120
EXPORT_MAX_ROWS = 5000
EVENTS_LIMIT = 1000
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
    DocumentVersion.comment,
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


def _version_out(row: Any, internal: bool) -> VersionOut:
    data = row._asdict()
    if not internal:
        data = {**data, "comment": None, "checksum_sha256": None}
    return VersionOut.model_validate(data)


def _versions(internal: bool) -> Select[Any]:
    """Версии с автором: имя загрузившего видно только внутренним ролям."""
    if not internal:
        return select(*_VER_EXTERNAL)
    return select(*_VER_INTERNAL, User.full_name.label("author_name")).outerjoin(
        User, User.id == DocumentVersion.created_by
    )


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
    return _Doc(row.id, row.project_id, row.legal_entity_id)


async def _assemble(
    session: AsyncSession, principal: Principal, rows: list[Any]
) -> list[DocumentOut]:
    """Документы с текущей версией, суммой и связями: по одному запросу на каждый вид данных."""
    internal = principal.is_internal
    ids = [row.id for row in rows]
    if not ids:
        return []
    current_ids = [row.current_version_id for row in rows if row.current_version_id]
    current: dict[uuid.UUID, VersionOut] = {}
    if current_ids:
        for version in await session.execute(
            _versions(internal).where(DocumentVersion.id.in_(current_ids))
        ):
            current[version.id] = _version_out(version, internal)
    amounts: dict[uuid.UUID, Decimal] = {
        row.document_id: row.amount
        for row in await session.execute(
            select(DocumentAmount.document_id, DocumentAmount.amount).where(
                DocumentAmount.document_id.in_(ids)
            )
        )
    }
    links: dict[uuid.UUID, list[LinkOut]] = defaultdict(list)
    if internal:
        for link in (
            await session.execute(select(DocumentLink).where(DocumentLink.document_id.in_(ids)))
        ).scalars():
            for kind, column in _LINK_COLUMN.items():
                target = getattr(link, column)
                if target is not None:
                    links[link.document_id].append(LinkOut(kind=kind.value, id=target))
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
        result.append(
            DocumentOut.model_validate(
                {
                    **data,
                    "current_version": current.get(row.current_version_id)
                    if row.current_version_id
                    else None,
                    "amount": amounts.get(row.id),
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


def _filtered(statement: Select[Any], filters: RegistryFilters) -> Select[Any]:
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
        pattern = filters.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(Document.registration_no.ilike(f"%{pattern}%", escape="\\"))
    if filters.equipment_unit_id is not None:
        linked = select(DocumentLink.document_id).where(
            DocumentLink.equipment_unit_id == filters.equipment_unit_id
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
    if filters.equipment_unit_id is not None and not principal.is_internal:
        # Связи документов внешним не отдаются (ADR часть 2 раздел 7.5): фильтр по технике - пусто.
        return []
    statement = _filtered(_registry(principal), filters)
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
    versions = [
        _version_out(version, principal.is_internal)
        for version in await session.execute(
            _versions(principal.is_internal)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_no)
        )
    ]
    return DocumentDetail.model_validate({**document.model_dump(), "versions": versions})


async def list_events(session: AsyncSession, document_id: uuid.UUID) -> list[EventOut]:
    """Журнал переходов статуса по всем версиям документа (только внутренним, см. роутер)."""
    if await session.scalar(select(Document.id).where(Document.id == document_id)) is None:
        raise _not_found()
    rows = await session.execute(
        select(
            DocumentVersion.version_no,
            DocumentStatusEvent.id,
            DocumentStatusEvent.from_status,
            DocumentStatusEvent.to_status,
            DocumentStatusEvent.comment,
            DocumentStatusEvent.created_at,
            User.full_name.label("actor_name"),
        )
        .join(DocumentVersion, DocumentVersion.id == DocumentStatusEvent.document_version_id)
        .outerjoin(User, User.id == DocumentStatusEvent.actor_id)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentStatusEvent.created_at, DocumentStatusEvent.id)
        .limit(EVENTS_LIMIT)
    )
    return [EventOut.model_validate(row._asdict()) for row in rows]


def _cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return f"'{value}"
    return value


async def export_xlsx(
    session: AsyncSession, principal: Principal, filters: RegistryFilters
) -> bytes:
    """Выгрузка реестра: те же фильтры и выборка, что у реестра. Сумма - только с правом
    documents:read:financial: без него колонки нет в файле."""
    statement = (
        _filtered(_registry(principal), filters)
        .order_by(Document.created_at.desc(), Document.id)
        .limit(EXPORT_MAX_ROWS + 1)
    )
    rows = list((await session.execute(statement)).all())
    if len(rows) > EXPORT_MAX_ROWS:
        raise _unprocessable("document.export_too_large")
    documents = await _assemble(session, principal, rows)
    with_amount = principal.has(Module.DOCUMENTS, Action.READ, "financial")
    book = Workbook()
    sheet = book.active
    sheet.title = "Документы"
    headers = [*XLSX_HEADERS, *(["Сумма"] if with_amount else [])]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
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
        sheet.append([_cell(value) for value in values])
        if with_amount:
            sheet.cell(row=sheet.max_row, column=len(headers)).number_format = "#,##0.00"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def create_document(
    session: AsyncSession, principal: Principal, body: DocumentCreate
) -> DocumentDetail:
    """Черновик документа: номер берётся счётчиком в той же транзакции (откат не оставляет дыр)."""
    spec = KINDS.get(body.kind)
    if spec is None:
        raise _unprocessable("document.kind_invalid")
    if spec.scope is Scope.OBJECT and body.project_id is None:
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
        if body.amount is not None:
            session.add(
                DocumentAmount(
                    document_id=document.id,
                    legal_entity_id=body.legal_entity_id,
                    amount=body.amount,
                )
            )
        await session.flush()
    return await get_document(session, principal, document.id)


async def set_amount(
    session: AsyncSession, principal: Principal, document_id: uuid.UUID, body: AmountIn
) -> None:
    """Сумма документа: право documents:read:financial проверяет RLS (чужая строка - отказ)."""
    row = (
        await session.execute(select(Document.legal_entity_id).where(Document.id == document_id))
    ).one_or_none()
    if row is None:
        raise _not_found()
    async with db_rules(session):
        existing = await session.scalar(
            select(DocumentAmount.id).where(DocumentAmount.document_id == document_id)
        )
        if existing is None:
            session.add(
                DocumentAmount(
                    document_id=document_id,
                    legal_entity_id=row.legal_entity_id,
                    amount=body.amount,
                )
            )
            await session.flush()
        else:
            updated = await session.scalar(
                update(DocumentAmount)
                .where(DocumentAmount.document_id == document_id)
                .values(amount=body.amount, updated_by=principal.user_id, updated_at=func.now())
                .returning(DocumentAmount.id)
                .execution_options(synchronize_session=False)
            )
            if updated is None:
                raise _forbidden()
    await session.commit()


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


async def confirm(
    session: AsyncSession, document_id: uuid.UUID, body: VersionConfirm
) -> VersionOut:
    """Без COMMIT: вызывается внутри идемпотентной транзакции. Тип, размер и хэш берутся из S3,
    а не из слов клиента; содержимое проверяет files.inspect до записи версии."""
    document = await _writable(session, document_id)
    if not body.key.startswith(_prefix(document)) or not _KEY.fullmatch(body.key):
        raise _unprocessable("document.key_invalid")
    try:
        stored = await storage.head_object(body.key)
    except (ClientError, BotoCoreError) as error:
        raise _storage_unavailable() from error
    if stored is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "document.not_uploaded")
    if stored.content_type not in EXTENSIONS:
        raise _unprocessable("document.type_not_allowed")
    if not body.key.endswith(f".{EXTENSIONS[stored.content_type]}"):
        raise _unprocessable("document.type_mismatch")
    if not 0 < stored.size <= MAX_FILE_BYTES:
        raise _unprocessable("document.too_large")
    if stored.checksum_sha256 is None:
        raise _unprocessable("document.checksum_missing")
    await files.inspect(body.key, stored.size, stored.content_type)
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
    return _version_out(
        (await session.execute(_versions(True).where(DocumentVersion.id == version.id))).one(),
        internal=True,
    )


async def download_url(
    session: AsyncSession, document_id: uuid.UUID, version_id: uuid.UUID
) -> DownloadOut:
    """Ссылка живёт 120 с. Видимость версии и право на сумму (финансовый вид) проверяет БД."""
    row = (
        await session.execute(
            select(DocumentVersion.content_type, DocumentVersion.version_no, Document.kind)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id == version_id, DocumentVersion.document_id == document_id)
        )
    ).one_or_none()
    if row is None:
        raise _not_found()
    key = await session.scalar(
        text("SELECT app.document_version_storage_key(:version)"), {"version": version_id}
    )
    if key is None:
        raise _forbidden()
    filename = f"{row.kind}-v{row.version_no}.{EXTENSIONS[row.content_type]}"
    url = storage.presign_download(key, row.content_type, filename, DOWNLOAD_TTL_SECONDS)
    return DownloadOut(url=url, expires_in=DOWNLOAD_TTL_SECONDS)


async def archive(session: AsyncSession, document_id: uuid.UUID) -> None:
    """Снятие с действия: архивная дата. Документ и версии не удаляются (ТЗ §5.4)."""
    async with db_rules(session):
        await session.execute(
            text("SELECT app.document_archive(:document)"), {"document": document_id}
        )
    await session.commit()
