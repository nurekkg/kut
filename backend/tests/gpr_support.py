"""Общие помощники тестов S3a: ГПР, факт работ, фото, готовность.

Тестовые данные создаются поверх демо-объекта «Восток» (devseed) под kut_worker и уходят по префиксу
S3A- мягким удалением: листья раньше родителей, фото раньше факта, факт раньше строки ГПР.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.context import RequestContext
from tests.auth_support import AuthWorld
from tests.db_support import context

PREFIX = "S3A-"


def unique(prefix: str = PREFIX) -> str:
    return f"{prefix}{uuid4().hex[:8]}"


@dataclass(frozen=True)
class Plan:
    """Узел, вид работ и строка ГПР одного теста."""

    project: UUID
    entity: UUID
    node: UUID
    work: UUID
    item: UUID


def ctx(world: AuthWorld, who: str, *, project: str = "east") -> RequestContext:
    """Контекст запроса пользователя dev-сида для SQL под kut_app (или kut_portal у внешних)."""
    external = who in ("customer", "sub_a", "sub_b", "supplier")
    counterparty = {
        "customer": "Заказчик (dev)",
        "sub_a": "Субподрядчик А (dev)",
        "sub_b": "Субподрядчик Б (dev)",
        "supplier": "Поставщик (dev)",
    }.get(who)
    return context(
        world.users[who],
        internal=not external,
        projects={world.projects[project]},
        counterparty=world.counterparties[counterparty] if counterparty else None,
    )


async def make_plan(
    engine: AsyncEngine,
    world: AuthWorld,
    *,
    unit: str = "м3",
    volume: str = "100",
    start: date = date(2026, 9, 1),
    end: date = date(2026, 9, 10),
    weight: str | None = None,
    with_item: bool = True,
    kind: str = "building",
) -> Plan:
    """Корневой узел под «Востоком», вид работ с единицей unit и (по умолчанию) строка ГПР."""
    project = world.projects["east"]
    async with engine.begin() as connection:
        entity = await connection.scalar(
            text("SELECT legal_entity_id FROM project WHERE id = :id"), {"id": project}
        )
        node = await connection.scalar(
            text(
                "INSERT INTO project_node (project_id, legal_entity_id, type, name)"
                " VALUES (:p, :le, :kind, :name) RETURNING id"
            ),
            {"p": project, "le": entity, "kind": kind, "name": unique()},
        )
        work = await connection.scalar(
            text(
                "INSERT INTO work (code, name, unit, category)"
                " VALUES (:code, :name, :unit, 'S3A') RETURNING id"
            ),
            {"code": unique(), "name": unique("Работа "), "unit": unit},
        )
        item = None
        if with_item:
            item = await add_item(
                connection, project, entity, node, work, volume, start, end, weight
            )
    return Plan(project=project, entity=entity, node=node, work=work, item=item)


async def add_item(
    connection: Any,
    project: UUID,
    entity: UUID,
    node: UUID,
    work: UUID,
    volume: str,
    start: date,
    end: date,
    weight: str | None = None,
) -> UUID:
    item: UUID = await connection.scalar(
        text(
            "INSERT INTO schedule_item (project_id, legal_entity_id, node_id, work_id,"
            " plan_start, plan_end, plan_volume, weight)"
            " VALUES (:p, :le, :node, :work, :start, :end, :volume, :weight) RETURNING id"
        ),
        {
            "p": project,
            "le": entity,
            "node": node,
            "work": work,
            "start": start,
            "end": end,
            "volume": volume,
            "weight": weight,
        },
    )
    return item


async def purge(engine: AsyncEngine, world: AuthWorld) -> None:
    """Мягко удаляет данные тестов S3a (всё, что создано с префиксом S3A- в узле или виде работ)."""
    project = world.projects["east"]
    async with engine.begin() as connection:
        nodes = "(SELECT id FROM project_node WHERE project_id = :p AND name LIKE 'S3A-%')"
        works = "(SELECT id FROM work WHERE code LIKE 'S3A-%')"
        for statement in (
            "UPDATE work_photo SET deleted_at = now() WHERE deleted_at IS NULL AND work_actual_id"
            f" IN (SELECT id FROM work_actual WHERE node_id IN {nodes} OR work_id IN {works})",
            "UPDATE work_actual SET deleted_at = now() WHERE deleted_at IS NULL"
            f" AND (node_id IN {nodes} OR work_id IN {works})",
            "UPDATE schedule_dependency SET deleted_at = now() WHERE deleted_at IS NULL AND"
            f" predecessor_id IN (SELECT id FROM schedule_item WHERE node_id IN {nodes}"
            f" OR work_id IN {works})",
            "UPDATE schedule_dependency SET deleted_at = now() WHERE deleted_at IS NULL AND"
            f" successor_id IN (SELECT id FROM schedule_item WHERE node_id IN {nodes}"
            f" OR work_id IN {works})",
            "UPDATE schedule_item SET deleted_at = now() WHERE deleted_at IS NULL"
            f" AND (node_id IN {nodes} OR work_id IN {works})",
        ):
            await connection.execute(text(statement), {"p": project})
        for kind in ("room", "floor", "section", "building", "stage"):
            await connection.execute(
                text(
                    "UPDATE project_node SET deleted_at = now() WHERE deleted_at IS NULL"
                    " AND project_id = :p AND type = :t AND name LIKE 'S3A-%'"
                ),
                {"p": project, "t": kind},
            )
        await connection.execute(
            text("UPDATE work SET deleted_at = now() WHERE deleted_at IS NULL AND code LIKE 'S3A-%'")
        )
