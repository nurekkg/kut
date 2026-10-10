"""Нагрузочный ГПР для разработки: «Восток» целиком, 4 800 работ с зависимостями и подтверждённым
фактом. Запуск: `docker compose run --rm dev-seed python -m app.modules.schedule.devseed_load`.

Нужен, чтобы проверять таблицу и диаграмму Ганта на реальном объёме (ТЗ: объект до 10 000 узлов).
Обычный сид (`app.modules.auth.devseed`) кладёт только 40 работ. Работает под kut_worker и только
при APP_ENV=development; повторный запуск ничего не меняет, если работ уже больше 4 000.

Состав: 120 этажей x 5 работ (этажи с демо-ГПР пропускаются), 1 024 помещения x 4 работы отделки,
64 работы корпусов (16 на корпус) и 40 работ демо-ГПР - всего 4 800.
"""

import sys
import uuid
import zlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any

from sqlalchemy import Connection, create_engine, text

from app.core.config import get_settings

EAST = "ЖК «Восток» (dev)"
ALREADY_LOADED = 4_000
BATCH = 1_000
FIRST_START = date(2026, 3, 1)
FLOOR_STEP_DAYS = 28
BUILDING_SHIFT_DAYS = 45
SECTION_SHIFT_DAYS = 10
BUILDING_WORK_DAYS = 10
ROOMS_AFTER_FLOOR_DAYS = 20

# (код вида работ, длительность в днях, плановый объём)
FLOOR_CYCLE = (
    ("АРМ-003", 4, "12.000"),
    ("АРМ-001", 3, "8.500"),
    ("БЖБ-003", 2, "85.000"),
    ("БЖБ-004", 3, "110.000"),
    ("КЛД-002", 6, "240.000"),
)
ROOM_CYCLE = (
    ("ОТД-001", 5, "42.000"),
    ("ОТД-002", 4, "38.000"),
    ("ОТД-003", 6, "95.000"),
    ("ОТД-004", 5, "12.000"),
)
BUILDING_CODES = (
    "ЗМЛ-001", "ЗМЛ-002", "ЗМЛ-003", "ЗМЛ-004", "ЗМЛ-005", "БЖБ-001", "БЖБ-002", "БЖБ-005",
    "АРМ-002", "АРМ-004", "КЛД-001", "КЛД-004", "КРВ-001", "КРВ-002", "КРВ-003", "КРВ-004",
)  # fmt: skip

SELECT_ITEMS = (
    "SELECT node_id, work_id FROM schedule_item WHERE project_id = :p AND deleted_at IS NULL"
)
SELECT_USERS = (
    "SELECT id, email FROM \"user\" WHERE email IN ('foreman@kut.test', 'master@kut.test')"
)
SELECT_NODES = (
    "SELECT id, parent_id, type FROM project_node"
    " WHERE project_id = :p AND deleted_at IS NULL ORDER BY sort_order, id"
)


@dataclass(frozen=True, slots=True)
class Item:
    id: uuid.UUID
    node: Any
    work: Any
    start: date
    end: date
    volume: Decimal


def _share(item_id: uuid.UUID, start: date, end: date, today: date) -> Decimal:
    """Доля выполненного: прошедшие работы закрыты (часть отстаёт), идущие - по календарю."""
    luck = zlib.crc32(str(item_id).encode()) % 100
    if end < today - timedelta(days=7):
        return Decimal("0.70") if luck < 12 else Decimal(1)
    if start > today:
        return Decimal(0)
    days = (end - start).days + 1
    elapsed = min((today - start).days + 1, days)
    full = Decimal(elapsed) / Decimal(days)
    return (full * Decimal("0.8") if luck < 30 else full).quantize(Decimal("0.001"))


