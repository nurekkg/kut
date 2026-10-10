"""S3b: добавления к API ради экранов - готовность в списке ГПР, объём против плана у факта,
фильтры и подписи в списке фото. Всё остальное API прежнее (тесты S3a)."""

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.auth_support import AuthWorld, bearer, token_for
from tests.gpr_support import Plan, make_plan, purge, unique

P = "/api/v1/projects"
AS_OF = "2026-09-06"
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-bytes" * 50


@pytest.fixture(autouse=True)
async def cleanup(worker_engine: AsyncEngine, auth_world: AuthWorld) -> AsyncIterator[None]:
    yield
    await purge(worker_engine, auth_world)


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


def url(world: AuthWorld, section: str, *tail: str) -> str:
    return "/".join([f"{P}/{world.projects['east']}/{section}", *tail])


async def send(
    api: AsyncClient,
    method: str,
    path: str,
    who: str,
    *,
    json: Any = None,
    params: Any = None,
    key: UUID | None = None,
) -> Response:
    headers = bearer(await token_for(api, who))
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    return await api.request(method, path, headers=headers, json=json, params=params)


async def new_fact(
    api: AsyncClient,
    world: AuthWorld,
    who: str,
    plan: Plan,
    *,
    volume: str = "5",
    work_date: str = "2026-09-03",
) -> dict[str, Any]:
    body = {
        "node_id": str(plan.node),
        "work_id": str(plan.work),
        "volume": volume,
        "work_date": work_date,
    }
    response = await send(api, "POST", url(world, "work-actuals"), who, json=body, key=uuid4())
    assert response.status_code == 201, response.text
    fact: dict[str, Any] = response.json()
    return fact


async def attach_photo(
    api: AsyncClient, world: AuthWorld, s3: httpx.AsyncClient, who: str, fact: str
) -> None:
    body = {
        "work_actual_id": fact,
        "files": [{"content_type": "image/jpeg", "size_bytes": len(JPEG)}],
    }
    issued = (await send(api, "POST", url(world, "photos", "presign"), who, json=body)).json()[
        "items"
    ][0]
    assert (
        await s3.put(issued["upload_url"], content=JPEG, headers=issued["headers"])
    ).status_code == 200
    confirmed = await send(
        api,
        "POST",
        url(world, "photos", "confirm"),
        who,
        json={"work_actual_id": fact, "items": [{"key": issued["key"]}]},
        key=uuid4(),
    )
    assert confirmed.status_code == 201, confirmed.text


async def add_child(engine: AsyncEngine, parent: Plan) -> Plan:
    """Этаж под узлом плана (без строки ГПР): факт на нём идёт с видом работ родителя."""
    async with engine.begin() as connection:
        node = await connection.scalar(
            text(
                "INSERT INTO project_node (project_id, legal_entity_id, parent_id, type, name)"
                " VALUES (:p, :le, :parent, 'floor', :name) RETURNING id"
            ),
            {"p": parent.project, "le": parent.entity, "parent": parent.node, "name": unique()},
        )
    return Plan(
        project=parent.project, entity=parent.entity, node=node, work=parent.work, item=None
    )


async def planned(
    api: AsyncClient, world: AuthWorld, engine: AsyncEngine
) -> tuple[Plan, dict[str, Any]]:
    """Строка ГПР на 100 с 1 по 10 сентября; подтверждено 40 + 20 (прораб), черновик мастера 30."""
    plan = await make_plan(
        engine, world, volume="100", start=date(2026, 9, 1), end=date(2026, 9, 10)
    )
    await new_fact(api, world, "foreman", plan, volume="40", work_date="2026-09-03")
    await new_fact(api, world, "foreman", plan, volume="20", work_date="2026-09-05")
    # Черновик датирован позже подтверждённых: в календарь факта он попасть не должен.
    draft = await new_fact(api, world, "master", plan, volume="30", work_date="2026-09-08")
    assert draft["status"] == "draft"
    return plan, draft


# --- ГПР: готовность в списке ---------------------------------------------------------------------


