"""Склад (ADR-016 часть 5, S7a): склады объектов, остатки, журнал, проведение ВН, сторно.
Приход и расход пишут функции БД (kut_auth); здесь - вызовы функций и чтение с RLS."""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rules import db_rules
from app.modules.documents.models import DocumentVersion
from app.modules.materials.models import Material
from app.modules.objects.models import Project
from app.modules.stock.models import StockMovement, Warehouse


@dataclass(frozen=True, slots=True)
class Balance:
    warehouse_id: uuid.UUID
    material_id: uuid.UUID
    base_unit: str
    qty: Decimal


async def list_warehouses(session: AsyncSession, project_id: uuid.UUID | None) -> list[Warehouse]:
    query = select(Warehouse).where(Warehouse.deleted_at.is_(None)).order_by(Warehouse.name)
    if project_id is not None:
        query = query.where(Warehouse.project_id == project_id)
    return list((await session.scalars(query)).all())


async def create_warehouse(session: AsyncSession, project_id: uuid.UUID, name: str) -> Warehouse:
    legal_entity_id = await session.scalar(
        select(Project.legal_entity_id).where(
            Project.id == project_id, Project.deleted_at.is_(None)
        )
    )
    if legal_entity_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    async with db_rules(session):
        row = Warehouse(
            project_id=project_id, legal_entity_id=legal_entity_id, kind="object", name=name
        )
        session.add(row)
        await session.flush()
    await session.commit()
    return row


async def balances(session: AsyncSession, warehouse_id: uuid.UUID) -> list[Balance]:
    rows = (
        await session.execute(
            select(
                StockMovement.warehouse_id,
                StockMovement.material_id,
                Material.base_unit,
                func.sum(StockMovement.qty).label("qty"),
            )
            .join(Material, Material.id == StockMovement.material_id)
            .where(StockMovement.warehouse_id == warehouse_id)
            .group_by(StockMovement.warehouse_id, StockMovement.material_id, Material.base_unit)
            .order_by(Material.base_unit)
        )
    ).all()
    return [
        Balance(
            warehouse_id=row.warehouse_id,
            material_id=row.material_id,
            base_unit=row.base_unit,
            qty=row.qty,
        )
        for row in rows
    ]


async def movements(
    session: AsyncSession, warehouse_id: uuid.UUID, limit: int
) -> list[StockMovement]:
    return list(
        (
            await session.scalars(
                select(StockMovement)
                .where(StockMovement.warehouse_id == warehouse_id)
                .order_by(StockMovement.created_at.desc(), StockMovement.id)
                .limit(limit)
            )
        ).all()
    )


async def post_waybill(session: AsyncSession, document_id: uuid.UUID, version_id: uuid.UUID) -> int:
    """Проведение ВН: расход по позициям, версия сразу утверждена (без маршрута)."""
    owner = await session.scalar(
        select(DocumentVersion.document_id).where(DocumentVersion.id == version_id)
    )
    if owner is None or owner != document_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    async with db_rules(session):
        count = await session.scalar(
            text("SELECT app.post_waybill(CAST(:v AS uuid))"), {"v": str(version_id)}
        )
        await session.flush()
    await session.commit()
    return int(count or 0)


async def reverse(session: AsyncSession, movement_id: uuid.UUID) -> uuid.UUID:
    async with db_rules(session):
        new_id = await session.scalar(
            text("SELECT app.stock_reverse(CAST(:m AS uuid))"), {"m": str(movement_id)}
        )
        await session.flush()
    await session.commit()
    return uuid.UUID(str(new_id))
