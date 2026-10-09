"""S3a: API ГПР - права, проверки значений, идемпотентность, зависимости, удаление."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.auth_support import AuthWorld, bearer, token_for
from tests.gpr_support import Plan, make_plan, purge

P = "/api/v1/projects"
W = "/api/v1/works"
BODY = {"plan_start": "2026-09-01", "plan_end": "2026-09-10", "plan_volume": "100"}


@pytest.fixture(autouse=True)
async def cleanup(worker_engine: AsyncEngine, auth_world: AuthWorld) -> AsyncIterator[None]:
    yield
    await purge(worker_engine, auth_world)


def url(world: AuthWorld, *tail: str, project: str = "east") -> str:
    return "/".join([f"{P}/{world.projects[project]}/schedule", *tail])


async def send(
    api: AsyncClient,
    method: str,
    path: str,
    who: str,
    *,
    json: Any = None,
    key: UUID | None = None,
    params: dict[str, Any] | None = None,
) -> Response:
    headers = bearer(await token_for(api, who))
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    return await api.request(method, path, headers=headers, json=json, params=params)


async def plan_row(
    api: AsyncClient, world: AuthWorld, plan: Plan, who: str = "foreman", **extra: Any
) -> Response:
    body = {"node_id": str(plan.node), "work_id": str(plan.work), **BODY, **extra}
    return await send(api, "POST", url(world), who, json=body, key=uuid4())


async def two_items(
    api: AsyncClient, world: AuthWorld, engine: AsyncEngine
) -> tuple[dict[str, Any], dict[str, Any]]:
    created = []
    for _ in range(2):
        plan = await make_plan(engine, world, with_item=False)
        response = await plan_row(api, world, plan)
        assert response.status_code == 201, response.text
        created.append(response.json())
    return created[0], created[1]


# --- права ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("who", "expected"),
    [
        ("foreman", 201),
        ("techdir", 201),
        ("estimator", 201),
        ("master", 403),
        ("owner", 403),
        ("finance", 403),
        ("storekeeper", 403),
        ("customer", 403),
        ("sub_a", 403),
    ],
)
async def test_only_the_roles_with_gpr_write_plan_work(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine, who: str, expected: int
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)

    response = await plan_row(api, auth_world, plan, who)

    assert response.status_code == expected, response.text


@pytest.mark.parametrize(
    ("who", "expected"),
    [("foreman", 200), ("master", 200), ("owner", 200), ("customer", 200), ("sub_a", 200),
     ("storekeeper", 403), ("accountant", 404), ("supplier", 404)],
)  # fmt: skip
async def test_who_reads_the_schedule_of_the_east_object(
    api: AsyncClient, auth_world: AuthWorld, who: str, expected: int
) -> None:
    response = await send(api, "GET", url(auth_world), who)
    assert response.status_code == expected, response.text


async def test_a_foreign_object_is_not_found_for_every_method(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)
    west = url(auth_world, project="west")

    for method, path, body in (
        ("GET", west, None),
        ("POST", west, {"node_id": str(plan.node), "work_id": str(plan.work), **BODY}),
        ("GET", f"{west}/{uuid4()}", None),
        ("GET", f"{west}/dependencies", None),
    ):
        response = await send(api, method, path, "foreman", json=body, key=uuid4())
        assert response.status_code == 404, (method, path, response.text)


# --- создание -----------------------------------------------------------------------------------


async def test_a_row_carries_the_unit_of_its_work_the_duration_and_the_responsible(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan = await make_plan(worker_engine, auth_world, unit="т", with_item=False)

    response = await plan_row(
        api, auth_world, plan, responsible_user_id=str(auth_world.users["foreman"]), weight="7.5"
    )

    row = response.json()
    assert response.status_code == 201, response.text
    assert row["unit"] == "т" and row["duration_days"] == 10 and row["weight"] == "7.500"
    assert row["plan_volume"] == "100.000" and row["work_name"].startswith("Работа ")
    assert row["responsible_name"] == "Прораб Восток (dev)"


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"plan_end": "2026-08-01"}, None),
        ({"plan_volume": "0"}, None),
        ({"plan_volume": "-3"}, None),
        ({"plan_volume": "1.2345"}, None),
        ({"weight": "0"}, None),
        ({"unknown_field": 1}, None),
        ({"node_id": str(uuid4())}, "schedule_node_invalid"),
        ({"work_id": str(uuid4())}, "schedule_work_invalid"),
    ],
)
async def test_a_wrong_row_is_refused(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_engine: AsyncEngine,
    overrides: dict[str, Any],
    detail: str | None,
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)

    response = await plan_row(api, auth_world, plan, **overrides)

    assert response.status_code == 422, response.text
    if detail:
        assert response.json()["detail"] == detail


async def test_the_node_must_belong_to_the_object_of_the_path(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)
    async with worker_engine.begin() as connection:
        west_node = await connection.scalar(
            text(
                "INSERT INTO project_node (project_id, legal_entity_id, type, name)"
                " SELECT id, legal_entity_id, 'building', 'S3A-' || gen_random_uuid()::text"
                " FROM project WHERE id = :p RETURNING id"
            ),
            {"p": auth_world.projects["west"]},
        )

    response = await plan_row(
        api, auth_world, Plan(plan.project, plan.entity, west_node, plan.work, plan.node)
    )

    assert (response.status_code, response.json()["detail"]) == (422, "schedule_node_invalid")


@pytest.mark.parametrize("who", ["customer", "lawyer", "supplier"])
async def test_the_responsible_is_an_internal_user_with_access_to_the_object(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine, who: str
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)

    response = await plan_row(api, auth_world, plan, responsible_user_id=str(auth_world.users[who]))

    # заказчик и поставщик - внешние, а у юриста доступ есть: он внутренний и допустим
    if who == "lawyer":
        assert response.status_code == 201, response.text
    else:
        assert (response.status_code, response.json()["detail"]) == (422, "responsible_invalid")


async def test_a_node_and_a_work_have_one_live_row(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)
    assert (await plan_row(api, auth_world, plan)).status_code == 201

    again = await plan_row(api, auth_world, plan)

    assert (again.status_code, again.json()["detail"]) == (409, "schedule_item_exists")


async def test_creation_is_idempotent_and_the_key_is_required(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)
    body = {"node_id": str(plan.node), "work_id": str(plan.work), **BODY}
    key = uuid4()

    first = await send(api, "POST", url(auth_world), "foreman", json=body, key=key)
    again = await send(api, "POST", url(auth_world), "foreman", json=body, key=key)
    other = await send(
        api, "POST", url(auth_world), "foreman", json={**body, "plan_volume": "5"}, key=key
    )
    keyless = await send(api, "POST", url(auth_world), "foreman", json=body)

    assert first.status_code == 201 and again.status_code == 201
    assert again.json() == first.json() and again.headers["Idempotent-Replayed"] == "true"
    assert other.status_code == 422 and other.json()["detail"] == "idempotency_key_reused"
    assert keyless.status_code == 422
    listed = await send(api, "GET", url(auth_world), "foreman", params={"node_id": str(plan.node)})
    assert len(listed.json()) == 1


# --- чтение и правка ----------------------------------------------------------------------------


async def test_the_list_filters_by_node_subtree_and_work(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    first, second = await two_items(api, auth_world, worker_engine)

    by_node = await send(
        api, "GET", url(auth_world), "foreman", params={"node_id": first["node_id"]}
    )
    by_work = await send(
        api, "GET", url(auth_world), "foreman", params={"work_id": second["work_id"]}
    )
    subtree = await send(
        api,
        "GET",
        url(auth_world),
        "foreman",
        params={"node_id": first["node_id"], "subtree": "true"},
    )

    assert [row["id"] for row in by_node.json()] == [first["id"]]
    assert [row["id"] for row in by_work.json()] == [second["id"]]
    assert [row["id"] for row in subtree.json()] == [first["id"]]


async def test_external_roles_read_rows_without_the_responsible(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)
    created = await plan_row(
        api, auth_world, plan, responsible_user_id=str(auth_world.users["foreman"])
    )
    assert created.json()["responsible_name"]

    for who in ("customer", "sub_a"):
        rows = (
            await send(api, "GET", url(auth_world), who, params={"node_id": str(plan.node)})
        ).json()
        assert [row["id"] for row in rows] == [created.json()["id"]], who
        assert rows[0]["responsible_user_id"] is None and rows[0]["responsible_name"] is None, who
        assert rows[0]["work_name"] and rows[0]["unit"], who


async def test_patch_changes_only_the_sent_fields_and_null_clears_an_optional_one(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)
    row = (await plan_row(api, auth_world, plan, weight="4")).json()
    path = url(auth_world, row["id"])

    changed = await send(
        api,
        "PATCH",
        path,
        "estimator",
        json={"plan_end": "2026-09-20", "weight": None, "plan_volume": "250.5"},
    )

    body = changed.json()
    assert changed.status_code == 200, changed.text
    assert body["plan_end"] == "2026-09-20" and body["weight"] is None
    assert body["plan_volume"] == "250.500" and body["plan_start"] == "2026-09-01"
    assert body["duration_days"] == 20


@pytest.mark.parametrize(
    "patch",
    [
        {"plan_end": "2026-08-01"},
        {"plan_volume": None},
        {"plan_start": None},
        {"plan_volume": "0"},
        {"node_id": str(uuid4())},
        {"work_id": str(uuid4())},
    ],
)
async def test_a_wrong_patch_is_refused(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine, patch: dict[str, Any]
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)
    row = (await plan_row(api, auth_world, plan)).json()

    response = await send(api, "PATCH", url(auth_world, row["id"]), "foreman", json=patch)

    assert response.status_code == 422, response.text


async def test_the_master_cannot_change_or_delete_a_row(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)
    row = (await plan_row(api, auth_world, plan)).json()
    path = url(auth_world, row["id"])

    assert (await send(api, "PATCH", path, "master", json={"plan_volume": "1"})).status_code == 403
    assert (await send(api, "DELETE", path, "master")).status_code == 403
    assert (await send(api, "GET", path, "master")).json()["plan_volume"] == "100.000"


# --- зависимости --------------------------------------------------------------------------------


async def test_dependencies_are_listed_deduplicated_and_closed_against_cycles(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    a, b = await two_items(api, auth_world, worker_engine)
    path = url(auth_world, "dependencies")
    link = {"predecessor_id": a["id"], "successor_id": b["id"]}

    created = await send(api, "POST", path, "foreman", json=link, key=uuid4())
    duplicate = await send(api, "POST", path, "foreman", json=link, key=uuid4())
    back = await send(
        api,
        "POST",
        path,
        "foreman",
        json={"predecessor_id": b["id"], "successor_id": a["id"]},
        key=uuid4(),
    )
    itself = await send(
        api,
        "POST",
        path,
        "foreman",
        json={"predecessor_id": a["id"], "successor_id": a["id"]},
        key=uuid4(),
    )

    assert created.status_code == 201, created.text
    assert (duplicate.status_code, duplicate.json()["detail"]) == (409, "dependency_exists")
    assert (back.status_code, back.json()["detail"]) == (422, "dependency_cycle")
    assert (itself.status_code, itself.json()["detail"]) == (422, "dependency_cycle")
    listed = (await send(api, "GET", path, "customer")).json()
    assert {(row["predecessor_id"], row["successor_id"]) for row in listed} >= {(a["id"], b["id"])}


async def test_a_dependency_needs_rows_of_the_same_object_and_the_right_to_plan(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    a, _ = await two_items(api, auth_world, worker_engine)
    path = url(auth_world, "dependencies")

    missing = await send(
        api,
        "POST",
        path,
        "foreman",
        json={"predecessor_id": a["id"], "successor_id": str(uuid4())},
        key=uuid4(),
    )
    forbidden = await send(
        api,
        "POST",
        path,
        "master",
        json={"predecessor_id": a["id"], "successor_id": a["id"]},
        key=uuid4(),
    )

    assert (missing.status_code, missing.json()["detail"]) == (422, "dependency_item_invalid")
    assert forbidden.status_code == 403


async def test_a_dependency_can_be_deleted_once(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    a, b = await two_items(api, auth_world, worker_engine)
    path = url(auth_world, "dependencies")
    link = (
        await send(
            api,
            "POST",
            path,
            "foreman",
            json={"predecessor_id": a["id"], "successor_id": b["id"]},
            key=uuid4(),
        )
    ).json()

    first = await send(api, "DELETE", f"{path}/{link['id']}", "foreman")
    second = await send(api, "DELETE", f"{path}/{link['id']}", "foreman")
    reverse = await send(
        api,
        "POST",
        path,
        "foreman",
        json={"predecessor_id": b["id"], "successor_id": a["id"]},
        key=uuid4(),
    )

    assert (first.status_code, second.status_code) == (204, 404)
    assert reverse.status_code == 201  # после удаления обратная связь уже не цикл


@pytest.mark.parametrize(
    "params",
    [{"offset": 99999999999999999999}, {"offset": 1_000_001}, {"limit": 0}, {"limit": 10_001}],
)
async def test_paging_outside_the_bounds_is_refused_not_a_server_error(
    api: AsyncClient, auth_world: AuthWorld, params: dict[str, Any]
) -> None:
    response = await send(api, "GET", url(auth_world), "foreman", params=params)

    assert response.status_code == 422, response.text


async def test_the_dependency_list_is_paged(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    first, second = await two_items(api, auth_world, worker_engine)
    path = url(auth_world, "dependencies")
    link = {"predecessor_id": first["id"], "successor_id": second["id"]}
    assert (await send(api, "POST", path, "foreman", json=link, key=uuid4())).status_code == 201

    page = await send(api, "GET", path, "foreman", params={"limit": 1, "offset": 0})
    beyond = await send(api, "GET", path, "foreman", params={"limit": 1, "offset": 999_999})
    too_big = await send(api, "GET", path, "foreman", params={"limit": 20_001})

    assert page.status_code == 200 and len(page.json()) == 1
    assert beyond.status_code == 200 and beyond.json() == []
    assert too_big.status_code == 422


# --- удаление -----------------------------------------------------------------------------------


async def test_a_deleted_row_takes_its_dependencies_along_and_is_gone(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    a, b = await two_items(api, auth_world, worker_engine)
    path = url(auth_world, "dependencies")
    await send(
        api,
        "POST",
        path,
        "foreman",
        json={"predecessor_id": a["id"], "successor_id": b["id"]},
        key=uuid4(),
    )

    deleted = await send(api, "DELETE", url(auth_world, a["id"]), "foreman")

    assert deleted.status_code == 204
    assert (await send(api, "GET", url(auth_world, a["id"]), "foreman")).status_code == 404
    assert (await send(api, "DELETE", url(auth_world, a["id"]), "foreman")).status_code == 404
    links = (await send(api, "GET", path, "foreman")).json()
    assert all(a["id"] not in (row["predecessor_id"], row["successor_id"]) for row in links)


async def test_a_node_with_a_schedule_row_cannot_be_deleted_through_the_api(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)
    row = (await plan_row(api, auth_world, plan)).json()
    node_path = f"{P}/{plan.project}/nodes/{plan.node}"

    refused = await send(api, "DELETE", node_path, "foreman")
    await send(api, "DELETE", url(auth_world, row["id"]), "foreman")
    allowed = await send(api, "DELETE", node_path, "foreman")

    assert (refused.status_code, refused.json()["detail"]) == (409, "node_has_dependents")
    assert allowed.status_code == 204


async def test_a_work_in_the_schedule_cannot_be_deleted_or_change_its_unit(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    plan = await make_plan(worker_engine, auth_world, with_item=False)
    await plan_row(api, auth_world, plan)

    rename = await send(api, "PATCH", f"{W}/{plan.work}", "estimator", json={"name": "Новое имя"})
    unit = await send(api, "PATCH", f"{W}/{plan.work}", "estimator", json={"unit": "м2"})
    deleted = await send(api, "DELETE", f"{W}/{plan.work}", "estimator")

    assert rename.status_code == 200
    assert (unit.status_code, unit.json()["detail"]) == (409, "work_unit_locked")
    assert (deleted.status_code, deleted.json()["detail"]) == (409, "work_has_dependents")
