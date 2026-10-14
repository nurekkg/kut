"""Демо-техника для разработки и тестов: ~300 единиц, три договора аренды с разными тарифами,
машинисты штатные и от арендодателя, заявки и назначения на «Восток» и «Запад», ТО и документы со
сроками. Вызывается из `app.modules.auth.devseed.seed` под kut_worker; повтор ничего не меняет.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

TYPES = (
    ("EXC", "Экскаватор гусеничный", "hour"),
    ("BUL", "Бульдозер", "hour"),
    ("SAM", "Самосвал", "trip"),
    ("KRN", "Кран автомобильный", "hour"),
    ("LOA", "Погрузчик фронтальный", "motor_hour"),
    ("GEN", "Генератор", "motor_hour"),
    ("KMP", "Компрессор", "shift"),
    ("CAT", "Каток", "hour"),
    ("BNS", "Бетононасос", "hour"),
    ("MIX", "Бетономешалка", "shift"),
)
OWN_EAST = 120
OWN_WEST = 60
RENTED = (("A", 60), ("B", 30), ("C", 30))  # префикс, количество: по одному договору на префикс
FAR_FUTURE = date(2035, 12, 31)
DOCS_VALID = date(2027, 6, 30)
TAX_IDS = {"east": "DEV-EAST-001", "west": "DEV-WEST-001"}
CONTRACTS = (
    # (ключ, юрлицо, арендодатель, номер, тарифы: (единица, с машинистом, сумма, минимум))
    (
        "A",
        "east",
        "Арендодатель А (dev)",
        "АР-ВОСТОК-01",
        (
            ("hour", False, "2200", None),
            ("hour", True, "3400", "40"),
        ),
    ),
    (
        "B",
        "east",
        "Арендодатель Б (dev)",
        "АР-ВОСТОК-02",
        (("trip", False, "2500", "10"),),
    ),
    (
        "C",
        "west",
        "Арендодатель В (dev)",
        "АР-ЗАПАД-01",
        (
            ("month", False, "320000", None),
            ("hour", True, "3100", "40"),
        ),
    ),
)
RATE_FROM = date(2026, 1, 1)
# Тариф уровня единицы (prefix, номер в префиксе, единица, с машинистом, сумма, минимум): у одной
# арендованной единицы договора А смена без машиниста, остальные - часы на уровне договора.
UNIT_RATES = (("A", 0, "shift", False, "14000", None),)


def _scalar(connection: Connection, sql: str, **params: Any) -> Any:
    return connection.execute(text(sql), params).scalar_one_or_none()


def _count(connection: Connection, table: str) -> int:
    return int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608


def _types(connection: Connection) -> dict[str, uuid.UUID]:
    for code, name, unit in TYPES:
        connection.execute(
            text(
                "INSERT INTO equipment_type (id, code, name, default_unit) VALUES"
                " (:id, :code, :name, :unit) ON CONFLICT (code) WHERE deleted_at IS NULL DO NOTHING"
            ),
            {"id": uuid.uuid4(), "code": code, "name": name, "unit": unit},
        )
    rows = connection.execute(text("SELECT code, id FROM equipment_type WHERE deleted_at IS NULL"))
    return {code: type_id for code, type_id in rows}


def _tax_ids(
    connection: Connection, legal_entities: dict[str, uuid.UUID], keys: dict[str, str]
) -> None:
    for key, tax_id in TAX_IDS.items():
        connection.execute(
            text("UPDATE legal_entity SET tax_id = :tax WHERE id = :id AND tax_id IS NULL"),
            {"tax": tax_id, "id": legal_entities[keys[key]]},
        )


def _contracts(
    connection: Connection,
    legal_entities: dict[str, uuid.UUID],
    counterparties: dict[str, uuid.UUID],
    keys: dict[str, str],
) -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    for key, entity, lessor, number, rates in CONTRACTS:
        legal_id = legal_entities[keys[entity]]
        contract_id = _scalar(
            connection,
            "SELECT id FROM contract WHERE legal_entity_id = :le AND kind = 'rental_equipment'"
            " AND number = :number AND deleted_at IS NULL",
            le=legal_id,
            number=number,
        )
        if contract_id is None:
            contract_id = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO contract (id, legal_entity_id, counterparty_id, kind, number,"
                    " date_from, status) VALUES (:id, :le, :cp, 'rental_equipment', :number,"
                    " :from, 'active')"
                ),
                {
                    "id": contract_id,
                    "le": legal_id,
                    "cp": counterparties[lessor],
                    "number": number,
                    "from": RATE_FROM,
                },
            )
        ids[key] = contract_id
        for unit, with_operator, amount, minimum in rates:
            connection.execute(
                text(
                    "INSERT INTO equipment_rate (id, contract_id, unit, with_operator, amount,"
                    " min_quantity, valid_from) VALUES (:id, :contract, :unit, :op, :amount,"
                    " :minimum, :from) ON CONFLICT (contract_id,"
                    " (COALESCE(equipment_unit_id, '00000000-0000-0000-0000-000000000000'::uuid)),"
                    " (COALESCE(equipment_type_id, '00000000-0000-0000-0000-000000000000'::uuid)),"
                    " unit, with_operator, valid_from) WHERE deleted_at IS NULL DO NOTHING"
                ),
                {
                    "id": uuid.uuid4(),
                    "contract": contract_id,
                    "unit": unit,
                    "op": with_operator,
                    "amount": amount,
                    "minimum": minimum,
                    "from": RATE_FROM,
                },
            )
    return ids


def _units(
    connection: Connection,
    types: dict[str, uuid.UUID],
    legal_entities: dict[str, uuid.UUID],
    keys: dict[str, str],
    contracts: dict[str, uuid.UUID],
) -> dict[str, list[uuid.UUID]]:
    """Единицы по префиксам: E, W - свои (восток, запад), A, B, C - арендованные."""
    rows: list[dict[str, Any]] = []
    plan: list[tuple[str, str, int, str | None]] = [
        ("E", keys["east"], OWN_EAST, None),
        ("W", keys["west"], OWN_WEST, None),
    ]
    for key, count in RENTED:
        entity = "west" if key == "C" else "east"
        plan.append((key, keys[entity], count, key))
    registry: dict[str, list[str]] = {}
    for prefix, entity_key, count, rented_key in plan:
        registry[prefix] = []
        for index in range(count):
            code, _name, _unit = TYPES[index % len(TYPES)]
            number = f"{code}-{prefix}{index:03d}"
            unit_id = uuid.uuid4()
            registry[prefix].append(number.replace("-", ""))
            rows.append(
                {
                    "id": unit_id,
                    "le": legal_entities[entity_key],
                    "type": types[code],
                    "ownership": "rented" if rented_key else "own",
                    "key": number.replace("-", ""),
                    "number": number,
                    "contract": contracts[rented_key] if rented_key else None,
                    "model": f"Модель {code}-{index % 5 + 1}",
                    "year": 2008 + index % 15,
                }
            )
    connection.execute(
        text(
            "INSERT INTO equipment_unit (id, legal_entity_id, equipment_type_id, ownership,"
            " registry_key, registry_number, contract_id, model, year) VALUES (:id, :le, :type,"
            " :ownership, :key, :number, :contract, :model, :year)"
            " ON CONFLICT (legal_entity_id, registry_key)"
            " WHERE deleted_at IS NULL DO NOTHING"
        ),
        rows,
    )
    # Повторный прогон не создаёт единиц: идентификаторы берём из базы по регистрационному ключу.
    stored = {
        key: unit_id
        for key, unit_id in connection.execute(
            text(
                "SELECT registry_key, id FROM equipment_unit"
                " WHERE registry_key = ANY(CAST(:keys AS varchar[])) AND deleted_at IS NULL"
            ),
            {"keys": [key for keys_ in registry.values() for key in keys_]},
        )
    }
    return {prefix: [stored[key] for key in keys_] for prefix, keys_ in registry.items()}


def _unit_rates(
    connection: Connection, contracts: dict[str, uuid.UUID], units: dict[str, list[uuid.UUID]]
) -> None:
    rows = [
        {
            "id": uuid.uuid4(),
            "contract": contracts[prefix],
            "unit_id": units[prefix][index],
            "unit": unit,
            "op": with_operator,
            "amount": amount,
            "minimum": minimum,
            "from": RATE_FROM,
        }
        for prefix, index, unit, with_operator, amount, minimum in UNIT_RATES
    ]
    connection.execute(
        text(
            "INSERT INTO equipment_rate (id, contract_id, equipment_unit_id, unit, with_operator,"
            " amount, min_quantity, valid_from) VALUES (:id, :contract, :unit_id, :unit, :op,"
            " :amount, :minimum, :from) ON CONFLICT (contract_id,"
            " (COALESCE(equipment_unit_id, '00000000-0000-0000-0000-000000000000'::uuid)),"
            " (COALESCE(equipment_type_id, '00000000-0000-0000-0000-000000000000'::uuid)),"
            " unit, with_operator, valid_from) WHERE deleted_at IS NULL DO NOTHING"
        ),
        rows,
    )


def _documents(connection: Connection, units: dict[str, list[uuid.UUID]], today: date) -> None:
    rows: list[dict[str, Any]] = []
    for prefix, ids in units.items():
        for index, unit_id in enumerate(ids):
            rows.append(_doc(unit_id, "passport", f"ПС-{prefix}{index:03d}", FAR_FUTURE))
            tech = today - timedelta(days=5) if index % 10 == 9 else DOCS_VALID
            rows.append(_doc(unit_id, "technical_inspection", f"ТО-{prefix}{index:03d}", tech))
            ins = today + timedelta(days=20) if index % 7 == 6 else DOCS_VALID
            rows.append(_doc(unit_id, "insurance", f"СТ-{prefix}{index:03d}", ins))
    connection.execute(
        text(
            "INSERT INTO equipment_document (id, legal_entity_id, equipment_unit_id, kind, number,"
            " valid_until, version) SELECT :id, u.legal_entity_id, :unit, :kind, :number, :until, 1"
            " FROM equipment_unit u WHERE u.id = :unit"
            " ON CONFLICT (equipment_unit_id, kind, version) DO NOTHING"
        ),
        rows,
    )


def _doc(unit_id: uuid.UUID, kind: str, number: str, until: date) -> dict[str, Any]:
    return {"id": uuid.uuid4(), "unit": unit_id, "kind": kind, "number": number, "until": until}


def _operators(
    connection: Connection,
    legal_entities: dict[str, uuid.UUID],
    counterparties: dict[str, uuid.UUID],
    keys: dict[str, str],
    machinist_id: uuid.UUID,
) -> None:
    connection.execute(
        text(
            "INSERT INTO equipment_operator (id, legal_entity_id, full_name, employment, user_id)"
            " VALUES (:id, :le, 'Машинист Восток (dev)', 'staff', :user)"
            " ON CONFLICT (user_id) WHERE deleted_at IS NULL AND user_id IS NOT NULL DO NOTHING"
        ),
        {"id": uuid.uuid4(), "le": legal_entities[keys["east"]], "user": machinist_id},
    )
    for lessor, letter, entity_key, count in (
        ("Арендодатель А (dev)", "А", "east", 3),
        ("Арендодатель Б (dev)", "Б", "east", 3),
        ("Арендодатель В (dev)", "В", "west", 2),
    ):
        entity = legal_entities[keys[entity_key]]
        for index in range(1, count + 1):
            name = f"Машинист {letter}-{index} (dev)"
            connection.execute(
                text(
                    "INSERT INTO equipment_operator (id, legal_entity_id, full_name, employment,"
                    " counterparty_id) SELECT :id, :le, CAST(:name AS varchar), 'lessor', :cp"
                    " WHERE NOT EXISTS (SELECT 1 FROM equipment_operator"
                    " WHERE full_name = :name AND deleted_at IS NULL)"
                ),
                {"id": uuid.uuid4(), "le": entity, "name": name, "cp": counterparties[lessor]},
            )


def _own_rates(
    connection: Connection,
    types: dict[str, uuid.UUID],
    legal_entities: dict[str, uuid.UUID],
    keys: dict[str, str],
    users: dict[str, uuid.UUID],
) -> None:
    head, finance = users["equipment_head"], users["finance"]
    rows = (
        (keys["east"], "EXC", "hour", "1800", finance),
        (keys["east"], "BUL", "hour", "1600", None),
        (keys["west"], "KRN", "hour", "2100", finance),
    )
    for entity, code, unit, amount, approver in rows:
        connection.execute(
            text(
                "INSERT INTO equipment_own_rate (id, legal_entity_id, equipment_type_id, unit,"
                " amount, valid_from, proposed_by, approved_by) VALUES (:id, :le, :type, :unit,"
                " :amount, :from, :proposed, :approved) ON CONFLICT"
                " (legal_entity_id, equipment_type_id, unit, valid_from) WHERE deleted_at IS NULL"
                " DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "le": legal_entities[entity],
                "type": types[code],
                "unit": unit,
                "amount": amount,
                "from": RATE_FROM,
                "proposed": head,
                "approved": approver,
            },
        )


def _plan(
    connection: Connection,
    types: dict[str, uuid.UUID],
    units: dict[str, list[uuid.UUID]],
    projects: dict[str, uuid.UUID],
    legal_entities: dict[str, uuid.UUID],
    keys: dict[str, str],
    users: dict[str, uuid.UUID],
    operators: dict[str, uuid.UUID],
    today: date,
) -> dict[str, int]:
    """Заявки, назначения, ТО: только если таблица пуста (без натуральных ключей)."""
    created: dict[str, int] = {}
    if _count(connection, "equipment_request") or _count(connection, "equipment_maintenance"):
        return created
    east, west = projects["east"], projects["west"]
    e0, e2, w5 = units["E"][0], units["E"][2], units["W"][5]
    sam_rented = units["A"][2]  # самосвал арендованный по договору A
    requests = [
        (
            east,
            keys["east"],
            types["EXC"],
            today - timedelta(days=3),
            today + timedelta(days=10),
            "8",
            "assigned",
            None,
        ),
        (
            east,
            keys["east"],
            types["SAM"],
            today,
            today + timedelta(days=5),
            "8",
            "submitted",
            None,
        ),
        (
            east,
            keys["east"],
            types["KRN"],
            today + timedelta(days=1),
            today + timedelta(days=4),
            "10",
            "rejected",
            "Нет крана на эти даты",
        ),
        (
            west,
            keys["west"],
            types["BUL"],
            today + timedelta(days=2),
            today + timedelta(days=9),
            "8",
            "submitted",
            None,
        ),
    ]
    request_ids: list[uuid.UUID] = []
    for project, entity, type_id, date_from, date_to, hours, status, reason in requests:
        request_id = uuid.uuid4()
        request_ids.append(request_id)
        connection.execute(
            text(
                "INSERT INTO equipment_request (id, project_id, legal_entity_id, equipment_type_id,"
                " date_from, date_to, planned_hours_per_day, status, reject_reason)"
                " VALUES (:id, :project, :le, :type, :from, :to, :hours, :status, :reason)"
            ),
            {
                "id": request_id,
                "project": project,
                "le": legal_entities[entity],
                "type": type_id,
                "from": date_from,
                "to": date_to,
                "hours": hours,
                "status": status,
                "reason": reason,
            },
        )
    created["equipment_request"] = len(requests)
    assignments: list[tuple[Any, ...]] = [
        (
            east,
            e0,
            request_ids[0],
            None,
            today - timedelta(days=3),
            today + timedelta(days=10),
            "8",
            operators["staff"],
        ),
        (
            east,
            sam_rented,
            None,
            None,
            today - timedelta(days=2),
            today + timedelta(days=5),
            "8",
            operators["lessor_a"],
        ),
        (east, e2, None, None, today, today + timedelta(days=5), "8", None),
        (west, e2, None, None, today, today + timedelta(days=5), "12", None),
    ]
    for project, unit, request_id, _node, date_from, date_to, hours, operator in assignments:
        connection.execute(
            text(
                "INSERT INTO equipment_assignment (id, project_id, legal_entity_id,"
                " equipment_unit_id,"
                " request_id, date_from, date_to, planned_hours_per_day, operator_id, status)"
                " VALUES (:id, :project, (SELECT legal_entity_id FROM project WHERE id = :project),"
                " :unit, :request, :from, :to, :hours, :operator, 'planned')"
            ),
            {
                "id": uuid.uuid4(),
                "project": project,
                "unit": unit,
                "request": request_id,
                "from": date_from,
                "to": date_to,
                "hours": hours,
                "operator": operator,
            },
        )
    created["equipment_assignment"] = len(assignments)
    maintenance: list[tuple[Any, ...]] = [
        (w5, "repair", "Замена гидравлики", today - timedelta(days=2), None, "in_progress"),
        (
            units["W"][0],
            "to",
            "ТО-500",
            today + timedelta(days=3),
            today + timedelta(days=3),
            "planned",
        ),
        (units["W"][1], "to", "ТО-250", today + timedelta(days=6), None, "planned"),
        (
            units["E"][50],
            "repair",
            "Ремонт ходовой",
            today - timedelta(days=30),
            today - timedelta(days=20),
            "done",
        ),
    ]
    for unit, kind, title, date_from, date_to, status in maintenance:
        maintenance_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO equipment_maintenance (id, legal_entity_id, equipment_unit_id, kind,"
                " title, planned_from, planned_to, status, done_at) VALUES (:id, (SELECT"
                " legal_entity_id FROM equipment_unit WHERE id = :unit), :unit, :kind, :title,"
                " :from, :to, :status, CASE WHEN CAST(:status AS varchar) = 'done' THEN now() END)"
            ),
            {
                "id": maintenance_id,
                "unit": unit,
                "kind": kind,
                "title": title,
                "from": date_from,
                "to": date_to,
                "status": "in_progress" if status == "done" else status,
            },
        )
        if status == "done":
            # Закрытие - через определитель (сумма - в журнал); INSERT в done отказан БД.
            connection.execute(
                text("SELECT app.equipment_maintenance_close(:id, NULL, CAST(:amount AS numeric))"),
                {"id": maintenance_id, "amount": "18500.00"},
            )
    created["equipment_maintenance"] = len(maintenance)
    return created


def ensure_equipment(
    connection: Connection,
    *,
    legal_entities: dict[str, uuid.UUID],
    counterparties: dict[str, uuid.UUID],
    projects: dict[str, uuid.UUID],
    users: dict[str, uuid.UUID],
    keys: dict[str, str],
    today: date,
) -> dict[str, int]:
    before = {
        table: _count(connection, table)
        for table in (
            "equipment_type",
            "equipment_unit",
            "contract",
            "equipment_rate",
            "equipment_document",
        )
    }
    types = _types(connection)
    _tax_ids(connection, legal_entities, keys)
    contracts = _contracts(connection, legal_entities, counterparties, keys)
    units = _units(connection, types, legal_entities, keys, contracts)
    _unit_rates(connection, contracts, units)
    _documents(connection, units, today)
    _operators(connection, legal_entities, counterparties, keys, users["machinist"])
    _own_rates(connection, types, legal_entities, keys, users)
    operators = {
        "staff": connection.execute(
            text("SELECT id FROM equipment_operator WHERE user_id = :user"),
            {"user": users["machinist"]},
        ).scalar_one(),
        "lessor_a": connection.execute(
            text(
                "SELECT o.id FROM equipment_operator o"
                " JOIN counterparty c ON c.id = o.counterparty_id"
                " WHERE c.name = 'Арендодатель А (dev)' AND o.employment = 'lessor' LIMIT 1"
            )
        ).scalar_one(),
    }
    plan = _plan(connection, types, units, projects, legal_entities, keys, users, operators, today)
    after = {table: _count(connection, table) for table in before}
    created = {table: after[table] - before[table] for table in before}
    created.update(plan)
    return {table: count for table, count in created.items() if count}


# Второй объект юрлица: на нём работают машинист и второй прораб (сид пользователей).
SECOND_EAST = "ЖК «Восток-2» (dev)"
# (объект, юрлицо, единица, день назад, оператор, часы, автор, состояние, причина отказа)
# Аренда - с машинистом арендодателя (часовой тариф договора). Для одной единицы договора А без
# машиниста задан тариф смены на уровне единицы: он выигрывает у часа договора по приоритету.
SHIFT_PLAN = (
    ("east", "east", "own_east", 1, "staff", "8", "foreman", "confirmed", None),
    ("east", "east", "own_east", 2, "staff", "6", "foreman", "submitted", None),
    ("east", "east", "own_east", 3, "staff", "7", "foreman", "rejected", "не совпадает с табелем"),
    ("east", "east", "own_east", 4, "staff", "5", "foreman", "draft", None),
    ("second", "east", "own_east", 1, "staff", "6", "foreman", "confirmed", None),
    ("east", "east", "rented_a", 2, "lessor_a", "10", "foreman", "confirmed", None),
    ("east", "east", "rented_a", 3, "lessor_a", "6", "foreman", "submitted", None),
    ("west", "west", "rented_c", 1, "lessor_west", "9", None, "confirmed", None),
    ("west", "west", "rented_c", 2, "lessor_west", "8", None, "submitted", None),
    ("west", "west", "rented_c", 3, "lessor_west", "7", None, "rejected", "нет подписи прораба"),
    ("west", "west", "rented_c", 4, "lessor_west", "5", None, "draft", None),
)


def _own_unit(connection: Connection, legal_entity: uuid.UUID) -> uuid.UUID:
    """Своя единица юрлица, у типа которой есть утверждённый норматив за час."""
    unit: uuid.UUID = connection.execute(
        text(
            "SELECT u.id FROM equipment_unit u JOIN equipment_own_rate r"
            " ON r.equipment_type_id = u.equipment_type_id"
            " AND r.legal_entity_id = u.legal_entity_id"
            " WHERE u.legal_entity_id = :le AND u.ownership = 'own' AND u.deleted_at IS NULL"
            " AND r.unit = 'hour' AND r.approved_by IS NOT NULL AND r.deleted_at IS NULL"
            " ORDER BY u.registry_number LIMIT 1"
        ),
        {"le": legal_entity},
    ).scalar_one()
    return unit


def _rented_unit(connection: Connection, contract_number: str) -> uuid.UUID:
    unit: uuid.UUID = connection.execute(
        text(
            "SELECT u.id FROM equipment_unit u JOIN contract c ON c.id = u.contract_id"
            " WHERE c.number = :number AND u.deleted_at IS NULL"
            " ORDER BY u.registry_number LIMIT 1"
        ),
        {"number": contract_number},
    ).scalar_one()
    return unit


def _machinist_fuel(connection: Connection, shift_id: uuid.UUID, machinist: uuid.UUID) -> None:
    """Заправка машиниста в его листе до подтверждения: в журнал попадёт при проводке листа."""
    connection.execute(
        text(
            "INSERT INTO equipment_fuel (id, legal_entity_id, project_id, equipment_unit_id,"
            " fuel_date, liters, amount, shift_id, created_by)"
            " SELECT gen_random_uuid(), s.legal_entity_id, s.project_id, s.equipment_unit_id,"
            " s.work_date, :liters, :amount, s.id, :machinist FROM equipment_shift s"
            " WHERE s.id = :shift"
        ),
        {
            "liters": Decimal("40.00"),
            "amount": Decimal("3200.00"),
            "shift": shift_id,
            "machinist": machinist,
        },
    )


def _move_shift(
    connection: Connection,
    shift_id: uuid.UUID,
    state: str,
    approver: uuid.UUID | None,
    reason: str | None,
) -> None:
    """Переход листа по правилам БД: черновик -> на подтверждение -> подтверждён или отказан."""
    if state == "draft":
        return
    connection.execute(
        text("UPDATE equipment_shift SET status = 'submitted' WHERE id = :id"), {"id": shift_id}
    )
    if state == "rejected":
        connection.execute(
            text(
                "UPDATE equipment_shift SET status = 'rejected', reject_reason = :reason"
                " WHERE id = :id"
            ),
            {"id": shift_id, "reason": reason},
        )
    elif state == "confirmed":
        connection.execute(
            text(
                "UPDATE equipment_shift SET status = 'confirmed', confirmed_by = :approver"
                " WHERE id = :id"
            ),
            {"id": shift_id, "approver": approver},
        )


def ensure_shifts(
    connection: Connection,
    *,
    today: date,
    east: uuid.UUID,
    west: uuid.UUID,
    east_entity: uuid.UUID,
    west_entity: uuid.UUID,
    users: dict[str, uuid.UUID],
) -> dict[str, int]:
    """Демо-листы SE-2a, только при пустой таблице листов: листы во всех состояниях на «Востоке»
    и «Западе», второй объект юрлица «Восток» (одна машина на двух объектах в один день), ГСМ и
    закрытый ремонт. Проводки пишут триггеры и определители, как в приложении; записи в журнал
    напрямую нет."""
    if connection.execute(text("SELECT count(*) FROM equipment_shift")).scalar_one():
        return {}
    second = connection.execute(
        text("SELECT id FROM project WHERE name = :name AND deleted_at IS NULL"),
        {"name": SECOND_EAST},
    ).scalar_one_or_none()
    if second is None:
        second = connection.execute(
            text(
                "INSERT INTO project (id, legal_entity_id, name)"
                " VALUES (gen_random_uuid(), :le, :name) RETURNING id"
            ),
            {"le": east_entity, "name": SECOND_EAST},
        ).scalar_one()
    projects = {"east": east, "second": second, "west": west}
    units = {
        "own_east": _own_unit(connection, east_entity),
        "rented_a": _rented_unit(connection, "АР-ВОСТОК-01"),
        "rented_c": _rented_unit(connection, "АР-ЗАПАД-01"),
    }
    operators = {
        "staff": connection.execute(
            text("SELECT id FROM equipment_operator WHERE user_id = :user"),
            {"user": users["machinist"]},
        ).scalar_one(),
        "lessor_a": connection.execute(
            text(
                "SELECT o.id FROM equipment_operator o"
                " JOIN counterparty c ON c.id = o.counterparty_id"
                " WHERE c.name = 'Арендодатель А (dev)' AND o.employment = 'lessor'"
                " AND o.deleted_at IS NULL LIMIT 1"
            )
        ).scalar_one(),
        "lessor_west": connection.execute(
            text(
                "SELECT id FROM equipment_operator WHERE legal_entity_id = :le"
                " AND employment = 'lessor' AND deleted_at IS NULL ORDER BY full_name LIMIT 1"
            ),
            {"le": west_entity},
        ).scalar_one(),
    }
    entities = {"east": east_entity, "west": west_entity}
    created = 0
    for project, entity, unit, days, operator, hours, author, state, reason in SHIFT_PLAN:
        if operators[operator] is None:
            continue
        shift_id = connection.execute(
            text(
                "INSERT INTO equipment_shift (id, legal_entity_id, project_id, equipment_unit_id,"
                " work_date, operator_id, hours, source, status, created_by, posting_version,"
                " lines_revision) VALUES (gen_random_uuid(), :le, :project, :unit, :day, :operator,"
                " :hours, 'manual', 'draft', :author, 0, 0) RETURNING id"
            ),
            {
                "le": entities[entity],
                "project": projects[project],
                "unit": units[unit],
                "day": today - timedelta(days=days),
                "operator": operators[operator],
                "hours": Decimal(hours),
                "author": users[author] if author else None,
            },
        ).scalar_one()
        if unit == "own_east" and state == "confirmed" and days == 1:
            _machinist_fuel(connection, shift_id, users["machinist"])
        _move_shift(connection, shift_id, state, users["techdir"], reason)
        created += 1
    for fuel_project, day, amount, liters in (
        (east, 1, "14000.00", "120.50"),
        (None, 2, "2500.00", None),
    ):
        connection.execute(
            text(
                "INSERT INTO equipment_fuel (id, legal_entity_id, project_id, equipment_unit_id,"
                " fuel_date, liters, amount, created_by)"
                " VALUES (gen_random_uuid(), :le, :project, :unit, :day, :liters, :amount,"
                " :author)"
            ),
            {
                "le": east_entity,
                "project": fuel_project,
                "unit": units["own_east"],
                "day": today - timedelta(days=day),
                "liters": Decimal(liters) if liters else None,
                "amount": Decimal(amount),
                "author": users["foreman"],
            },
        )
    repair = connection.execute(
        text(
            "INSERT INTO equipment_maintenance (id, legal_entity_id, equipment_unit_id, kind,"
            " title, planned_from, status) VALUES (gen_random_uuid(), :le, :unit, 'repair',"
            " 'Гидравлика, шланги', :day, 'planned') RETURNING id"
        ),
        {"le": east_entity, "unit": units["own_east"], "day": today - timedelta(days=6)},
    ).scalar_one()
    connection.execute(
        text("UPDATE equipment_maintenance SET status = 'in_progress' WHERE id = :id"),
        {"id": repair},
    )
    connection.execute(
        text("SELECT app.equipment_maintenance_close(:m, :p, :a)"),
        {"m": repair, "p": east, "a": Decimal("45000.00")},
    )
    return {
        "equipment_shift": created,
        "equipment_fuel": 3,
        "equipment_maintenance": 1,
        "project": 1,
    }
