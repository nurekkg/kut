"""Общие помощники тестов техники: вход, единицы, назначения. Даты - в окне 2031 года, чтобы не
пересекаться с демо-данными devseed (там сроки документов до 2027 года)."""

from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

from httpx import AsyncClient, Response

from app.modules.auth import devseed
from tests.auth_support import AuthWorld, bearer, token_for

API = "/api/v1/equipment"
BASE = date(2031, 3, 1)


def day(offset: int) -> str:
    return (BASE + timedelta(days=offset)).isoformat()


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
) -> dict[str, Any]:
    """Единица с документами до 2035 года: назначения в окне 2031 года проходят проверку сроков."""
    body = {
        "legal_entity_id": str(auth_world.legal_entities[devseed.LE_EAST]),
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


__all__ = ["API", "assign", "day", "east", "headers_for", "idem", "new_unit", "type_id", "west"]