def load(connection: Connection) -> dict[str, int]:
    project = connection.execute(
        text("SELECT id, legal_entity_id FROM project WHERE name = :n AND deleted_at IS NULL"),
        {"n": EAST},
    ).one_or_none()
    if project is None:
        raise RuntimeError("the demo object is missing: run the main dev seed first")
    existing = connection.execute(text(SELECT_ITEMS), {"p": project.id}).all()
    if len(existing) >= ALREADY_LOADED:
        return {}
    taken = {(row.node_id, row.work_id) for row in existing}
    users = {row.email: row.id for row in connection.execute(text(SELECT_USERS))}
    foreman, master = users["foreman@kut.test"], users["master@kut.test"]
    works = {
        row.code: row.id
        for row in connection.execute(text("SELECT id, code FROM work WHERE deleted_at IS NULL"))
    }
    today = connection.execute(text("SELECT app.local_today()")).scalar_one()

    children: dict[Any, list[Any]] = defaultdict(list)
    buildings: list[Any] = []
    floor_ids: set[Any] = set()
    for node in connection.execute(text(SELECT_NODES), {"p": project.id}):
        children[node.parent_id].append(node)
        if node.type == "building":
            buildings.append(node)
        elif node.type == "floor":
            floor_ids.add(node.id)

    def chain(node: Any, start: date, cycle: tuple[tuple[str, int, str], ...]) -> list[Item]:
        made: list[Item] = []
        cursor = start
        for code, days, volume in cycle:
            end = cursor + timedelta(days=days - 1)
            if (node, works[code]) not in taken:
                made.append(Item(uuid.uuid4(), node, works[code], cursor, end, Decimal(volume)))
            cursor = end + timedelta(days=1)
        return made

    chains: list[list[Item]] = []
    for b_index, building in enumerate(buildings):
        base = FIRST_START + timedelta(days=BUILDING_SHIFT_DAYS * b_index)
        building_cycle = tuple((code, BUILDING_WORK_DAYS, "500.000") for code in BUILDING_CODES)
        chains.append(chain(building.id, base - timedelta(days=40), building_cycle))
        sections = [node for node in children[building.id] if node.type == "section"]
        for s_index, section in enumerate(sections):
            for f_index, floor in enumerate(children[section.id]):
                start = base + timedelta(
                    days=SECTION_SHIFT_DAYS * s_index + FLOOR_STEP_DAYS * f_index
                )
                chains.append(chain(floor.id, start, FLOOR_CYCLE))
                rooms_start = start + timedelta(days=ROOMS_AFTER_FLOOR_DAYS)
                chains.extend(
                    chain(room.id, rooms_start, ROOM_CYCLE) for room in children[floor.id]
                )
    items = [item for made in chains for item in made]

    insert_item = text(
        "INSERT INTO schedule_item (id, project_id, legal_entity_id, node_id, work_id, plan_start,"
        " plan_end, plan_volume, responsible_user_id) VALUES (:id, :p, :le, :node, :work, :start,"
        " :end, :volume, :who)"
    )
    for offset in range(0, len(items), BATCH):
        connection.execute(
            insert_item,
            [
                {
                    "id": item.id, "p": project.id, "le": project.legal_entity_id,
                    "node": item.node, "work": item.work, "start": item.start, "end": item.end,
                    "volume": item.volume, "who": foreman if item.node in floor_ids else master,
                }
                for item in items[offset : offset + BATCH]
            ],
        )  # fmt: skip

    edges = [(a.id, b.id) for made in chains for a, b in pairwise(made)]
    connection.execute(
        text(
            "INSERT INTO schedule_dependency (project_id, legal_entity_id, predecessor_id,"
            " successor_id) VALUES (:p, :le, :pred, :succ)"
        ),
        [{"p": project.id, "le": project.legal_entity_id, "pred": a, "succ": b} for a, b in edges],
    )

    facts = []
    for item in items:
        share = _share(item.id, item.start, item.end, today)
        if share > 0:
            facts.append(
                {
                    "id": uuid.uuid4(), "p": project.id, "le": project.legal_entity_id,
                    "node": item.node, "work": item.work, "item": item.id,
                    "volume": (item.volume * share).quantize(Decimal("0.001")),
                    "date": min(item.end, today), "author": master, "approver": foreman,
                }
            )  # fmt: skip
    insert_fact = text(
        "INSERT INTO work_actual (id, project_id, legal_entity_id, node_id, work_id,"
        " schedule_item_id, volume, work_date, author_id, status, approved_by, approved_at)"
        " VALUES (:id, :p, :le, :node, :work, :item, :volume, :date, :author, 'approved',"
        " :approver, now())"
    )
    for offset in range(0, len(facts), BATCH):
        connection.execute(insert_fact, facts[offset : offset + BATCH])
    return {
        "schedule_item": len(items),
        "schedule_dependency": len(edges),
        "work_actual": len(facts),
    }


def main() -> int:
    settings = get_settings()
    if settings.app_env != "development":
        print("load seed refused: APP_ENV must be 'development'", file=sys.stderr)
        return 1
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.begin() as connection:
        created = load(connection)
    engine.dispose()
    print(f"load seed: created {created}" if created else "load seed: already loaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
