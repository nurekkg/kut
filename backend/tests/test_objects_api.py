"""S2: API объектов, дерева и справочника работ - права, правила дерева, идемпотентность."""

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import Engine, text

from app.modules.auth import devseed
from tests.auth_support import AuthWorld, bearer, token_for

P = "/api/v1/projects"
W = "/api/v1/works"


def unique(prefix: str = "API-") -> str:
    return f"{prefix}{uuid4().hex[:8]}"


@pytest.fixture
def objects_cleanup(worker_sync_engine: Engine) -> Iterator[None]:
    """Записи тестов (имена и коды API-...) гасятся мягко: листья раньше родителей."""
    yield
    with worker_sync_engine.begin() as connection:
        for kind in ("room", "floor", "section", "building", "stage"):
            connection.execute(
                text(
                    "UPDATE project_node SET deleted_at = now() WHERE deleted_at IS NULL"
                    " AND type = :t AND (name LIKE 'API-%' OR project_id IN"
                    " (SELECT id FROM project WHERE name LIKE 'API-%'))"
                ),
                {"t": kind},
            )
        connection.execute(
            text(
                "UPDATE project SET deleted_at = now()"
                " WHERE deleted_at IS NULL AND name LIKE 'API-%'"
            )
        )
        connection.execute(
            text(
                "UPDATE work SET deleted_at = now() WHERE deleted_at IS NULL AND code LIKE 'API-%'"
            )
        )


async def call(
    api: AsyncClient,
    method: str,
    path: str,
    token: str,
    *,
    json: Any = None,
    key: UUID | None = None,
    params: dict[str, Any] | None = None,
) -> Response:
    headers = bearer(token)
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    return await api.request(method, path, headers=headers, json=json, params=params)


async def create(api: AsyncClient, path: str, token: str, body: dict[str, Any]) -> Response:
    return await call(api, "POST", path, token, json=body, key=uuid4())


