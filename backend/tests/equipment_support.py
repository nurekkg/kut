"""Общие помощники тестов техники: вход, единицы, назначения, ставки, листы и SQL-проверки доступа.

Даты назначений - в окне 2031 года, чтобы не пересекаться с демо-данными devseed (там сроки
документов до 2027 года). Даты листов и ставок - в прошлом: лист не может быть позже сегодня, а
ставка действует с RATE_FROM."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.context import RequestContext, use_context
from app.core.db import get_sessionmaker
from app.modules.auth import devseed
from tests.auth_support import AuthWorld, bearer, token_for

API = "/api/v1/equipment"
BASE = date(2031, 3, 1)
PAST = date(2026, 6, 1)
RATE_FROM = "2026-04-01"


def day(offset: int) -> str:
    return (BASE + timedelta(days=offset)).isoformat()


def past(offset: int) -> str:
    return (PAST + timedelta(days=offset)).isoformat()


def idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid4())}


async def headers_for(client: AsyncClient, who: str) -> dict[str, str]:
    return bearer(await token_for(client, who))


async def type_id(client: AsyncClient, headers: dict[str, str], code: str) -> str:
    types = (await client.get(f"{API}/types", headers=headers)).json()
    return next(item["id"] for item in types if item["code"] == code)


async def new_unit(
    client: AsyncClient,
    headers: dict[str, str],
    auth_world: AuthWorld,
    *,
    code: str = "SAM",
    ownership: str = "own",
    contract_id: str | None = None,
    number: str | None = None,
    legal_entity: UUID | None = None,
) -> dict[str, Any]:
    """Единица с документами до 2035 года: назначения в окне 2031 года проходят проверку сроков."""
    body = {
        "legal_entity_id": str(legal_entity or auth_world.legal_entities[devseed.LE_EAST]),
        "equipment_type_id": await type_id(client, headers, code),
        "ownership": ownership,
        "registry_number": number or f"T-{uuid4().hex[:10].upper()}",
        "contract_id": contract_id,
    }
    response = await client.post(f"{API}/units", json=body, headers={**headers, **idem()})
    assert response.status_code == 201, response.text
    unit = response.json()
    for kind in ("technical_inspection", "insurance", "passport"):
        document = await client.post(
            f"{API}/units/{unit['id']}/documents",
            json={"kind": kind, "number": f"D-{kind}", "valid_until": "2035-12-31"},
            headers={**headers, **idem()},
        )
        assert document.status_code == 201, document.text
    return unit


async def assign(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    unit_id: str,
    project_id: UUID,
    start: int,
    end: int,
    hours: str,
    operator_id: str | None = None,
    request_id: str | None = None,
) -> Response:
    body: dict[str, Any] = {
        "equipment_unit_id": unit_id,
        "project_id": str(project_id),
        "date_from": day(start),
        "date_to": day(end),
        "planned_hours_per_day": hours,
        "operator_id": operator_id,
        "request_id": request_id,
    }
    return await client.post(f"{API}/assignments", json=body, headers={**headers, **idem()})


def east(auth_world: AuthWorld) -> UUID:
    return auth_world.projects["east"]


def west(auth_world: AuthWorld) -> UUID:
    return auth_world.projects["west"]


# --- ставки, договоры, машинисты --------------------------------------------------------------


async def new_type(client: AsyncClient, head: dict[str, str]) -> str:
    """Свой тип на тест: норматив и тариф привязаны к типу, чужие ставки его не задевают."""
    code = f"T{uuid4().hex[:8].upper()}"
    response = await client.post(
        f"{API}/types",
        json={"code": code, "name": f"Тип {code}", "default_unit": "hour"},
        headers={**head, **idem()},
    )
    assert response.status_code == 201, response.text
    return code


async def approved_own_rate(
    client: AsyncClient,
    head: dict[str, str],
    finance: dict[str, str],
    legal_entity: UUID,
    code: str,
    *,
    unit: str = "hour",
    amount: str = "1000.00",
) -> None:
    proposal = await client.post(
        f"{API}/own-rates",
        json={
            "legal_entity_id": str(legal_entity),
            "equipment_type_id": await type_id(client, head, code),
            "unit": unit,
            "amount": amount,
            "valid_from": RATE_FROM,
        },
        headers={**head, **idem()},
    )
    assert proposal.status_code == 201, proposal.text
    approved = await client.post(
        f"{API}/own-rates/{proposal.json()['id']}/approve", headers=finance
    )
    assert approved.status_code == 200, approved.text


async def lessor_contract(
    client: AsyncClient,
    finance: dict[str, str],
    legal_entity: UUID,
    lessor: UUID,
    rates: list[dict[str, Any]],
) -> str:
    created = await client.post(
        f"{API}/contracts",
        json={
            "legal_entity_id": str(legal_entity),
            "counterparty_id": str(lessor),
            "kind": "rental_equipment",
            "number": f"АР-{uuid4().hex[:8]}",
            "date_from": RATE_FROM,
        },
        headers={**finance, **idem()},
    )
    assert created.status_code == 201, created.text
    contract_id = created.json()["id"]
    active = await client.patch(
        f"{API}/contracts/{contract_id}", json={"status": "active"}, headers=finance
    )
    assert active.status_code == 200, active.text
    for rate in rates:
        response = await client.post(
            f"{API}/contracts/{contract_id}/rates",
            json={"valid_from": RATE_FROM, **rate},
            headers={**finance, **idem()},
        )
        assert response.status_code == 201, response.text
    return contract_id


async def lessor_operator(
    client: AsyncClient, head: dict[str, str], legal_entity: UUID, lessor: UUID, name: str
) -> str:
    created = await client.post(
        f"{API}/operators",
        json={
            "legal_entity_id": str(legal_entity),
            "full_name": name,
            "employment": "lessor",
            "counterparty_id": str(lessor),
        },
        headers={**head, **idem()},
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def own_operator(client: AsyncClient, machinist: dict[str, str]) -> str:
    """Штатный оператор машиниста из devseed: единственная строка, которую он видит."""
    listed = (await client.get(f"{API}/operators", headers=machinist)).json()
    return str(listed[0]["id"])


def le_east(auth_world: AuthWorld) -> UUID:
    return auth_world.legal_entities[devseed.LE_EAST]


def lessor(auth_world: AuthWorld, letter: str) -> UUID:
    return auth_world.counterparties[f"Арендодатель {letter} (dev)"]


async def own_unit(
    client: AsyncClient, auth_world: AuthWorld, head: dict[str, str], finance: dict[str, str]
) -> dict[str, Any]:
    """Своя единица со своим типом и нормативом 1000,00 за час (утверждён финдиректором)."""
    code = await new_type(client, head)
    await approved_own_rate(client, head, finance, le_east(auth_world), code)
    return await new_unit(client, head, auth_world, code=code)


# --- листы --------------------------------------------------------------------------------------


def shift_body(
    *,
    unit_id: Any,
    project_id: Any,
    work_date: str,
    operator_id: Any,
    hours: str = "8",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "equipment_unit_id": str(unit_id),
        "project_id": str(project_id),
        "work_date": work_date,
        "operator_id": str(operator_id),
        "hours": hours,
        **extra,
    }


async def post_shift(
    client: AsyncClient, headers: dict[str, str], body: dict[str, Any]
) -> Response:
    return await client.post(f"{API}/shifts", json=body, headers={**headers, **idem()})


async def move(client: AsyncClient, headers: dict[str, str], shift_id: str, step: str) -> Response:
    return await client.post(f"{API}/shifts/{shift_id}/{step}", headers=headers)


# --- доступ и SQL-проверки ----------------------------------------------------------------------


def in_context(
    user_id: UUID, projects: tuple[UUID, ...], entities: tuple[UUID, ...]
) -> RequestContext:
    return RequestContext(
        user_id=user_id,
        is_internal=True,
        project_ids=frozenset(projects),
        legal_entity_ids=frozenset(entities),
        counterparty_id=None,
        request_id=None,
        sees_all_users=False,
    )


def context(
    auth_world: AuthWorld, who: str, projects: tuple[UUID, ...], entities: tuple[UUID, ...]
) -> RequestContext:
    return in_context(auth_world.users[who], projects, entities)


async def rows(
    sql: str, ctx: RequestContext, params: dict[str, Any] | None = None
) -> list[tuple[Any, ...]]:
    """Запрос от имени приложения (kut_app) в контексте пользователя: RLS не обходится."""
    with use_context(ctx):
        async with get_sessionmaker()() as session:
            return [tuple(row) for row in await session.execute(text(sql), params or {})]


def second_project(engine: Engine, legal_entity: UUID) -> UUID:
    """Второй объект того же юрлица: лимит часов единицы считается по всем объектам юрлица."""
    with engine.begin() as connection:
        return connection.execute(
            text(
                "INSERT INTO project (id, legal_entity_id, name)"
                " VALUES (gen_random_uuid(), :le, :name) RETURNING id"
            ),
            {"le": legal_entity, "name": f"Объект тест {uuid4().hex[:8]}"},
        ).scalar_one()


def grant(
    engine: Engine,
    user_id: UUID,
    *,
    projects: tuple[UUID, ...] = (),
    entities: tuple[UUID, ...] = (),
) -> None:
    with engine.begin() as connection:
        for project in projects:
            connection.execute(
                text("INSERT INTO user_project_access (user_id, project_id) VALUES (:u, :p)"),
                {"u": user_id, "p": project},
            )
        for entity in entities:
            connection.execute(
                text(
                    "INSERT INTO user_legal_entity_access (user_id, legal_entity_id)"
                    " VALUES (:u, :l)"
                ),
                {"u": user_id, "l": entity},
            )


async def signed_in(
    client: AsyncClient,
    make_user: Any,
    engine: Engine,
    role: str,
    *,
    projects: tuple[UUID, ...] = (),
    entities: tuple[UUID, ...] = (),
) -> tuple[UUID, dict[str, str]]:
    """Временный пользователь роли с выданным доступом; возвращает (id, заголовки входа)."""
    user_id, email = make_user(role)
    grant(engine, user_id, projects=projects, entities=entities)
    return user_id, bearer(await token_for(client, email))


__all__ = [
    "API",
    "RATE_FROM",
    "approved_own_rate",
    "assign",
    "context",
    "day",
    "east",
    "grant",
    "headers_for",
    "idem",
    "in_context",
    "le_east",
    "lessor",
    "lessor_contract",
    "lessor_operator",
    "move",
    "new_type",
    "new_unit",
    "own_operator",
    "own_unit",
    "past",
    "post_shift",
    "rows",
    "second_project",
    "shift_body",
    "signed_in",
    "type_id",
    "west",
]
