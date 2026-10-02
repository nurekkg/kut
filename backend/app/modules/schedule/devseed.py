"""Демо-ГПР и факт для разработки: 40 работ «Востока» с зависимостями и факт в разных статусах.

Вызывается из app.modules.auth.devseed под kut_worker (системный контекст: автора и подтвердившего
сид задаёт явно, права в БД не проверяются). Повторный запуск ничего не меняет: пока у объекта
есть живые строки ГПР, сид пропускается.
"""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

FIRST_FLOOR_START = date(2026, 6, 1)
FLOOR_STEP_DAYS = 28
FLOORS = 8
BUILDING = "Корпус 1"
SECTION = "Секция А"

# (код вида работ, длительность в днях, плановый объём на этаж, кто отвечает: прораб или мастер)
CYCLE = (
    ("АРМ-003", 4, Decimal("12.000"), "foreman"),
    ("АРМ-001", 3, Decimal("8.500"), "foreman"),
    ("БЖБ-003", 2, Decimal("85.000"), "foreman"),
    ("БЖБ-004", 3, Decimal("110.000"), "foreman"),
    ("КЛД-002", 6, Decimal("240.000"), "master"),
)
UNPLANNED_WORK = "ЗМЛ-004"


@dataclass(frozen=True, slots=True)
class ScheduleReport:
    items: int = 0
    dependencies: int = 0
    actuals: int = 0


def _scalar(connection: Connection, sql: str, **params: Any) -> Any:
    return connection.execute(text(sql), params).scalar_one_or_none()


def _floors(connection: Connection, project_id: Any) -> list[Any]:
    return list(
        connection.execute(
            text(
                "SELECT floor.id, floor.name FROM project_node floor"
                " JOIN project_node section ON section.id = floor.parent_id"
                " JOIN project_node building ON building.id = section.parent_id"
                " WHERE floor.project_id = :project AND floor.type = 'floor'"
                " AND floor.deleted_at IS NULL AND section.name = :section"
                " AND building.name = :building ORDER BY floor.sort_order LIMIT :n"
            ),
            {"project": project_id, "section": SECTION, "building": BUILDING, "n": FLOORS},
        )
    )


def ensure_schedule(
    connection: Connection,
    project_id: Any,
    legal_entity_id: Any,
    *,
    foreman_id: Any,
    master_id: Any,
) -> ScheduleReport:
    """40 строк ГПР (8 этажей x 5 работ), цепочка FS внутри этажа и между этажами, факт."""
    if _scalar(
        connection,
        "SELECT id FROM schedule_item WHERE project_id = :p AND deleted_at IS NULL LIMIT 1",
        p=project_id,
    ):
        return ScheduleReport()
    floors = _floors(connection, project_id)
    if len(floors) < FLOORS:
        return ScheduleReport()
    works = {
        row.code: row.id
        for row in connection.execute(
            text("SELECT id, code FROM work WHERE code = ANY(:codes) AND deleted_at IS NULL"),
            {"codes": [code for code, *_ in CYCLE] + [UNPLANNED_WORK]},
        )
    }
    people = {"foreman": foreman_id, "master": master_id}

    items: dict[tuple[int, int], Any] = {}
    for floor_index, floor in enumerate(floors):
        start = FIRST_FLOOR_START + timedelta(days=FLOOR_STEP_DAYS * floor_index)
        for step, (code, days, volume, who) in enumerate(CYCLE):
            end = start + timedelta(days=days - 1)
            # Один вес задан вручную: он переопределяет длительность (пример для ПТО).
            weight = Decimal("10.000") if (floor_index, step) == (FLOORS - 1, 4) else None
            items[(floor_index, step)] = connection.execute(
                text(
                    "INSERT INTO schedule_item (project_id, legal_entity_id, node_id, work_id,"
                    " plan_start, plan_end, plan_volume, weight, responsible_user_id)"
                    " VALUES (:project, :entity, :node, :work, :start, :end, :volume, :weight,"
                    " :who) RETURNING id"
                ),
                {
                    "project": project_id,
                    "entity": legal_entity_id,
                    "node": floor.id,
                    "work": works[code],
                    "start": start,
                    "end": end,
                    "volume": volume,
                    "weight": weight,
                    "who": people[who],
                },
            ).scalar_one()
            start = end + timedelta(days=1)

    edges = []
    for floor_index in range(FLOORS):
        edges += [
            (items[(floor_index, step)], items[(floor_index, step + 1)])
            for step in range(len(CYCLE) - 1)
        ]
        if floor_index + 1 < FLOORS:
            edges.append((items[(floor_index, len(CYCLE) - 2)], items[(floor_index + 1, 0)]))
    connection.execute(
        text(
            "INSERT INTO schedule_dependency (project_id, legal_entity_id, predecessor_id,"
            " successor_id) VALUES (:project, :entity, :predecessor, :successor)"
        ),
        [
            {
                "project": project_id,
                "entity": legal_entity_id,
                "predecessor": predecessor,
                "successor": successor,
            }
            for predecessor, successor in edges
        ],
    )
    actuals = _seed_actuals(
        connection,
        project_id,
        legal_entity_id,
        floors=floors,
        items=items,
        works=works,
        foreman_id=foreman_id,
        master_id=master_id,
    )
    return ScheduleReport(items=len(items), dependencies=len(edges), actuals=actuals)