async def test_the_schedule_list_carries_the_progress_of_every_row_when_asked(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan, _draft = await planned(api, auth_world, worker_engine)
    idle = await make_plan(
        worker_engine, auth_world, volume="10", start=date(2026, 9, 1), end=date(2026, 9, 4)
    )

    response = await send(
        api,
        "GET",
        url(auth_world, "schedule"),
        "foreman",
        params={"with_progress": "true", "as_of": AS_OF},
    )

    assert response.status_code == 200, response.text
    rows = {row["node_id"]: row for row in response.json()}
    worked = rows[str(plan.node)]["progress"]
    # Подтверждено 40 + 20; черновик мастера (30) не считается. Линейный план на 6-е: 6 из 10 суток.
    assert Decimal(worked["confirmed_volume"]) == 60 and Decimal(worked["weight"]) == 10
    assert worked["percent"] == "60.00" and worked["planned_percent"] == "60.00"
    assert worked["delay_days"] == "0.0" and worked["state"] == "in_progress"
    assert worked["overdue_days"] == 0
    assert (worked["first_fact_date"], worked["last_fact_date"]) == ("2026-09-03", "2026-09-05")
    # Строка без факта: пусто, календаря факта нет, срок начала прошёл.
    quiet = rows[str(idle.node)]["progress"]
    assert Decimal(quiet["confirmed_volume"]) == 0 and quiet["percent"] == "0.00"
    assert quiet["state"] == "overdue_no_fact" and quiet["overdue_days"] == 2
    assert quiet["first_fact_date"] is None and quiet["last_fact_date"] is None


async def test_the_progress_is_absent_unless_it_is_asked_for(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan, _draft = await planned(api, auth_world, worker_engine)

    response = await send(
        api, "GET", url(auth_world, "schedule"), "foreman", params={"node_id": str(plan.node)}
    )

    assert response.status_code == 200 and response.json()[0]["progress"] is None


async def test_the_progress_of_a_node_filter_is_limited_to_its_rows_and_matches_the_readiness(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan, _draft = await planned(api, auth_world, worker_engine)
    await make_plan(worker_engine, auth_world)  # чужая строка в том же объекте

    listed = await send(
        api,
        "GET",
        url(auth_world, "schedule"),
        "foreman",
        params={"node_id": str(plan.node), "with_progress": "true", "as_of": AS_OF},
    )
    detail = await send(
        api,
        "GET",
        url(auth_world, "nodes", str(plan.node), "readiness"),
        "foreman",
        params={"as_of": AS_OF},
    )

    rows = listed.json()
    assert len(rows) == 1
    item = detail.json()["items"][0]
    for field in ("percent", "planned_percent", "delay_days", "state", "overdue_days"):
        assert rows[0]["progress"][field] == item[field], field


@pytest.mark.parametrize("who", ["customer", "sub_a"])
async def test_external_roles_get_the_progress_from_approved_facts_only(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine, who: str
) -> None:
    plan, _draft = await planned(api, auth_world, worker_engine)

    response = await send(
        api,
        "GET",
        url(auth_world, "schedule"),
        who,
        params={"node_id": str(plan.node), "with_progress": "true", "as_of": AS_OF},
    )

    assert response.status_code == 200, response.text
    progress = response.json()[0]["progress"]
    assert progress["percent"] == "60.00" and progress["last_fact_date"] == "2026-09-05"
    assert response.json()[0]["responsible_name"] is None


# --- факт: объём против плана ---------------------------------------------------------------------


async def test_facts_carry_the_planned_and_the_confirmed_volume_of_their_row(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan, draft = await planned(api, auth_world, worker_engine)

    response = await send(
        api, "GET", url(auth_world, "work-actuals"), "foreman", params={"node_id": str(plan.node)}
    )

    assert response.status_code == 200, response.text
    facts = response.json()
    assert len(facts) == 3
    for fact in facts:
        assert Decimal(fact["item_plan_volume"]) == 100, fact["id"]
        # Подтверждённый объём строки: черновик в него не входит, значение одно для всех её фактов.
        assert Decimal(fact["item_confirmed_volume"]) == 60, fact["id"]
    single = await send(api, "GET", url(auth_world, "work-actuals", draft["id"]), "foreman")
    assert Decimal(single.json()["item_confirmed_volume"]) == 60


async def test_a_fact_without_a_row_has_no_planned_or_confirmed_volume(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)
    fact = await new_fact(api, auth_world, "foreman", plan)

    assert fact["unplanned"] is True
    assert fact["item_plan_volume"] is None and fact["item_confirmed_volume"] is None


async def test_external_roles_do_not_get_the_volume_against_the_plan_of_a_fact(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan, _draft = await planned(api, auth_world, worker_engine)

    response = await send(
        api, "GET", url(auth_world, "work-actuals"), "customer", params={"node_id": str(plan.node)}
    )

    assert response.status_code == 200 and len(response.json()) == 2  # только подтверждённые
    for fact in response.json():
        assert fact["item_plan_volume"] is None and fact["item_confirmed_volume"] is None


# --- фото: фильтры и подписи ----------------------------------------------------------------------


async def test_the_photo_list_filters_by_several_facts_and_by_subtree_and_names_the_author(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine, s3: httpx.AsyncClient
) -> None:
    first = await make_plan(worker_engine, auth_world)
    second = await make_plan(worker_engine, auth_world)
    third = await make_plan(worker_engine, auth_world)
    child = await add_child(worker_engine, first)
    fact_a = (await new_fact(api, auth_world, "master", first))["id"]
    fact_b = (await new_fact(api, auth_world, "master", second))["id"]
    fact_c = (await new_fact(api, auth_world, "master", third))["id"]
    fact_in_child = (await new_fact(api, auth_world, "master", child))["id"]
    for fact in (fact_a, fact_b, fact_c, fact_in_child):
        await attach_photo(api, auth_world, s3, "master", fact)
    photos = url(auth_world, "photos")

    both = await send(
        api,
        "GET",
        photos,
        "foreman",
        params=[("work_actual_id", fact_a), ("work_actual_id", fact_b), ("with_urls", "true")],
    )
    own = await send(api, "GET", photos, "foreman", params={"node_id": str(first.node)})
    subtree = await send(
        api, "GET", photos, "foreman", params={"node_id": str(first.node), "subtree": "true"}
    )

    assert both.status_code == 200, both.text
    rows = both.json()
    assert {row["work_actual_id"] for row in rows} == {fact_a, fact_b}  # третий факт не попал
    assert {row["node_id"] for row in rows} == {str(first.node), str(second.node)}
    for row in rows:
        assert row["uploaded_by_name"] == "Мастер Восток (dev)"
        assert row["work_name"].startswith("Работа ") and row["work_date"] == "2026-09-03"
        assert row["url"] and row["url_expires_in"]
    assert [row["work_actual_id"] for row in own.json()] == [fact_a]
    assert {row["work_actual_id"] for row in subtree.json()} == {fact_a, fact_in_child}


async def test_the_photo_list_is_paged_and_can_start_with_the_newest(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine, s3: httpx.AsyncClient
) -> None:
    plan = await make_plan(worker_engine, auth_world)
    facts = [(await new_fact(api, auth_world, "master", plan))["id"] for _ in range(2)]
    for fact in facts:
        await attach_photo(api, auth_world, s3, "master", fact)
    photos = url(auth_world, "photos")
    node = {"node_id": str(plan.node)}

    oldest = await send(api, "GET", photos, "foreman", params={**node, "limit": 1})
    newest = await send(
        api, "GET", photos, "foreman", params={**node, "limit": 1, "newest_first": "true"}
    )
    second = await send(
        api,
        "GET",
        photos,
        "foreman",
        params={**node, "limit": 1, "offset": 1, "newest_first": "true"},
    )

    assert [row["work_actual_id"] for row in oldest.json()] == [facts[0]]
    assert [row["work_actual_id"] for row in newest.json()] == [facts[1]]
    assert [row["work_actual_id"] for row in second.json()] == [facts[0]]


async def test_the_photo_list_refuses_too_many_facts_and_bad_paging(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    photos = url(auth_world, "photos")
    many = [("work_actual_id", str(uuid4())) for _ in range(101)]

    too_many = await send(api, "GET", photos, "foreman", params=many)
    assert too_many.status_code == 422
    for params in ({"limit": 0}, {"limit": 501}, {"offset": -1}, {"offset": 1_000_001}):
        response = await send(api, "GET", photos, "foreman", params=params)
        assert response.status_code == 422, params


async def test_the_photo_list_stays_closed_to_external_roles(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    for who in ("customer", "sub_a"):
        response = await send(
            api, "GET", url(auth_world, "photos"), who, params={"node_id": str(uuid4())}
        )
        assert response.status_code == 403, who
