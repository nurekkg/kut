"""Позиции версии (ADR-015 часть 4, S6a-1): чтение, замена списка и цен в черновике, импорт из XLSX.

Права на запись проверяют политики и гарды БД; сервис - то же до записи, чтобы отдать понятный код.
Коммит - у вызывающего (run_idempotent): ключ и изменения - одним COMMIT.
Цена держится за номером позиции: правка строки её не трогает, новая позиция цены не имеет.
Импорт без цены в ячейке цену сохраняет. Цены видит только право на деньги (RLS представления).
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import column, func, select, table, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rules import db_rules
from app.modules.auth.principal import Principal
from app.modules.documents import lines_import
from app.modules.documents.models import (
    Document,
    DocumentLine,
    DocumentLinePrice,
    DocumentVersion,
)
from app.modules.documents.schemas import LineOut, LinesIn, LinesOut, PricesIn
from app.modules.documents.types import LINE_KINDS
from app.modules.rbac.enums import Action, Module

# Сумма позиции и цена - представление с security_invoker: RLS цен действует через него.
AMOUNTS = table(
    "document_line_amount",
    column("line_id"),
    column("unit_price"),
    column("amount"),
)


@dataclass(frozen=True, slots=True)
class NewLine:
    position_no: int
    name: str
    unit: str
    quantity: Decimal
    work_id: uuid.UUID | None
    project_node_id: uuid.UUID | None
    unit_price: Decimal | None


@dataclass(frozen=True, slots=True)
class _Draft:
    document_id: uuid.UUID
    version_id: uuid.UUID
    legal_entity_id: uuid.UUID
    project_id: uuid.UUID | None
    kind: str


def _money_allowed(principal: Principal) -> bool:
    """Право на деньги видов с позициями - документное финансовое.
    Виды техники позиций не имеют, поэтому отдельной ветки нет."""
    return principal.has(Module.DOCUMENTS, Action.READ, "financial")


async def _draft(session: AsyncSession, document_id: uuid.UUID, version_id: uuid.UUID) -> _Draft:
    """Версия-черновик вида с позициями. Строка документа блокируется до конца транзакции."""
    row = (
        await session.execute(
            select(
                DocumentVersion.status,
                Document.kind,
                Document.legal_entity_id,
                Document.project_id,
            )
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id == version_id, DocumentVersion.document_id == document_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if row.kind not in LINE_KINDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "document.lines_not_allowed")
    archived = await session.scalar(
        select(Document.archived_at).where(Document.id == document_id).with_for_update()
    )
    if archived is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "document.archived")
    if row.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "document.lines_not_draft")
    return _Draft(document_id, version_id, row.legal_entity_id, row.project_id, row.kind)


async def list_lines(
    session: AsyncSession, document_id: uuid.UUID, version_id: uuid.UUID
) -> LinesOut:
    visible = await session.scalar(
        select(DocumentVersion.id).where(
            DocumentVersion.id == version_id, DocumentVersion.document_id == document_id
        )
    )
    if visible is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    source_id = await session.scalar(select(func.app.version_source(version_id)))
    rows = (
        await session.execute(
            select(
                DocumentLine.position_no,
                DocumentLine.name,
                DocumentLine.unit,
                DocumentLine.quantity,
                DocumentLine.work_id,
                DocumentLine.project_node_id,
                AMOUNTS.c.unit_price,
                AMOUNTS.c.amount,
            )
            .select_from(DocumentLine)
            .outerjoin(AMOUNTS, AMOUNTS.c.line_id == DocumentLine.id)
            .where(
                DocumentLine.document_id == document_id,
                DocumentLine.version_id == source_id,
            )
            .order_by(DocumentLine.position_no)
        )
    ).all()
    lines = [
        LineOut(
            position_no=row.position_no,
            name=row.name,
            unit=row.unit,
            quantity=row.quantity,
            work_id=row.work_id,
            project_node_id=row.project_node_id,
            unit_price=row.unit_price,
            amount=row.amount,
        )
        for row in rows
    ]
    # Итог справочный: сумма позиций, только если цена и сумма видны по каждой позиции.
    total = None
    if lines and all(line.amount is not None for line in lines):
        total = sum((line.amount for line in lines if line.amount is not None), Decimal(0))
    return LinesOut(lines=lines, total=total)


async def _replace(
    session: AsyncSession, principal: Principal, draft: _Draft, new_lines: list[NewLine]
) -> None:
    """Список сводится к нему по номерам. Совпавшая позиция меняет содержимое на месте, её цена
    остаётся.
    Отсутствующая мягко удаляется (с ценой - только с правом на деньги, гард БД); новый номер -
    новая строка.
    Удаления идут раньше вставок, чтобы номер освобождённой позиции не занял новый до её
    удаления."""
    live = (
        await session.execute(
            select(DocumentLine.id, DocumentLine.position_no).where(
                DocumentLine.version_id == draft.version_id, DocumentLine.deleted_at.is_(None)
            )
        )
    ).all()
    id_by_position = {row.position_no: row.id for row in live}
    wanted = {line.position_no for line in new_lines}
    gone = [row_id for number, row_id in id_by_position.items() if number not in wanted]
    if gone:
        await session.execute(
            update(DocumentLine)
            .where(DocumentLine.id.in_(gone))
            .values(deleted_at=func.now(), updated_at=func.now(), updated_by=principal.user_id)
            .execution_options(synchronize_session=False)
        )
    line_ids: dict[int, uuid.UUID] = {}
    for line in new_lines:
        existing = id_by_position.get(line.position_no)
        if existing is None:
            continue
        await session.execute(
            update(DocumentLine)
            .where(DocumentLine.id == existing)
            .values(
                name=line.name,
                unit=line.unit,
                quantity=line.quantity,
                work_id=line.work_id,
                project_node_id=line.project_node_id,
                updated_at=func.now(),
                updated_by=principal.user_id,
            )
            .execution_options(synchronize_session=False)
        )
        line_ids[line.position_no] = existing
    created: dict[int, DocumentLine] = {}
    for line in new_lines:
        if line.position_no in id_by_position:
            continue
        row = DocumentLine(
            document_id=draft.document_id,
            version_id=draft.version_id,
            legal_entity_id=draft.legal_entity_id,
            project_id=draft.project_id,
            position_no=line.position_no,
            name=line.name,
            unit=line.unit,
            quantity=line.quantity,
            work_id=line.work_id,
            project_node_id=line.project_node_id,
        )
        session.add(row)
        created[line.position_no] = row
    await session.flush()
    for number, row in created.items():
        line_ids[number] = row.id
    for line in new_lines:
        if line.unit_price is not None:
            await _write_price(
                session, principal, draft, line_ids[line.position_no], line.unit_price
            )
    await session.flush()


async def _write_price(
    session: AsyncSession,
    principal: Principal,
    draft: _Draft,
    line_id: uuid.UUID,
    price: Decimal | None,
) -> None:
    """Цена позиции: одна строка на позицию. Очистка - мягкое удаление строки (deleted_at), значение
    возвращает строку. Права и черновик проверяет гард БД."""
    existing = await session.scalar(
        select(DocumentLinePrice)
        .where(DocumentLinePrice.line_id == line_id)
        .execution_options(include_deleted=True)
    )
    if price is None:
        if existing is not None and existing.deleted_at is None:
            await session.execute(
                update(DocumentLinePrice)
                .where(DocumentLinePrice.id == existing.id)
                .values(deleted_at=func.now(), updated_at=func.now(), updated_by=principal.user_id)
                .execution_options(synchronize_session=False)
            )
        return
    if existing is None:
        session.add(
            DocumentLinePrice(
                line_id=line_id,
                document_id=draft.document_id,
                version_id=draft.version_id,
                legal_entity_id=draft.legal_entity_id,
                project_id=draft.project_id,
                unit_price=price,
            )
        )
    else:
        existing.unit_price = price
        existing.deleted_at = None


async def replace_lines(
    session: AsyncSession,
    principal: Principal,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    body: LinesIn,
) -> LinesOut:
    draft = await _draft(session, document_id, version_id)
    given = [item.position_no for item in body.lines if item.position_no is not None]
    if len(set(given)) != len(given):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "document.lines_invalid")
    # Новые позиции получают номера выше всех, какие были в версии (в том числе удалённых).
    # Цена держится за строкой позиции, поэтому номер удалённой позиции новой цену не даёт.
    highest = await session.scalar(
        select(func.max(DocumentLine.position_no))
        .where(DocumentLine.version_id == version_id)
        .execution_options(include_deleted=True)
    )
    next_no = max([highest or 0, *given]) + 1
    new_lines: list[NewLine] = []
    for item in body.lines:
        if item.position_no is None:
            number = next_no
            next_no += 1
        else:
            number = item.position_no
        new_lines.append(
            NewLine(
                position_no=number,
                name=item.name,
                unit=item.unit,
                quantity=item.quantity,
                work_id=item.work_id,
                project_node_id=item.project_node_id,
                unit_price=None,
            )
        )
    async with db_rules(session):
        await _replace(session, principal, draft, new_lines)
    return await list_lines(session, document_id, version_id)


async def set_prices(
    session: AsyncSession,
    principal: Principal,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    body: PricesIn,
) -> LinesOut:
    draft = await _draft(session, document_id, version_id)
    # Цены нужны и право на деньги, и право на позиции: иначе UPDATE не найдёт строку
    # (RLS отбирает её молча) и ответ будет необработанной ошибкой вместо 403.
    if not (principal.has(Module.DOCUMENTS, Action.WRITE, "lines") and _money_allowed(principal)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    positions = [item.position_no for item in body.prices]
    if len(set(positions)) != len(positions):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "document.lines_invalid")
    live = (
        await session.execute(
            select(DocumentLine.id, DocumentLine.position_no).where(
                DocumentLine.version_id == version_id, DocumentLine.deleted_at.is_(None)
            )
        )
    ).all()
    line_by_position = {row.position_no: row.id for row in live}
    async with db_rules(session):
        for item in body.prices:
            line_id = line_by_position.get(item.position_no)
            if line_id is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "document.line_unknown")
            await _write_price(session, principal, draft, line_id, item.unit_price)
        await session.flush()
    return await list_lines(session, document_id, version_id)


async def import_lines(
    session: AsyncSession,
    principal: Principal,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    data: bytes,
) -> LinesOut:
    """Импорт XLSX в черновик: список сводится к файлу по номерам. Пустая цена в файле цену не
    меняет.
    Цены из файла - только с правом на деньги. Работа и узел позиции, пришедшей по номеру,
    сохраняются."""
    draft = await _draft(session, document_id, version_id)
    parsed = lines_import.parse_lines(lines_import.read_rows(data))
    if any(line.unit_price is not None for line in parsed) and not _money_allowed(principal):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    live_links = (
        await session.execute(
            select(
                DocumentLine.position_no, DocumentLine.work_id, DocumentLine.project_node_id
            ).where(DocumentLine.version_id == version_id, DocumentLine.deleted_at.is_(None))
        )
    ).all()
    links = {row.position_no: (row.work_id, row.project_node_id) for row in live_links}
    new_lines = [
        NewLine(
            position_no=line.position_no,
            name=line.name,
            unit=line.unit,
            quantity=line.quantity,
            work_id=links.get(line.position_no, (None, None))[0],
            project_node_id=links.get(line.position_no, (None, None))[1],
            unit_price=line.unit_price,
        )
        for line in parsed
    ]
    async with db_rules(session):
        await _replace(session, principal, draft, new_lines)
    return await list_lines(session, document_id, version_id)
