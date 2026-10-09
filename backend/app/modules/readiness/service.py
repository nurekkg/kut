import uuid
from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.objects.models import ProjectNode
from app.modules.readiness.schemas import (
    Aggregate,
    ItemProgress,
    NodeReadiness,
    NodeReadinessDetail,
    ProjectReadiness,
    UnplannedVolume,
)

HUNDRED = Decimal(100)
TWO = Decimal("0.01")
ONE = Decimal("0.1")

# Расчёт целиком в БД (миграция f8c2a5e61d47): функции работают от имени вызывающего, поэтому
# границу доступа ставит RLS - внешняя роль считает только свой объект и подтверждённый факт.
_TODAY = text("SELECT app.local_today()")
_NODES = text("SELECT * FROM app.project_readiness(:project, :as_of, CAST(:root AS uuid))")
_ITEMS = text(
    "SELECT pr.item_id, pr.node_id, pr.work_id, w.code AS work_code, w.name AS work_name, w.unit,"
    " pr.plan_start, pr.plan_end, pr.plan_volume, pr.weight, pr.confirmed_volume,"
    " pr.fraction, pr.planned_fraction, pr.delay_days, pr.state, pr.overdue_days"
    " FROM app.schedule_progress(:project, :as_of, :node) pr"
    " LEFT JOIN work w ON w.id = pr.work_id WHERE pr.node_id = :node"
    " ORDER BY pr.plan_start, w.code, pr.item_id"
)
_UNPLANNED = text(
    "SELECT a.work_id, w.name AS work_name, w.unit, sum(a.volume) AS volume, count(*)::int AS facts"
    " FROM work_actual a LEFT JOIN work w ON w.id = a.work_id"
    " WHERE a.project_id = :project AND a.node_id = :node AND a.schedule_item_id IS NULL"
    " AND a.status = 'approved' AND a.deleted_at IS NULL"
    " GROUP BY a.work_id, w.name, w.unit ORDER BY w.name, a.work_id"
)


def _q(value: Decimal, places: Decimal) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)


def aggregate(
    *,
    items: int | None,
    weight: Decimal | None,
    done: Decimal | None,
    planned: Decimal | None,
    delay: Decimal | None,
    completed: int | None,
    overdue: int | None,
    overdue_no_fact: int | None,
) -> Aggregate | None:
    """Процент = сумма(вес x доля) / сумма веса: именно так, а не среднее процентов детей."""
    if not items or not weight:
        return None
    percent = (done or Decimal(0)) / weight * HUNDRED
    planned_percent = (planned or Decimal(0)) / weight * HUNDRED
    return Aggregate(
        items=items,
        weight=weight,
        percent=_q(percent, TWO),
        planned_percent=_q(planned_percent, TWO),
        deviation=_q(percent - planned_percent, TWO),
        delay_days=_q((delay or Decimal(0)) / weight, ONE),
        completed=completed or 0,
        overdue=overdue or 0,
        overdue_no_fact=overdue_no_fact or 0,
    )


def _own(row: Any) -> Aggregate | None:
    return aggregate(
        items=row.own_items,
        weight=row.own_weight,
        done=row.own_done,
        planned=row.own_planned,
        delay=row.own_delay,
        completed=row.own_completed,
        overdue=row.own_overdue,
        overdue_no_fact=row.own_overdue_no_fact,
    )


def _subtree(row: Any) -> Aggregate | None:
    return aggregate(
        items=row.sub_items,
        weight=row.sub_weight,
        done=row.sub_done,
        planned=row.sub_planned,
        delay=row.sub_delay,
        completed=row.sub_completed,
        overdue=row.sub_overdue,
        overdue_no_fact=row.sub_overdue_no_fact,
    )


def _node(row: Any) -> NodeReadiness | None:
    subtree = _subtree(row)
    if subtree is None:
        return None
    return NodeReadiness(
        node_id=row.node_id, parent_id=row.parent_id, own=_own(row), subtree=subtree
    )


def _object_total(rows: Sequence[Any]) -> Aggregate | None:
    """Объект - сумма своих итогов всех узлов (каждая работа считается один раз)."""

    def total(column: str) -> Any:
        values = [getattr(row, column) for row in rows if getattr(row, column) is not None]
        return sum(values, start=type(values[0])(0)) if values else None

    return aggregate(
        items=total("own_items"),
        weight=total("own_weight"),
        done=total("own_done"),
        planned=total("own_planned"),
        delay=total("own_delay"),
        completed=total("own_completed"),
        overdue=total("own_overdue"),
        overdue_no_fact=total("own_overdue_no_fact"),
    )


async def _as_of(session: AsyncSession, as_of: date | None) -> date:
    return as_of if as_of is not None else await session.scalar(_TODAY)


async def project_readiness(
    session: AsyncSession, project_id: uuid.UUID, as_of: date | None
) -> ProjectReadiness:
    day = await _as_of(session, as_of)
    rows = (
        await session.execute(_NODES, {"project": project_id, "as_of": day, "root": None})
    ).all()
    nodes = [node for node in (_node(row) for row in rows) if node is not None]
    return ProjectReadiness(as_of=day, project=_object_total(rows), nodes=nodes)


async def node_readiness(
    session: AsyncSession, project_id: uuid.UUID, node_id: uuid.UUID, as_of: date | None
) -> NodeReadinessDetail:
    found = await session.scalar(
        select(ProjectNode.id).where(
            ProjectNode.id == node_id, ProjectNode.project_id == project_id
        )
    )
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    day = await _as_of(session, as_of)
    params = {"project": project_id, "as_of": day, "node": node_id}
    rows = (await session.execute(_NODES, {**params, "root": node_id})).all()
    own_row = next((row for row in rows if row.node_id == node_id), None)
    children = [
        node
        for node in (_node(row) for row in rows if row.parent_id == node_id)
        if node is not None
    ]
    items = [
        ItemProgress(
            item_id=row.item_id,
            node_id=row.node_id,
            work_id=row.work_id,
            work_code=row.work_code,
            work_name=row.work_name,
            unit=row.unit,
            plan_start=row.plan_start,
            plan_end=row.plan_end,
            plan_volume=row.plan_volume,
            weight=row.weight,
            confirmed_volume=row.confirmed_volume,
            overrun_volume=max(row.confirmed_volume - row.plan_volume, Decimal(0)),
            percent=_q(row.fraction * HUNDRED, TWO),
            planned_percent=_q(row.planned_fraction * HUNDRED, TWO),
            delay_days=_q(row.delay_days, ONE),
            state=row.state,
            overdue_days=row.overdue_days,
        )
        for row in (await session.execute(_ITEMS, {**params, "root": node_id})).all()
    ]
    unplanned = [
        UnplannedVolume.model_validate(row, from_attributes=True)
        for row in (await session.execute(_UNPLANNED, params)).all()
    ]
    return NodeReadinessDetail(
        as_of=day,
        node_id=node_id,
        own=_own(own_row) if own_row is not None else None,
        subtree=_subtree(own_row) if own_row is not None else None,
        children=children,
        items=items,
        unplanned=unplanned,
    )
