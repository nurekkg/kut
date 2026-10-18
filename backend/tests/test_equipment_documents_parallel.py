"""F2 (ADR-015 часть 3, раздел 13): параллельные добавления документа техники дают
непрерывные версии.

Без блокировки строки техники два одновременных добавления одного вида получают одну и ту же
версию и падают на уникальном индексе. С блокировкой они ждут друг друга и идут по порядку.
"""

import asyncio

from httpx import AsyncClient

from tests.auth_support import AuthWorld
from tests.equipment_support import API, headers_for, idem, new_unit


async def test_parallel_additions_of_one_kind_get_contiguous_versions(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    unit = await new_unit(api, head, auth_world)
    responses = await asyncio.gather(
        *(
            api.post(
                f"{API}/units/{unit['id']}/documents",
                json={"kind": "permit", "number": f"P-{index}", "valid_until": "2035-12-31"},
                headers={**head, **idem()},
            )
            for index in range(20)
        )
    )
    assert [response.status_code for response in responses] == [201] * 20, [
        response.text for response in responses if response.status_code != 201
    ]
    assert sorted(response.json()["version"] for response in responses) == list(range(1, 21))


async def test_parallel_inspections_by_the_mechanic_get_contiguous_versions(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    mechanic = await headers_for(api, "mechanic")
    unit = await new_unit(api, head, auth_world)
    seeded = await api.get(f"{API}/units/{unit['id']}/documents", headers=head)
    last = max(item["version"] for item in seeded.json() if item["kind"] == "technical_inspection")
    responses = await asyncio.gather(
        *(
            api.post(
                f"{API}/units/{unit['id']}/documents",
                json={
                    "kind": "technical_inspection",
                    "number": f"ТО-{index}",
                    "valid_until": "2035-12-31",
                },
                headers={**mechanic, **idem()},
            )
            for index in range(20)
        )
    )
    assert [response.status_code for response in responses] == [201] * 20, [
        response.text for response in responses if response.status_code != 201
    ]
    assert sorted(response.json()["version"] for response in responses) == list(
        range(last + 1, last + 21)
    )