def _seed_actuals(
    connection: Connection,
    project_id: Any,
    legal_entity_id: Any,
    *,
    floors: list[Any],
    items: dict[tuple[int, int], Any],
    works: dict[str, Any],
    foreman_id: Any,
    master_id: Any,
) -> int:
    """Этажи 1-4 выполнены (мастер вносит, прораб подтверждает), этаж 5 в работе: все статусы."""
    today = connection.execute(text("SELECT app.local_today()")).scalar_one()
    rows: list[dict[str, Any]] = []

    def add(
        floor_index: int,
        step: int,
        share: str,
        offset_days: int,
        status: str,
        *,
        author: Any = None,
        reason: str | None = None,
        geo: tuple[str, str, str] | None = None,
        planned: bool = True,
        work: str | None = None,
        volume: str | None = None,
    ) -> None:
        code, days, plan_volume, _who = CYCLE[step]
        start = FIRST_FLOOR_START + timedelta(days=FLOOR_STEP_DAYS * floor_index)
        start += timedelta(days=sum(entry[1] for entry in CYCLE[:step]))
        author = author or master_id
        approved = status == "approved"
        rows.append(
            {
                "id": uuid.uuid4(),
                "project": project_id,
                "entity": legal_entity_id,
                "node": floors[floor_index].id,
                "work": works[work or code],
                "item": items[(floor_index, step)] if planned else None,
                "volume": Decimal(volume) if volume else plan_volume * Decimal(share),
                "date": min(start + timedelta(days=min(offset_days, days - 1)), today),
                "author": author,
                "status": status,
                "approved_by": foreman_id if approved else None,
                "reason": reason,
                "lat": geo[0] if geo else None,
                "lon": geo[1] if geo else None,
                "accuracy": geo[2] if geo else None,
            }
        )

    site = ("42.874600", "74.569800", "8.50")
    for floor_index in range(4):
        for step in range(len(CYCLE)):
            geo = site if step == 2 else None
            if (floor_index, step) == (0, 4):
                add(floor_index, step, "1.10", 5, "approved", geo=geo)
                continue
            add(floor_index, step, "0.60", 0, "approved", geo=geo)
            add(floor_index, step, "0.40", 1, "approved", author=foreman_id)
    add(2, 4, "0.375", 5, "rejected", reason="Объём больше проектного по секции: уточните обмер")
    add(4, 0, "1.00", 3, "approved", geo=site)
    add(4, 1, "0.60", 2, "approved")
    add(4, 2, "0.40", 1, "submitted", geo=site)
    add(4, 3, "0.20", 0, "draft")
    # Факт на узел без плана в ГПР: разрешён, в % готовности не входит.
    add(0, 0, "1", 0, "approved", planned=False, work=UNPLANNED_WORK, volume="15.000")

    connection.execute(
        text(
            "INSERT INTO work_actual (id, project_id, legal_entity_id, node_id, work_id,"
            " schedule_item_id, volume, work_date, author_id, status, approved_by, approved_at,"
            " reject_reason, latitude, longitude, accuracy_m) VALUES (:id, :project, :entity,"
            " :node, :work, :item, :volume, :date, :author, :status, :approved_by,"
            " CASE WHEN CAST(:approved_by AS uuid) IS NULL THEN NULL ELSE now() END, :reason,"
            " CAST(:lat AS numeric), CAST(:lon AS numeric), CAST(:accuracy AS numeric))"
        ),
        rows,
    )
    return len(rows)
