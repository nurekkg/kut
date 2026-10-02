"""Демо-данные модуля «Объекты» для разработки: карточки объектов, дерево, справочник работ.

Вызывается из app.modules.auth.devseed под kut_worker (BYPASSRLS). Повторный запуск ничего не
меняет: карточка дополняется, только пока она пуста, дерево строится только пустому объекту,
работы ищутся по коду.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text

# (стадия, начало, окончание, бюджет KGS, адрес, широта, долгота, заказчик из COUNTERPARTIES)
PROJECT_CARDS: dict[str, tuple[str, str, str, str, str, str, str, str | None]] = {
    "east": (
        "construction",
        "2025-03-01",
        "2028-12-31",
        "2400000000.00",
        "г. Бишкек, ул. Ахунбаева, 120",
        "42.874600",
        "74.569800",
        "Заказчик (dev)",
    ),
    "west": (
        "design",
        "2026-01-15",
        "2029-06-30",
        "980000000.00",
        "г. Ош, ул. Курманжан Датка, 45",
        "40.528300",
        "72.798500",
        None,
    ),
}

STAGES = 2
BUILDINGS_PER_STAGE = 2
SECTIONS_PER_BUILDING = 2
FLOORS_PER_SECTION = 16
ROOMS_PER_FLOOR = 8

# (категория, единица, [(код, название)])
WORKS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "Земляные работы",
        "м3",
        (
            ("ЗМЛ-001", "Разработка грунта экскаватором"),
            ("ЗМЛ-002", "Разработка грунта вручную"),
            ("ЗМЛ-003", "Обратная засыпка пазух"),
            ("ЗМЛ-004", "Устройство песчаной подушки"),
            ("ЗМЛ-005", "Вывоз грунта"),
        ),
    ),
    (
        "Бетонные и железобетонные работы",
        "м3",
        (
            ("БЖБ-001", "Устройство бетонной подготовки"),
            ("БЖБ-002", "Бетонирование фундаментной плиты"),
            ("БЖБ-003", "Бетонирование стен и колонн"),
            ("БЖБ-004", "Бетонирование перекрытий"),
            ("БЖБ-005", "Бетонирование лестниц и шахт лифтов"),
        ),
    ),
    (
        "Арматурные и опалубочные работы",
        "т",
        (
            ("АРМ-001", "Вязка арматурных каркасов"),
            ("АРМ-002", "Установка арматуры перекрытий"),
            ("АРМ-003", "Монтаж опалубки стен"),
            ("АРМ-004", "Монтаж опалубки перекрытий"),
        ),
    ),
    (
        "Кладка и перегородки",
        "м2",
        (
            ("КЛД-001", "Кладка наружных стен"),
            ("КЛД-002", "Кладка внутренних перегородок"),
            ("КЛД-003", "Устройство гипсокартонных перегородок"),
            ("КЛД-004", "Утепление фасада"),
        ),
    ),
    (
        "Кровля и гидроизоляция",
        "м2",
        (
            ("КРВ-001", "Устройство пароизоляции"),
            ("КРВ-002", "Устройство утеплителя кровли"),
            ("КРВ-003", "Устройство рулонной кровли"),
            ("КРВ-004", "Гидроизоляция фундамента"),
        ),
    ),
    (
        "Отделочные работы",
        "м2",
        (
            ("ОТД-001", "Штукатурка стен"),
            ("ОТД-002", "Стяжка пола"),
            ("ОТД-003", "Шпатлёвка и окраска стен"),
            ("ОТД-004", "Укладка плитки"),
        ),
    ),
    (
        "Инженерные системы",
        "п.м.",
        (
            ("ИНЖ-001", "Монтаж стояков водоснабжения"),
            ("ИНЖ-002", "Монтаж канализации"),
            ("ИНЖ-003", "Прокладка электрических кабелей"),
            ("ИНЖ-004", "Монтаж системы отопления"),
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class TreeReport:
    cards: int = 0
    works: int = 0
    nodes: int = 0


def _scalar(connection: Connection, sql: str, **params: Any) -> Any:
    return connection.execute(text(sql), params).scalar_one_or_none()


def ensure_project_card(
    connection: Connection, project_id: Any, key: str, counterparties: dict[str, Any]
) -> bool:
    """Заполняет карточку объекта, пока в ней нет дат (то есть один раз): стадия и даты в project,
    бюджет, заказчик и адрес - в project_finance (их видят только роли с finance:read)."""
    stage, start, end, budget, address, lat, lon, customer = PROJECT_CARDS[key]
    result = connection.execute(
        text(
            "UPDATE project SET lifecycle_stage = :stage, planned_start = CAST(:start AS date),"
            " planned_end = CAST(:end AS date)"
            " WHERE id = :id AND planned_start IS NULL AND planned_end IS NULL"
        ),
        {"stage": stage, "start": start, "end": end, "id": project_id},
    )
    if not result.rowcount:
        return False
    connection.execute(
        text(
            "INSERT INTO project_finance (project_id, legal_entity_id, planned_budget, address,"
            " latitude, longitude, customer_id)"
            " SELECT id, legal_entity_id, CAST(:budget AS numeric), :address,"
            " CAST(:lat AS numeric), CAST(:lon AS numeric), :customer FROM project WHERE id = :id"
            " ON CONFLICT (project_id) DO NOTHING"
        ),
        {
            "budget": budget,
            "address": address,
            "lat": lat,
            "lon": lon,
            "customer": counterparties[customer] if customer else None,
            "id": project_id,
        },
    )
    return True


def ensure_works(connection: Connection) -> int:
    created = 0
    for category, unit, items in WORKS:
        for code, name in items:
            exists = _scalar(
                connection,
                "SELECT id FROM work WHERE code = :code AND deleted_at IS NULL",
                code=code,
            )
            if exists is None:
                connection.execute(
                    text(
                        "INSERT INTO work (code, name, unit, category)"
                        " VALUES (:code, :name, :unit, :category)"
                    ),
                    {"code": code, "name": name, "unit": unit, "category": category},
                )
                created += 1
    return created


def _rectangle(x: float, y: float, width: float, height: float) -> str:
    return json.dumps([[x, y], [x + width, y], [x + width, y + height], [x, y + height]])


def ensure_tree(
    connection: Connection,
    project_id: Any,
    legal_entity_id: Any,
    *,
    foreman_id: Any,
    master_id: Any,
) -> int:
    """2 очереди x 2 корпуса x 2 секции x 16 этажей x 8 помещений (1166 узлов) пустому объекту."""
    if _scalar(
        connection,
        "SELECT id FROM project_node WHERE project_id = :id AND deleted_at IS NULL LIMIT 1",
        id=project_id,
    ):
        return 0

    insert = text(
        "INSERT INTO project_node (id, project_id, legal_entity_id, parent_id, type, name, code,"
        " sort_order, foreman_user_id, master_user_id, planned_start, planned_end, polygon)"
        " VALUES (:id, :project, :entity, :parent, :type, :name, :code, :sort, :foreman, :master,"
        " CAST(:start AS date), CAST(:end AS date), CAST(:polygon AS jsonb))"
    )
    levels: list[list[dict[str, Any]]] = [[], [], [], [], []]

    def add(
        level: int, parent: Any, kind: str, name: str, code: str, sort: int, **extra: Any
    ) -> Any:
        node_id = uuid.uuid4()
        levels[level].append(
            {
                "id": node_id,
                "project": project_id,
                "entity": legal_entity_id,
                "parent": parent,
                "type": kind,
                "name": name,
                "code": code,
                "sort": sort,
                "foreman": None,
                "master": None,
                "start": None,
                "end": None,
                "polygon": None,
                **extra,
            }
        )
        return node_id

    room_number = 0
    for stage_index in range(STAGES):
        stage = add(
            0, None, "stage", f"Очередь {stage_index + 1}", f"О{stage_index + 1}", stage_index
        )
        for building_index in range(BUILDINGS_PER_STAGE):
            slot = stage_index * BUILDINGS_PER_STAGE + building_index
            building = add(
                1,
                stage,
                "building",
                f"Корпус {slot + 1}",
                f"К{slot + 1}",
                building_index,
                foreman=foreman_id,
                start=f"{2025 + stage_index}-0{3 + building_index * 3}-01",
                end=f"{2027 + stage_index}-1{building_index}-30",
                polygon=_rectangle(20 + slot * 240, 20, 220, 140),
            )
            for section_index in range(SECTIONS_PER_BUILDING):
                section = add(
                    2,
                    building,
                    "section",
                    f"Секция {'АБ'[section_index]}",
                    "АБ"[section_index],
                    section_index,
                    master=master_id if section_index == 0 else None,
                    polygon=_rectangle(20 + slot * 240 + section_index * 110, 20, 110, 140),
                )
                room_number = 0
                for floor_index in range(FLOORS_PER_SECTION):
                    floor = add(
                        3,
                        section,
                        "floor",
                        f"Этаж {floor_index + 1}",
                        str(floor_index + 1),
                        floor_index,
                    )
                    for room_index in range(ROOMS_PER_FLOOR):
                        room_number += 1
                        add(
                            4,
                            floor,
                            "room",
                            f"Кв. {room_number}",
                            f"{room_index + 1:02d}",
                            room_index,
                        )
    # Уровень за уровнем: триггер БД ищет родителя в таблице, значит, он уже должен существовать.
    total = 0
    for rows in levels:
        connection.execute(insert, rows)
        total += len(rows)
    return total
