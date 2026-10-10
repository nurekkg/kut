"""Нагрузка для сводки группы (S4): 20 объектов «Нагрузка 01-20» по 240 работ, всего 4 800.

Запуск:
`docker compose run --rm --no-deps dev-seed python -m app.modules.dashboards.devseed_load`.
Только при APP_ENV=development; повторный запуск ничего не меняет, если объекты уже есть.

Состав объекта: 2 корпуса x 3 секции x 5 этажей, на этаже 8 работ из каталога.
Факт детерминированный (по контрольной сумме): прошлые работы почти все одобрены, текущие
частично, будущие пусты, часть отправлена на подтверждение. Работает под kut_worker (BYPASSRLS),
как и остальные сиды.
"""

import sys
import uuid
import zlib
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, create_engine, text

from app.core.config import get_settings

EAST = "ЖК «Восток» (dev)"
PREFIX = "Нагрузка "
OBJECTS = 20
BUILDINGS = 2
SECTIONS = 3
FLOORS = 5
WORKS_PER_FLOOR = 8
FIRST_START = date(2026, 1, 15)
OBJECT_SHIFT_DAYS = 2
BUILDING_SHIFT_DAYS = 45
SECTION_SHIFT_DAYS = 10
FLOOR_STEP_DAYS = 28
WORK_DAYS = 3
BATCH = 1_000
ITEM_SQL = text(
    "INSERT INTO schedule_item (id, project_id, legal_entity_id, node_id, work_id, plan_start,"
    " plan_end, plan_volume) VALUES (:id, :p, :le, :n, :w, :s, :e, :v)"
)
FACT_SQL = text(
    "INSERT INTO work_actual (project_id, legal_entity_id, node_id, work_id, schedule_item_id,"
    " volume, work_date, author_id, status, approved_by, approved_at)"
    " VALUES (:p, :le, :n, :w, :i, :v, :d, :u, :s, CASE WHEN :ok THEN :u END,"
    " CASE WHEN :ok THEN now() END)"
)


def _roll(*key: object) -> int:
    return zlib.crc32(repr(key).encode()) % 100


def _node(
    connection: Connection, project: Any, entity: Any, kind: str, parent: Any, name: str
) -> Any:
    return connection.scalar(
        text("INSERT INTO project_node (project_id, legal_entity_id, parent_id, type, name)"
             " VALUES (:p, :le, :parent, :kind, :name) RETURNING id"),
        {"p": project, "le": entity, "parent": parent, "kind": kind, "name": name},
    )  # fmt: skip