async def new_project(
    api: AsyncClient, token: str, world: AuthWorld, **overrides: Any
) -> dict[str, Any]:
    body = {
        "name": unique(),
        "legal_entity_id": str(world.legal_entities[devseed.LE_EAST]),
        **overrides,
    }
    response = await create(api, P, token, body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def new_node(
    api: AsyncClient,
    token: str,
    project: str | UUID,
    kind: str,
    parent: str | None = None,
    **kw: Any,
) -> dict[str, Any]:
    body = {"type": kind, "name": unique(), "parent_id": parent, **kw}
    response = await create(api, f"{P}/{project}/nodes", token, body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def tree_of(api: AsyncClient, token: str, project: str | UUID) -> list[dict[str, Any]]:
    response = await call(api, "GET", f"{P}/{project}/tree", token)
    assert response.status_code == 200, response.text
    nodes: list[dict[str, Any]] = response.json()
    return nodes


async def east_floor(api: AsyncClient, token: str, world: AuthWorld) -> dict[str, Any]:
    nodes = await tree_of(api, token, world.projects["east"])
    return next(node for node in nodes if node["type"] == "floor" and node["name"] == "Этаж 1")


# --- карточка объекта ----------------------------------------------------------------------


async def test_internal_roles_get_the_whole_card_and_external_roles_only_the_basics(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    east = str(auth_world.projects["east"])
    owner = await token_for(api, "owner")
    card = (await call(api, "GET", f"{P}/{east}", owner)).json()

    assert card["lifecycle_stage"] == "construction"
    assert card["planned_start"] == "2025-03-01" and card["planned_end"] == "2028-12-31"
    assert card["planned_budget"] == "2400000000.00" and card["budget_currency"] == "KGS"
    assert card["customer_name"] == devseed.CUSTOMER
    assert card["address"] and card["latitude"] and card["longitude"]
    assert card["node_count"] == 1166

    external = (await call(api, "GET", f"{P}/{east}", await token_for(api, "customer"))).json()
    assert external["name"] == card["name"] and external["status"] == card["status"]
    hidden = set(card) - {"id", "name", "status", "legal_entity_id"}
    assert all(external[field] is None for field in hidden)


@pytest.mark.parametrize(
    ("who", "expected"),
    [
        ("owner", 201),
        ("techdir", 201),
        ("finance", 403),
        ("foreman", 403),
        ("master", 403),
        ("estimator", 403),
        ("accountant", 403),
        ("lawyer", 403),
        ("customer", 403),
        ("sub_a", 403),
        ("supplier", 403),
    ],
)
async def test_only_owner_and_technical_director_create_objects(
    api: AsyncClient, auth_world: AuthWorld, objects_cleanup: None, who: str, expected: int
) -> None:
    body = {
        "name": unique(),
        "legal_entity_id": str(auth_world.legal_entities[devseed.LE_EAST]),
    }

    response = await create(api, P, await token_for(api, who), body)

    assert response.status_code == expected, response.text
    if expected == 201:
        assert response.json()["lifecycle_stage"] == "land_purchase"
        assert response.json()["status"] == "active" and response.json()["node_count"] == 0


async def test_a_new_object_is_idempotent_and_visible_to_every_group_role_at_once(
    api: AsyncClient, auth_world: AuthWorld, objects_cleanup: None
) -> None:
    owner, techdir = await token_for(api, "owner"), await token_for(api, "techdir")
    before = {
        item["id"] for item in (await call(api, "GET", P, techdir)).json()
    }  # кэш прав прогрет
    body = {"name": unique(), "legal_entity_id": str(auth_world.legal_entities[devseed.LE_EAST])}
    key = uuid4()

    first = await call(api, "POST", P, owner, json=body, key=key)
    again = await call(api, "POST", P, owner, json=body, key=key)
    other = await call(api, "POST", P, owner, json={**body, "name": unique()}, key=key)

    assert first.status_code == 201 and again.status_code == 201
    assert again.json() == first.json() and again.headers["Idempotent-Replayed"] == "true"
    assert other.status_code == 422 and other.json()["detail"] == "idempotency_key_reused"
    created = first.json()["id"]
    assert created not in before
    seen = {item["id"] for item in (await call(api, "GET", P, techdir)).json()}
    assert created in seen  # сразу, а не через минуту кэша контекста
    assert (
        await call(api, "GET", f"{P}/{created}", await token_for(api, "finance"))
    ).status_code == 200
    assert (await call(api, "POST", P, owner, json=body)).status_code == 422  # ключ обязателен


@pytest.mark.parametrize(
    ("patch", "detail"),
    [
        ({"planned_start": "2027-02-01", "planned_end": "2027-01-01"}, None),
        ({"planned_budget": "-1"}, None),
        ({"planned_budget": "1.234"}, None),
        ({"latitude": "42.5"}, None),
        ({"latitude": "91", "longitude": "74"}, None),
        ({"lifecycle_stage": "dreaming"}, None),
        ({"status": "frozen"}, None),
        ({"name": None}, None),
        ({"legal_entity_id": str(uuid4())}, None),
        ({"customer_id": str(uuid4())}, "customer_invalid"),
    ],
)
async def test_an_invalid_card_is_refused(
    api: AsyncClient,
    auth_world: AuthWorld,
    objects_cleanup: None,
    patch: dict[str, Any],
    detail: str | None,
) -> None:
    owner = await token_for(api, "owner")
    project = await new_project(api, owner, auth_world)

    response = await call(api, "PATCH", f"{P}/{project['id']}", owner, json=patch)

    assert response.status_code == 422, response.text
    if detail:
        assert response.json()["detail"] == detail


async def test_the_customer_must_be_a_customer_counterparty_and_the_legal_entity_known(
    api: AsyncClient, auth_world: AuthWorld, objects_cleanup: None
) -> None:
    owner = await token_for(api, "owner")
    entity = str(auth_world.legal_entities[devseed.LE_EAST])
    wrong = str(auth_world.counterparties[devseed.SUB_A])
    right = str(auth_world.counterparties[devseed.CUSTOMER])

    bad = await create(
        api, P, owner, {"name": unique(), "legal_entity_id": entity, "customer_id": wrong}
    )
    unknown = await create(api, P, owner, {"name": unique(), "legal_entity_id": str(uuid4())})
    good = await create(
        api, P, owner, {"name": unique(), "legal_entity_id": entity, "customer_id": right}
    )

    assert (bad.status_code, bad.json()["detail"]) == (422, "customer_invalid")
    assert (unknown.status_code, unknown.json()["detail"]) == (422, "unknown_legal_entity")
    assert good.status_code == 201 and good.json()["customer_name"] == devseed.CUSTOMER


async def test_patch_changes_only_the_sent_fields_and_null_clears_an_optional_one(
    api: AsyncClient, auth_world: AuthWorld, objects_cleanup: None
) -> None:
    owner = await token_for(api, "owner")
    project = await new_project(
        api, owner, auth_world, address="ул. Тестовая, 1", planned_budget="100.50"
    )

    changed = await call(
        api, "PATCH", f"{P}/{project['id']}", owner,
        json={"lifecycle_stage": "design", "address": None, "planned_end": "2030-01-01"},
    )  # fmt: skip

    body = changed.json()
    assert changed.status_code == 200
    assert body["lifecycle_stage"] == "design" and body["address"] is None
    assert body["planned_end"] == "2030-01-01" and body["planned_budget"] == "100.50"
    assert body["name"] == project["name"] and body["legal_entity_id"] == project["legal_entity_id"]


async def test_a_foreman_edits_the_tree_of_his_object_but_not_its_card_and_not_a_foreign_object(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    foreman = await token_for(api, "foreman")
    east, west = auth_world.projects["east"], auth_world.projects["west"]

    assert (await call(api, "PATCH", f"{P}/{east}", foreman, json={"name": "x"})).status_code == 403
    assert (await call(api, "PATCH", f"{P}/{west}", foreman, json={"name": "x"})).status_code == 404
    assert (await call(api, "DELETE", f"{P}/{east}", foreman)).status_code == 403
    assert (await call(api, "GET", f"{P}/{west}", foreman)).status_code == 404


async def test_an_object_with_nodes_cannot_be_deleted_until_they_are_gone(
    api: AsyncClient, auth_world: AuthWorld, objects_cleanup: None
) -> None:
    owner = await token_for(api, "owner")
    project = await new_project(api, owner, auth_world)
    node = await new_node(api, owner, project["id"], "building")

    blocked = await call(api, "DELETE", f"{P}/{project['id']}", owner)
    await call(api, "DELETE", f"{P}/{project['id']}/nodes/{node['id']}", owner)
    done = await call(api, "DELETE", f"{P}/{project['id']}", owner)

    assert (blocked.status_code, blocked.json()["detail"]) == (409, "project_has_nodes")
    assert done.status_code == 204
    assert (await call(api, "GET", f"{P}/{project['id']}", owner)).status_code == 404
    assert project["id"] not in {item["id"] for item in (await call(api, "GET", P, owner)).json()}


# --- дерево -----------------------------------------------------------------------------------


async def test_the_tree_is_one_flat_list_with_parents_and_paths(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    nodes = await tree_of(api, await token_for(api, "owner"), auth_world.projects["east"])

    by_id = {node["id"]: node for node in nodes}
    kinds = {
        kind: sum(node["type"] == kind for node in nodes)
        for kind in ("stage", "building", "section", "floor", "room")
    }
    assert len(nodes) == len(by_id) == 1166
    assert kinds == {"stage": 2, "building": 4, "section": 8, "floor": 128, "room": 1024}
    for node in nodes:
        assert node["path"][-1] == node["id"]
        if node["parent_id"] is None:
            assert node["path"] == [node["id"]]
        else:
            assert node["path"][:-1] == by_id[node["parent_id"]]["path"]
    building = next(node for node in nodes if node["type"] == "building")
    assert building["foreman_user_id"] and building["polygon"] and len(building["polygon"]) == 4


@pytest.mark.parametrize(
    ("who", "status"),
    [
        ("owner", 200), ("finance", 200), ("techdir", 200), ("foreman", 200), ("master", 200),
        ("estimator", 200), ("storekeeper", 200), ("lawyer", 200), ("customer", 200),
        ("sub_a", 200), ("sub_b", 404), ("procurement", 404), ("supplier", 404),
    ],
)  # fmt: skip
async def test_who_can_read_the_tree_of_the_east_object(
    api: AsyncClient, auth_world: AuthWorld, who: str, status: int
) -> None:
    response = await call(
        api, "GET", f"{P}/{auth_world.projects['east']}/tree", await token_for(api, who)
    )

    assert response.status_code == status, response.text


async def test_a_supplier_has_no_right_to_objects_at_all_even_on_its_own_project(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "supplier")
    west = auth_world.projects["west"]

    assert (await call(api, "GET", f"{P}/{west}/tree", token)).status_code == 403
    assert (await call(api, "GET", f"{P}/{west}", token)).status_code == 403
    assert (await call(api, "GET", P, token)).status_code == 403


async def test_external_roles_read_the_tree_without_the_people(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    for who in ("customer", "sub_a"):
        nodes = await tree_of(api, await token_for(api, who), auth_world.projects["east"])
        assert len(nodes) == 1166
        assert all(
            node["foreman_user_id"] is None and node["master_user_id"] is None for node in nodes
        )
        assert any(node["polygon"] for node in nodes)


# --- узлы -------------------------------------------------------------------------------------


async def test_a_foreman_builds_inside_his_own_object_and_a_master_cannot(
    api: AsyncClient, auth_world: AuthWorld, objects_cleanup: None
) -> None:
    foreman, master = await token_for(api, "foreman"), await token_for(api, "master")
    east = auth_world.projects["east"]
    floor = await east_floor(api, foreman, auth_world)

    room = await new_node(api, foreman, east, "room", floor["id"], code="X1")
    forbidden = await create(
        api,
        f"{P}/{east}/nodes",
        master,
        {"type": "room", "name": unique(), "parent_id": floor["id"]},
    )
    other_object = await create(
        api,
        f"{P}/{auth_world.projects['west']}/nodes",
        foreman,
        {"type": "stage", "name": unique()},
    )

    assert room["path"] == [*floor["path"], room["id"]] and room["parent_id"] == floor["id"]
    assert room["sort_order"] >= 8  # дописан в конец, после восьми помещений этажа
    assert forbidden.status_code == 403
    assert other_object.status_code == 404
    assert (await call(api, "GET", f"{P}/{east}/nodes/{room['id']}", master)).status_code == 200


@pytest.mark.parametrize("who", ["finance", "estimator", "storekeeper", "customer", "sub_a"])
async def test_read_only_roles_cannot_write_nodes(
    api: AsyncClient, auth_world: AuthWorld, who: str
) -> None:
    east = auth_world.projects["east"]
    token = await token_for(api, who)

    create_ = await create(api, f"{P}/{east}/nodes", token, {"type": "stage", "name": unique()})
    some = (await tree_of(api, token, east))[0]["id"]
    patch = await call(api, "PATCH", f"{P}/{east}/nodes/{some}", token, json={"name": "x"})
    delete = await call(api, "DELETE", f"{P}/{east}/nodes/{some}", token)
    move = await call(api, "POST", f"{P}/{east}/nodes/{some}/move", token, json={"parent_id": None})

    assert {create_.status_code, patch.status_code, delete.status_code, move.status_code} == {403}


async def test_the_rules_of_the_tree_come_back_as_codes(
    api: AsyncClient, auth_world: AuthWorld, objects_cleanup: None
) -> None:
    owner = await token_for(api, "owner")
    project = await new_project(api, owner, auth_world)
    other = await new_project(api, owner, auth_world)
    building = await new_node(api, owner, project["id"], "building", code="K1")
    section = await new_node(api, owner, project["id"], "section", building["id"])
    foreign = await new_node(api, owner, other["id"], "building")
    nodes = f"{P}/{project['id']}/nodes"

    wrong_type = await create(
        api, nodes, owner, {"type": "room", "name": unique(), "parent_id": building["id"]}
    )
    wrong_parent = await create(
        api, nodes, owner, {"type": "section", "name": unique(), "parent_id": foreign["id"]}
    )
    no_parent = await create(
        api, nodes, owner, {"type": "section", "name": unique(), "parent_id": str(uuid4())}
    )
    taken = await create(api, nodes, owner, {"type": "building", "name": unique(), "code": "K1"})
    cycle = await call(
        api, "POST", f"{nodes}/{building['id']}/move", owner, json={"parent_id": section["id"]}
    )
    moved_away = await call(
        api, "POST", f"{nodes}/{section['id']}/move", owner, json={"parent_id": foreign["id"]}
    )
    busy = await call(api, "DELETE", f"{nodes}/{building['id']}", owner)

    assert (wrong_type.status_code, wrong_type.json()["detail"]) == (422, "node_type_not_allowed")
    assert (wrong_parent.status_code, wrong_parent.json()["detail"]) == (422, "node_parent_invalid")
    assert (no_parent.status_code, no_parent.json()["detail"]) == (422, "node_parent_invalid")
    assert (taken.status_code, taken.json()["detail"]) == (409, "node_code_taken")
    assert (cycle.status_code, cycle.json()["detail"]) == (422, "node_cycle")
    assert (moved_away.status_code, moved_away.json()["detail"]) == (422, "node_parent_invalid")
    assert (busy.status_code, busy.json()["detail"]) == (409, "node_has_dependents")


async def test_creating_a_node_is_idempotent_and_validates_its_fields(
    api: AsyncClient, auth_world: AuthWorld, objects_cleanup: None
) -> None:
    owner = await token_for(api, "owner")
    project = await new_project(api, owner, auth_world)
    nodes = f"{P}/{project['id']}/nodes"
    body = {"type": "building", "name": unique()}
    key = uuid4()

    first = await call(api, "POST", nodes, owner, json=body, key=key)
    again = await call(api, "POST", nodes, owner, json=body, key=key)
    invalid = [
        {"type": "building", "name": ""},
        {"type": "villa", "name": unique()},
        {
            "type": "building",
            "name": unique(),
            "planned_start": "2026-02-01",
            "planned_end": "2026-01-01",
        },
        {"type": "building", "name": unique(), "polygon": [[0, 0], [1, 1]]},
        {"type": "building", "name": unique(), "polygon": [[0, 0], [1, 1], [2, "x"]]},
        {"type": "building", "name": unique(), "polygon": [[0, 0], [1, 1], [9_000_000, 1]]},
        {"type": "building", "name": unique(), "extra": 1},
    ]

    assert first.status_code == 201 and again.json() == first.json()
    assert (await call(api, "POST", nodes, owner, json=body)).status_code == 422  # нет ключа
    for item in invalid:
        assert (await create(api, nodes, owner, item)).status_code == 422, item
    assert len(await tree_of(api, owner, project["id"])) == 1


async def test_a_node_is_edited_partly_and_its_responsible_people_are_checked(
    api: AsyncClient, auth_world: AuthWorld, objects_cleanup: None
) -> None:
    owner = await token_for(api, "owner")
    foreman_id = str(auth_world.users["foreman"])
    master_id = str(auth_world.users["master"])
    # объект создан заново: у прораба и мастера к нему доступа нет
    project = await new_project(api, owner, auth_world)
    node = await new_node(api, owner, project["id"], "building")
    path = f"{P}/{project['id']}/nodes/{node['id']}"
    east = auth_world.projects["east"]
    east_building = next(n for n in await tree_of(api, owner, east) if n["type"] == "building")
    east_path = f"{P}/{east}/nodes/{east_building['id']}"
    own = await call(api, "GET", east_path, owner)

    nobody = await call(api, "PATCH", path, owner, json={"foreman_user_id": foreman_id})
    wrong_role = await call(api, "PATCH", east_path, owner, json={"foreman_user_id": master_id})
    wrong_role_master = await call(
        api, "PATCH", east_path, owner, json={"master_user_id": foreman_id}
    )
    unknown = await call(api, "PATCH", east_path, owner, json={"foreman_user_id": str(uuid4())})
    fine = await call(
        api,
        "PATCH",
        east_path,
        owner,
        json={
            "name": east_building["name"],
            "master_user_id": master_id,
            "code": east_building["code"],
        },
    )
    polygon = await call(
        api, "PATCH", path, owner, json={"polygon": [[0, 0], [4, 0], [4, 4]], "code": "  P-1 "}
    )
    cleared = await call(api, "PATCH", path, owner, json={"polygon": None, "code": None})

    assert own.json()["foreman_name"] == "Прораб Восток (dev)"
    assert (nobody.status_code, nobody.json()["detail"]) == (422, "foreman_invalid")
    assert (wrong_role.status_code, wrong_role.json()["detail"]) == (422, "foreman_invalid")
    assert (wrong_role_master.status_code, wrong_role_master.json()["detail"]) == (
        422,
        "master_invalid",
    )
    assert unknown.status_code == 422
    assert fine.status_code == 200 and fine.json()["master_name"] == "Мастер Восток (dev)"
    assert polygon.json()["polygon"] == [[0, 0], [4, 0], [4, 4]] and polygon.json()["code"] == "P-1"
    assert cleared.json()["polygon"] is None and cleared.json()["code"] is None
    assert (await call(api, "PATCH", path, owner, json={"name": None})).status_code == 422


async def test_a_move_changes_the_path_of_the_whole_subtree_and_the_order_of_the_siblings(
    api: AsyncClient, auth_world: AuthWorld, objects_cleanup: None
) -> None:
    owner = await token_for(api, "owner")
    project = await new_project(api, owner, auth_world)
    pid = project["id"]
    first = await new_node(api, owner, pid, "stage")
    second = await new_node(api, owner, pid, "stage")
    building = await new_node(api, owner, pid, "building", first["id"])
    section = await new_node(api, owner, pid, "section", building["id"])
    floor = await new_node(api, owner, pid, "floor", section["id"])
    other = await new_node(api, owner, pid, "building", second["id"])

    moved = await call(
        api,
        "POST",
        f"{P}/{pid}/nodes/{building['id']}/move",
        owner,
        json={"parent_id": second["id"], "index": 0},
    )
    nodes = {node["id"]: node for node in await tree_of(api, owner, pid)}

    assert moved.status_code == 200 and moved.json()["parent_id"] == second["id"]
    assert nodes[building["id"]]["path"] == [second["id"], building["id"]]
    assert nodes[floor["id"]]["path"] == [second["id"], building["id"], section["id"], floor["id"]]
    assert (nodes[building["id"]]["sort_order"], nodes[other["id"]]["sort_order"]) == (0, 1)

    again = await call(
        api,
        "POST",
        f"{P}/{pid}/nodes/{building['id']}/move",
        owner,
        json={"parent_id": second["id"]},
    )
    nodes = {node["id"]: node for node in await tree_of(api, owner, pid)}
    assert again.status_code == 200
    assert (nodes[other["id"]]["sort_order"], nodes[building["id"]]["sort_order"]) == (0, 1)

    to_root = await call(
        api, "POST", f"{P}/{pid}/nodes/{building['id']}/move", owner, json={"parent_id": None}
    )
    nodes = {node["id"]: node for node in await tree_of(api, owner, pid)}
    assert to_root.status_code == 200 and nodes[floor["id"]]["path"][0] == building["id"]
    assert (
        await call(api, "POST", f"{P}/{pid}/nodes/{uuid4()}/move", owner, json={})
    ).status_code == 404


async def test_a_node_is_deleted_leaf_first_and_a_deleted_node_is_gone(
    api: AsyncClient, auth_world: AuthWorld, objects_cleanup: None
) -> None:
    owner = await token_for(api, "owner")
    project = await new_project(api, owner, auth_world)
    pid = project["id"]
    building = await new_node(api, owner, pid, "building")
    section = await new_node(api, owner, pid, "section", building["id"])

    parent_first = await call(api, "DELETE", f"{P}/{pid}/nodes/{building['id']}", owner)
    leaf = await call(api, "DELETE", f"{P}/{pid}/nodes/{section['id']}", owner)
    parent = await call(api, "DELETE", f"{P}/{pid}/nodes/{building['id']}", owner)
    again = await call(api, "DELETE", f"{P}/{pid}/nodes/{building['id']}", owner)

    assert parent_first.status_code == 409
    assert (leaf.status_code, parent.status_code, again.status_code) == (204, 204, 404)
    assert await tree_of(api, owner, pid) == []
    assert (await call(api, "GET", f"{P}/{pid}/nodes/{section['id']}", owner)).status_code == 404


async def test_a_node_of_another_project_is_not_reachable_through_this_one(
    api: AsyncClient, auth_world: AuthWorld, objects_cleanup: None
) -> None:
    owner = await token_for(api, "owner")
    one, two = await new_project(api, owner, auth_world), await new_project(api, owner, auth_world)
    node = await new_node(api, owner, two["id"], "building")

    for method, suffix, body in (
        ("GET", "", None),
        ("PATCH", "", {"name": "x"}),
        ("DELETE", "", None),
        ("POST", "/move", {}),
    ):
        response = await call(
            api, method, f"{P}/{one['id']}/nodes/{node['id']}{suffix}", owner, json=body
        )
        assert response.status_code == 404, (method, response.text)


# --- справочник работ -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("who", "read", "write"),
    [
        ("owner", 200, 201), ("techdir", 200, 201), ("estimator", 200, 201),
        ("finance", 200, 403), ("foreman", 200, 403), ("master", 200, 403),
        ("procurement", 200, 403),
        ("storekeeper", 200, 403), ("accountant", 200, 403), ("lawyer", 200, 403),
        ("customer", 403, 403), ("sub_a", 403, 403), ("supplier", 403, 403),
    ],
)  # fmt: skip
async def test_the_works_catalog_is_written_by_technical_roles_and_read_by_internal_ones(
    api: AsyncClient, objects_cleanup: None, who: str, read: int, write: int
) -> None:
    token = await token_for(api, who)

    listed = await call(api, "GET", W, token)
    created = await create(
        api, W, token, {"code": unique(), "name": "Тест", "unit": "м3", "category": "Тест"}
    )

    assert (listed.status_code, created.status_code) == (read, write)


async def test_works_are_created_changed_deleted_and_filtered(
    api: AsyncClient, objects_cleanup: None
) -> None:
    estimator = await token_for(api, "estimator")
    code = unique()
    body = {"code": code, "name": "Устройство стяжки", "unit": "м2", "category": "API-Отделка"}

    created = await create(api, W, estimator, body)
    duplicate = await create(api, W, estimator, {**body, "name": "другое"})
    work_id = created.json()["id"]
    renamed = await call(
        api, "PATCH", f"{W}/{work_id}", estimator, json={"name": "Стяжка пола", "unit": "м3"}
    )
    found = await call(api, "GET", W, estimator, params={"q": code})
    by_category = await call(api, "GET", W, estimator, params={"category": "API-Отделка"})
    categories = await call(api, "GET", f"{W}/categories", estimator)
    nothing = await call(api, "GET", W, estimator, params={"q": "%"})
    deleted = await call(api, "DELETE", f"{W}/{work_id}", estimator)
    reused = await create(api, W, estimator, body)

    assert created.status_code == 201 and (duplicate.status_code, duplicate.json()["detail"]) == (
        409,
        "work_code_taken",
    )
    assert renamed.json()["name"] == "Стяжка пола" and renamed.json()["unit"] == "м3"
    assert [item["id"] for item in found.json()] == [work_id] and len(by_category.json()) == 1
    assert "API-Отделка" in categories.json() and "Земляные работы" in categories.json()
    assert nothing.json() == []  # % экранируется, а не превращается в «любой»
    assert deleted.status_code == 204 and reused.status_code == 201  # код освободился
    assert (await call(api, "GET", f"{W}/{work_id}", estimator)).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"code": "", "name": "x", "unit": "м", "category": "x"},
        {"code": "x", "name": " ", "unit": "м", "category": "x"},
        {"code": "x", "name": "x", "unit": "", "category": "x"},
        {"code": "x", "name": "x", "unit": "м"},
        {"code": "x", "name": "x", "unit": "м", "category": "x", "extra": 1},
    ],
)
async def test_an_invalid_work_is_refused(
    api: AsyncClient, objects_cleanup: None, body: dict[str, Any]
) -> None:
    owner = await token_for(api, "owner")

    assert (await create(api, W, owner, body)).status_code == 422
    assert (await call(api, "POST", W, owner, json=body)).status_code == 422


async def test_the_seed_brings_thirty_works_in_seven_categories(api: AsyncClient) -> None:
    token = await token_for(api, "foreman")

    listed = await call(api, "GET", W, token, params={"limit": 500})

    codes = {item["code"] for item in listed.json()}
    assert {code for code in codes if not code.startswith("API-")} >= {
        "ЗМЛ-001",
        "БЖБ-005",
        "ИНЖ-004",
    }
    seeded = [
        item
        for item in listed.json()
        if item["code"][:3] in {"ЗМЛ", "БЖБ", "АРМ", "КЛД", "КРВ", "ОТД", "ИНЖ"}
    ]
    assert len(seeded) == 30 and len({item["category"] for item in seeded}) == 7


@pytest.mark.parametrize(
    ("who", "expected"),
    [
        ("owner", 200),
        ("techdir", 200),
        ("finance", 403),
        ("foreman", 403),
        ("master", 403),
        ("customer", 403),
        ("sub_a", 403),
    ],
)
async def test_the_group_directories_for_the_forms_are_for_the_roles_that_edit_the_card(
    api: AsyncClient, auth_world: AuthWorld, who: str, expected: int
) -> None:
    token = await token_for(api, who)

    entities = await call(api, "GET", "/api/v1/legal-entities", token)
    customers = await call(api, "GET", "/api/v1/customers", token)

    assert (entities.status_code, customers.status_code) == (expected, expected)
    if expected == 200 and who == "owner":
        assert {item["name"] for item in entities.json()} >= {devseed.LE_EAST, devseed.LE_WEST}
        names = {item["name"] for item in customers.json()}
        assert devseed.CUSTOMER in names
        assert not names & {devseed.SUB_A, devseed.SUB_B, devseed.SUPPLIER}  # только заказчики
