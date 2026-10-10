"""Дев-сид: «Запад» с ГПР и фактом, чтобы сводка группы показывала больше одного объекта (S4).

Запуск: `docker compose run --rm --no-deps dev-seed python -m app.modules.dashboards.devseed`.
Только при APP_ENV=development; повторный запуск ничего не меняет, если у объекта уже есть ГПР.

Состав: этаж 1 выполнен (факт одобрен полностью), этаж 2 просрочен (часть факта), этаж 3 в работе
(одобренный, отправленный и черновой факт), этаж 4 ещё не начат. Даты - относительно 2026 года,
проценты и просрочки в дашборде видны на дату ведения dev-базы.
"""

import sys
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Connection, create_engine, text

from app.core.config import get_settings

WEST = "ЖК «Запад» (dev)"
FOREMAN_EMAIL = "foreman@kut.test"


@dataclass(frozen=True)
class Plan:
    code: str
    start: date
    end: date
    volume: str


@dataclass(frozen=True)
class Fact:
    code: str
    day: date
    volume: str
    status: str


FLOORS: tuple[tuple[str, tuple[Plan, ...], tuple[Fact, ...]], ...] = (
    (
        "Этаж 1",
        (
            Plan("АРМ-003", date(2026, 6, 1), date(2026, 6, 4), "12"),
            Plan("АРМ-001", date(2026, 6, 5), date(2026, 6, 8), "8.5"),
            Plan("БЖБ-003", date(2026, 6, 9), date(2026, 6, 10), "85"),
        ),
        (
            Fact("АРМ-003", date(2026, 6, 4), "12", "approved"),
            Fact("АРМ-001", date(2026, 6, 8), "8.5", "approved"),
            Fact("БЖБ-003", date(2026, 6, 10), "85", "approved"),
        ),
    ),
    (
        "Этаж 2",
        (
            Plan("АРМ-003", date(2026, 7, 1), date(2026, 7, 4), "12"),
            Plan("АРМ-001", date(2026, 7, 5), date(2026, 7, 8), "8.5"),
            Plan("БЖБ-003", date(2026, 7, 9), date(2026, 7, 10), "85"),
        ),
        (
            Fact("АРМ-003", date(2026, 7, 4), "12", "approved"),
            Fact("АРМ-001", date(2026, 7, 8), "4.25", "approved"),
        ),
    ),
    (
        "Этаж 3",
        (
            Plan("АРМ-003", date(2026, 9, 1), date(2026, 9, 4), "12"),
            Plan("АРМ-001", date(2026, 9, 5), date(2026, 9, 8), "8.5"),
            Plan("БЖБ-003", date(2026, 9, 9), date(2026, 9, 10), "85"),
        ),
        (
            Fact("АРМ-003", date(2026, 9, 4), "12", "approved"),
            Fact("АРМ-001", date(2026, 9, 5), "3.4", "approved"),
            Fact("АРМ-001", date(2026, 9, 8), "2.55", "submitted"),
            Fact("БЖБ-003", date(2026, 9, 20), "20", "draft"),
        ),
    ),
    (
        "Этаж 4",
        (
            Plan("АРМ-003", date(2026, 10, 15), date(2026, 10, 18), "12"),
            Plan("АРМ-001", date(2026, 10, 19), date(2026, 10, 22), "8.5"),
            Plan("БЖБ-003", date(2026, 10, 23), date(2026, 10, 24), "85"),
        ),
        (),
    ),
)


def _west_exists(connection: Connection, project: uuid.UUID) -> bool:
    count = connection.scalar(
        text("SELECT count(*) FROM schedule_item WHERE project_id = :p AND deleted_at IS NULL"),
        {"p": project},
    )
    return bool(count)


def _node(
    connection: Connection,
    project: uuid.UUID,
    entity: uuid.UUID,
    kind: str,
    parent: uuid.UUID | None,
    name: str,
) -> uuid.UUID:
    node: uuid.UUID = connection.scalar(
        text("INSERT INTO project_node (project_id, legal_entity_id, parent_id, type, name)"
             " VALUES (:p, :le, :parent, :kind, :name) RETURNING id"),
        {"p": project, "le": entity, "parent": parent, "kind": kind, "name": name},
    )  # fmt: skip
    return node


def load(connection: Connection) -> bool:
    project = connection.execute(
        text("SELECT id, legal_entity_id FROM project WHERE name = :n AND deleted_at IS NULL"),
        {"n": WEST},
    ).one_or_none()
    if project is None:
        return False
    if _west_exists(connection, project.id):
        return False
    author = connection.scalar(
        text('SELECT id FROM "user" WHERE email = :e AND deleted_at IS NULL'),
        {"e": FOREMAN_EMAIL},
    )
    works = {
        row.code: row.id for row in connection.execute(text("SELECT id, code FROM work")).all()
    }
    entity = project.legal_entity_id
    building = _node(connection, project.id, entity, "building", None, "Корпус Б")
    section = _node(connection, project.id, entity, "section", building, "Секция 1")
    for floor_name, plans, facts in FLOORS:
        floor = _node(connection, project.id, entity, "floor", section, floor_name)
        items: dict[str, uuid.UUID] = {}
        for plan in plans:
            item: uuid.UUID = connection.scalar(
                text("INSERT INTO schedule_item (project_id, legal_entity_id, node_id, work_id,"
                     " plan_start, plan_end, plan_volume)"
                     " VALUES (:p, :le, :n, :w, :s, :e, :v) RETURNING id"),
                {"p": project.id, "le": entity, "n": floor, "w": works[plan.code],
                 "s": plan.start, "e": plan.end, "v": Decimal(plan.volume)},
            )  # fmt: skip
            items[plan.code] = item
        for fact in facts:
            approved = fact.status == "approved"
            connection.execute(
                text("INSERT INTO work_actual (project_id, legal_entity_id, node_id, work_id,"
                     " schedule_item_id, volume, work_date, author_id, status, approved_by,"
                     " approved_at) VALUES (:p, :le, :n, :w, :i, :v, :d, :u, :s,"
                     " CASE WHEN :ok THEN :u END, CASE WHEN :ok THEN now() END)"),
                {"p": project.id, "le": entity, "n": floor, "w": works[fact.code],
                 "i": items[fact.code], "v": Decimal(fact.volume), "d": fact.day,
                 "u": author, "s": fact.status, "ok": approved},
            )  # fmt: skip
    return True


def main() -> int:
    settings = get_settings()
    if settings.app_env != "development":
        print("west seed refused: APP_ENV must be 'development'", file=sys.stderr)
        return 1
    engine = create_engine(settings.database_url.get_secret_value())
    with engine.begin() as connection:
        created = load(connection)
    engine.dispose()
    print("west seed: created" if created else "west seed: already loaded or no project")
    return 0


if __name__ == "__main__":
    sys.exit(main())
