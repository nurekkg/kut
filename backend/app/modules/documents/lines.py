"""Позиции версии (ADR-015 часть 4, S6a-1): чтение, замена списка и цен в черновике, импорт из XLSX.

Права на запись проверяют политики и гарды БД; сервис - то же до записи, чтобы отдать понятный код.
Коммит - у вызывающего (run_idempotent): ключ и изменения - одним COMMIT.
Цена держится за номером позиции: правка строки её не трогает, новая позиция цены не имеет.
Импорт без цены в ячейке цену сохраняет. Цены видит только право на деньги (RLS представления).
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, case, column, exists, func, null, select, table, update
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
from app.modules.documents.schemas import LineIn, LineOut, LinesIn, LinesOut, PricesIn
from app.modules.documents.types import INVENTORY_KIND, LINE_KINDS
from app.modules.materials.models import Material, MaterialBatch, MaterialPrice
from app.modules.rbac.enums import Action, Module
from app.modules.stock.models import StockMovement, Warehouse

# Сумма позиции и цена - представление с security_invoker: RLS цен действует через него.
AMOUNTS = table(
    "document_line_amount",
    column("line_id"),
    column("unit_price"),
    column("amount"),
)


def _kept(links: dict[int, Any], number: int, field: str) -> uuid.UUID | None:
    """Связь позиции, сохранённая в черновике: переносится при перестройке списка."""
    row = links.get(number)
    return getattr(row, field) if row is not None else None


def _keep(
    value: uuid.UUID | None, links: dict[int, Any], number: int, field: str
) -> uuid.UUID | None:
    """Значение из запроса; если его нет, связь берётся у позиции с тем же номером."""
    return value if value is not None else _kept(links, number, field)


def _carry(links: dict[int, Any], line: Any) -> dict[int, Any]:
    """Связи переносятся, если позиция с тем же номером имеет то же имя и единицу."""
    row = links.get(line.position_no)
    if row is not None and row.name == line.name and row.unit == line.unit:
        return {line.position_no: row}
    return {}


@dataclass(frozen=True, slots=True)
class NewLine:
    position_no: int
    name: str
    unit: str
    quantity: Decimal | None
    work_id: uuid.UUID | None
    project_node_id: uuid.UUID | None
    unit_price: Decimal | None
    material_id: uuid.UUID | None = None
    material_batch_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    responsible_user_id: uuid.UUID | None = None


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


# Склад и ответственный - только для внутренних: внешним роли портала читают лишь PORTAL_LINE.
INTERNAL_LINE_COLUMNS = (
    DocumentLine.material_id,
    DocumentLine.material_batch_id,
    DocumentLine.warehouse_id,
    DocumentLine.responsible_user_id,
)
# Снимок и остаток склада - только с правом на материалы (RLS движений требует того же права).
STOCK_STATE_COLUMNS = (
    DocumentLine.snapshot_qty,
    # Остаток склада сейчас (для акта пересчёта); без склада - null.
    case(
        (DocumentLine.warehouse_id.is_(None), null()),
        else_=func.app.stock_balance(
            DocumentLine.warehouse_id, DocumentLine.material_id, DocumentLine.material_batch_id
        ),
    ).label("balance_now"),
)


def _line_out(row: Any) -> LineOut:
    """Позиция для ответа. Расхождение и стоимость считает сервис: снимок и остаток - внутренним.
    Цена плана приходит только с правом на закупочные цены (RLS); без него стоимости нет."""
    snapshot = getattr(row, "snapshot_qty", None)
    discrepancy = None
    if row.quantity is not None and snapshot is not None:
        discrepancy = row.quantity - snapshot
    plan_price = getattr(row, "plan_price", None)
    value = None
    if discrepancy is not None and plan_price is not None:
        value = discrepancy * plan_price
    return LineOut(
        position_no=row.position_no,
        name=row.name,
        unit=row.unit,
        quantity=row.quantity,
        work_id=row.work_id,
        project_node_id=row.project_node_id,
        material_id=getattr(row, "material_id", None),
        material_batch_id=getattr(row, "material_batch_id", None),
        warehouse_id=getattr(row, "warehouse_id", None),
        responsible_user_id=getattr(row, "responsible_user_id", None),
        unit_price=row.unit_price,
        amount=row.amount,
        snapshot_qty=snapshot,
        balance_now=getattr(row, "balance_now", None),
        discrepancy=discrepancy,
        plan_price=plan_price,
        value=value,
    )


async def list_lines(
    session: AsyncSession,
    principal: Principal,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
) -> LinesOut:
    visible = await session.scalar(
        select(DocumentVersion.id).where(
            DocumentVersion.id == version_id, DocumentVersion.document_id == document_id
        )
    )
    if visible is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    source_id = await session.scalar(select(func.app.version_source(version_id)))
    internal = principal.is_internal
    stock_visible = internal and principal.has(Module.MATERIALS, Action.READ)
    statement = (
        select(
            DocumentLine.position_no,
            DocumentLine.name,
            DocumentLine.unit,
            DocumentLine.quantity,
            DocumentLine.work_id,
            DocumentLine.project_node_id,
            *(INTERNAL_LINE_COLUMNS if internal else ()),
            *(STOCK_STATE_COLUMNS if stock_visible else ()),
            *((MaterialPrice.unit_price.label("plan_price"),) if internal else ()),
            AMOUNTS.c.unit_price,
            AMOUNTS.c.amount,
        )
        .select_from(DocumentLine)
        .outerjoin(AMOUNTS, AMOUNTS.c.line_id == DocumentLine.id)
    )
    if internal:
        statement = statement.outerjoin(
            MaterialPrice,
            and_(
                MaterialPrice.material_id == DocumentLine.material_id,
                MaterialPrice.deleted_at.is_(None),
            ),
        )
    rows = (
        await session.execute(
            statement.where(
                DocumentLine.document_id == document_id,
                DocumentLine.version_id == source_id,
            ).order_by(DocumentLine.position_no)
        )
    ).all()
    lines = [_line_out(row) for row in rows]
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
                material_id=line.material_id,
                material_batch_id=line.material_batch_id,
                warehouse_id=line.warehouse_id,
                responsible_user_id=line.responsible_user_id,
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
            material_id=line.material_id,
            material_batch_id=line.material_batch_id,
            warehouse_id=line.warehouse_id,
            responsible_user_id=line.responsible_user_id,
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
    if draft.kind == INVENTORY_KIND:
        # Склад акта - объекта акта и видимый вызывающему; чужой склад отказывает, как при приходе.
        wanted = {item.warehouse_id for item in body.lines if item.warehouse_id is not None}
        if wanted:
            own = set(
                await session.scalars(
                    select(Warehouse.id).where(
                        Warehouse.id.in_(wanted), Warehouse.project_id == draft.project_id
                    )
                )
            )
            if own != wanted:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "stock.warehouse_project"
                )
    live = {
        row.position_no: row
        for row in (
            await session.execute(
                select(
                    DocumentLine.position_no,
                    DocumentLine.name,
                    DocumentLine.unit,
                    DocumentLine.material_id,
                    DocumentLine.material_batch_id,
                    DocumentLine.warehouse_id,
                    DocumentLine.responsible_user_id,
                ).where(DocumentLine.version_id == version_id, DocumentLine.deleted_at.is_(None))
            )
        ).all()
    }
    new_lines: list[NewLine] = []
    for item in body.lines:
        if item.position_no is None:
            number = next_no
            next_no += 1
        else:
            number = item.position_no
        carried = _carry(live, item)
        new_lines.append(
            NewLine(
                position_no=number,
                name=item.name,
                unit=item.unit,
                quantity=item.quantity,
                work_id=item.work_id,
                project_node_id=item.project_node_id,
                material_id=_keep(item.material_id, carried, number, "material_id"),
                material_batch_id=_keep(
                    item.material_batch_id, carried, number, "material_batch_id"
                ),
                warehouse_id=_keep(item.warehouse_id, carried, number, "warehouse_id"),
                responsible_user_id=_keep(
                    item.responsible_user_id, carried, number, "responsible_user_id"
                ),
                unit_price=None,
            )
        )
    async with db_rules(session):
        await _replace(session, principal, draft, new_lines)
    return await list_lines(session, principal, document_id, version_id)


async def prefill_inventory(
    session: AsyncSession,
    principal: Principal,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    warehouse_id: uuid.UUID,
) -> LinesOut:
    """Акт пересчёта: позиции склада с ненулевым учётным остатком (по партиям у сертифицированных).
    Факт пуст - его вводит кладовщик. Заполняет только пустой черновик, факт не трогает."""
    draft = await _draft(session, document_id, version_id)
    if draft.kind != INVENTORY_KIND:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "inventory.not_inventory")
    filled = await session.scalar(
        select(
            exists().where(DocumentLine.version_id == version_id, DocumentLine.deleted_at.is_(None))
        )
    )
    if filled:
        raise HTTPException(status.HTTP_409_CONFLICT, "inventory.already_filled")
    warehouse = await session.scalar(select(Warehouse).where(Warehouse.id == warehouse_id))
    if warehouse is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if warehouse.project_id != draft.project_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "stock.warehouse_project")
    rows = (
        await session.execute(
            select(
                StockMovement.material_id,
                Material.requires_certificate,
                StockMovement.batch_id,
                func.sum(StockMovement.qty).label("qty"),
            )
            .join(Material, Material.id == StockMovement.material_id)
            .where(StockMovement.warehouse_id == warehouse_id)
            .group_by(
                StockMovement.material_id, Material.requires_certificate, StockMovement.batch_id
            )
        )
    ).all()
    balances: dict[tuple[uuid.UUID, uuid.UUID | None], Decimal] = {}
    for row in rows:
        key = (row.material_id, row.batch_id if row.requires_certificate else None)
        balances[key] = balances.get(key, Decimal(0)) + row.qty
    counted = [key for key, qty in balances.items() if qty != 0]
    materials = {
        m.id: m
        for m in await session.scalars(
            select(Material).where(Material.id.in_([key[0] for key in counted]))
        )
    }
    batches = {
        b.id: b
        for b in await session.scalars(
            select(MaterialBatch).where(
                MaterialBatch.id.in_([key[1] for key in counted if key[1] is not None])
            )
        )
    }
    prepared = []
    for material_id, batch_id in counted:
        material = materials[material_id]
        batch_no = batches[batch_id].batch_no if batch_id is not None else None
        name = material.name if batch_no is None else f"{material.name}, партия {batch_no}"
        line = LineIn(
            name=name,
            unit=material.base_unit,
            quantity=None,
            material_id=material_id,
            material_batch_id=batch_id,
            warehouse_id=warehouse_id,
        )
        prepared.append((material.name, batch_no or "", line))
    body = LinesIn(lines=[line for _, _, line in sorted(prepared, key=lambda item: item[:2])])
    return await replace_lines(session, principal, document_id, version_id, body)


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
    return await list_lines(session, principal, document_id, version_id)


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
                DocumentLine.position_no,
                DocumentLine.name,
                DocumentLine.unit,
                DocumentLine.work_id,
                DocumentLine.project_node_id,
                DocumentLine.material_id,
                DocumentLine.material_batch_id,
                DocumentLine.warehouse_id,
                DocumentLine.responsible_user_id,
            ).where(DocumentLine.version_id == version_id, DocumentLine.deleted_at.is_(None))
        )
    ).all()
    links = {row.position_no: row for row in live_links}
    new_lines = [
        NewLine(
            position_no=line.position_no,
            name=line.name,
            unit=line.unit,
            quantity=line.quantity,
            work_id=_kept(_carry(links, line), line.position_no, "work_id"),
            project_node_id=_kept(_carry(links, line), line.position_no, "project_node_id"),
            material_id=_kept(_carry(links, line), line.position_no, "material_id"),
            material_batch_id=_kept(_carry(links, line), line.position_no, "material_batch_id"),
            warehouse_id=_kept(_carry(links, line), line.position_no, "warehouse_id"),
            responsible_user_id=_kept(_carry(links, line), line.position_no, "responsible_user_id"),
            unit_price=line.unit_price,
        )
        for line in parsed
    ]
    async with db_rules(session):
        await _replace(session, principal, draft, new_lines)
    return await list_lines(session, principal, document_id, version_id)
