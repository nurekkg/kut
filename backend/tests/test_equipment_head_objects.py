"""Начальник отдела спецтехники видит объекты (objects:read), но не бюджет (решение PO)."""

from httpx import AsyncClient

from tests.auth_support import AuthWorld
from tests.equipment_support import east, headers_for


async def test_the_head_reads_objects_but_not_their_budget(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    listed = await api.get("/api/v1/projects", headers=head)
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == str(east(auth_world)) for item in listed.json())
    card = await api.get(f"/api/v1/projects/{east(auth_world)}", headers=head)
    assert card.status_code == 200, card.text
    assert card.json().get("planned_budget") is None