def _plan_and_facts(
    index: int, today: date, project: Any, entity: Any, author: Any, works: list[Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Строки ГПР, факты и узлы этажей объекта (узлы создаются отдельно: им нужен RETURNING)."""
    start_object = FIRST_START + timedelta(days=OBJECT_SHIFT_DAYS * index)
    plans: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    floors: list[dict[str, Any]] = []
    for b in range(BUILDINGS):
        for s in range(SECTIONS):
            for f in range(FLOORS):
                floors.append({"b": b, "s": s, "f": f})
                floor_start = start_object + timedelta(
                    days=BUILDING_SHIFT_DAYS * b + SECTION_SHIFT_DAYS * s + FLOOR_STEP_DAYS * f
                )
                for k in range(WORKS_PER_FLOOR):
                    start = floor_start + timedelta(days=WORK_DAYS * k)
                    end = start + timedelta(days=WORK_DAYS - 1)
                    volume = Decimal(10 + (k * 5 + f + index) % 30)
                    key = (index, b, s, f, k)
                    plan = {
                        "id": uuid.uuid4(), "key": key, "p": project, "le": entity,
                        "w": works[(index * 7 + f * 3 + k) % len(works)],
                        "s": start, "e": end, "v": volume,
                        "floor": (b, s, f),
                    }  # fmt: skip
                    plans.append(plan)
                    roll = _roll(*key)
                    if end < today - timedelta(days=14):
                        if roll < 85:
                            facts.append({**plan, "vol": volume, "day": end,
                                          "status": "approved"})  # fmt: skip
                        elif roll < 95:
                            facts.append({**plan, "vol": volume / 2, "day": start,
                                          "status": "approved"})  # fmt: skip
                    elif start <= today <= end:
                        if roll < 40:
                            facts.append({**plan, "vol": volume * Decimal("0.4"),
                                          "day": start, "status": "approved"})  # fmt: skip
                        elif roll < 50:
                            facts.append({**plan, "vol": volume * Decimal("0.3"),
                                          "day": start, "status": "submitted"})  # fmt: skip
    return plans, facts, floors


def _object(
    connection: Connection, index: int, entity: Any, author: Any, today: date, works: list[Any]
) -> None:
    project = connection.scalar(
        text("INSERT INTO project (legal_entity_id, name) VALUES (:le, :n) RETURNING id"),
        {"le": entity, "n": f"{PREFIX}{index + 1:02d}"},
    )
    connection.execute(
        text(
            "INSERT INTO project_finance (project_id, legal_entity_id, planned_budget)"
            " VALUES (:p, :le, :b)"
        ),
        {"p": project, "le": entity, "b": Decimal(50_000_000 + index * 1_000_000)},
    )
    plans, facts, floors = _plan_and_facts(index, today, project, entity, author, works)
    nodes: dict[tuple[int, int, int], Any] = {}
    buildings: dict[int, Any] = {}
    sections: dict[tuple[int, int], Any] = {}
    for b in range(BUILDINGS):
        buildings[b] = _node(connection, project, entity, "building", None, f"Корпус {b + 1}")
    for b in range(BUILDINGS):
        for s in range(SECTIONS):
            sections[(b, s)] = _node(
                connection, project, entity, "section", buildings[b], f"Секция {s + 1}"
            )
    for floor in floors:
        b, s, f = floor["b"], floor["s"], floor["f"]
        nodes[(b, s, f)] = _node(
            connection, project, entity, "floor", sections[(b, s)], f"Этаж {f + 1}"
        )
    for plan in plans:
        plan["n"] = nodes[plan["floor"]]
    for batch in range(0, len(plans), BATCH):
        connection.execute(
            ITEM_SQL,
            [{k: v for k, v in row.items() if k in {"id", "p", "le", "n", "w", "s", "e", "v"}}
             for row in plans[batch:batch + BATCH]],
        )  # fmt: skip
    by_key = {plan["key"]: plan for plan in plans}
    fact_rows = [
        {
            "p": project,
            "le": entity,
            "n": by_key[fact["key"]]["n"],
            "w": by_key[fact["key"]]["w"],
            "i": by_key[fact["key"]]["id"],
            "v": fact["vol"],
            "d": fact["day"],
            "u": author,
            "s": fact["status"],
            "ok": fact["status"] == "approved",
        }
        for fact in facts
    ]
    for batch in range(0, len(fact_rows), BATCH):
        connection.execute(FACT_SQL, fact_rows[batch : batch + BATCH])


def load(connection: Connection) -> int:
    if connection.scalar(
        text("SELECT count(*) FROM project WHERE name = :n"), {"n": f"{PREFIX}01"}
    ):
        return 0
    entity = connection.scalar(
        text("SELECT legal_entity_id FROM project WHERE name = :n"), {"n": EAST}
    )
    author = connection.scalar(
        text('SELECT id FROM "user" WHERE email = :e'), {"e": "foreman@kut.test"}
    )
    today: date = connection.scalar(text("SELECT (now() AT TIME ZONE 'Asia/Bishkek')::date"))
    works = [row.id for row in connection.execute(text("SELECT id FROM work ORDER BY code"))]
    for index in range(OBJECTS):
        _object(connection, index, entity, author, today, works)
    return OBJECTS


def main() -> int:
    settings = get_settings()
    if settings.app_env != "development":
        print("load seed refused: APP_ENV must be 'development'", file=sys.stderr)
        return 1
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.begin() as connection:
        created = load(connection)
    engine.dispose()
    print(f"load seed: created {created} objects" if created else "load seed: already loaded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
