from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.modules.auth import devseed
from tests.auth_support import AuthWorld, token_for
from tests.settings_support import call, post_new, unique

pytestmark = pytest.mark.usefixtures("clean_redis", "settings_cleanup")

REPLAYED = "idempotent-replayed"

READ_ENDPOINTS = [
    "/legal-entities",
    "/counterparties",
    "/roles",
    "/projects",
    "/users",
    "/events",
]


@pytest.mark.parametrize(
    "who", ["finance", "techdir", "foreman", "accountant", "customer", "sub_a"]
)
async def test_only_the_settings_admin_can_use_the_settings_api(
    api: AsyncClient, auth_world: AuthWorld, who: str
) -> None:
    token = await token_for(api, who)
    entity = str(next(iter(auth_world.legal_entities.values())))
    user = str(auth_world.users["foreman"])

    for path in READ_ENDPOINTS:
        assert (await call(api, "GET", path, token)).status_code == 403, path
    attempts = [
        ("POST", "/legal-entities", {"name": "CFG-чужой"}),
        ("PATCH", f"/legal-entities/{entity}", {"name": "CFG-чужой"}),
        ("DELETE", f"/legal-entities/{entity}", None),
        ("POST", "/counterparties", {"name": "CFG-чужой", "types": ["supplier"]}),
        ("POST", "/users", {"full_name": "X", "email": "x@cfg.test", "role_id": str(uuid4())}),
        ("PUT", f"/users/{user}/role", {"role_id": str(auth_world.roles["owner"])}),
        ("PUT", f"/users/{user}/active", {"active": False}),
        ("PUT", f"/users/{user}/access", {"project_ids": []}),
        ("POST", f"/users/{user}/invite", None),
        ("POST", f"/users/{user}/reset-mfa", None),
    ]
    for method, path, body in attempts:
        response = await call(api, method, path, token, json=body, key=uuid4())
        assert response.status_code == 403, (method, path)


async def test_the_settings_api_needs_a_token(api: AsyncClient) -> None:
    for path in READ_ENDPOINTS:
        assert (await api.get(f"/api/v1/settings{path}")).status_code == 401, path


# --- юрлица ---


async def test_a_legal_entity_can_be_created_listed_edited_and_removed(api: AsyncClient) -> None:
    token = await token_for(api, "owner")
    name = unique("CFG-Альфа-")

    created = await post_new(api, "/legal-entities", token, {"name": name, "tax_id": " 01234 "})
    entity = created.json()
    listing = (await call(api, "GET", "/legal-entities", token)).json()
    edited = await call(
        api,
        "PATCH",
        f"/legal-entities/{entity['id']}",
        token,
        json={"name": name + "-2", "tax_id": ""},
    )
    removed = await call(api, "DELETE", f"/legal-entities/{entity['id']}", token)
    after = (await call(api, "GET", "/legal-entities", token)).json()

    assert created.status_code == 201
    assert (entity["name"], entity["tax_id"], entity["project_count"]) == (name, "01234", 0)
    assert entity["id"] in {item["id"] for item in listing}  # видно сразу, не через минуту кэша
    assert edited.status_code == 200
    assert (edited.json()["name"], edited.json()["tax_id"]) == (name + "-2", None)
    assert removed.status_code == 204
    assert entity["id"] not in {item["id"] for item in after}
    assert (await call(api, "DELETE", f"/legal-entities/{entity['id']}", token)).status_code == 404
    assert (
        await call(api, "PATCH", f"/legal-entities/{entity['id']}", token, json={"name": "x"})
    ).status_code == 404


async def test_the_new_legal_entity_can_be_used_right_away(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    """Владелец видит юрлицо без ожидания кэша и может дать на него доступ."""
    token = await token_for(api, "owner")
    entity = (await post_new(api, "/legal-entities", token, {"name": unique("CFG-Бета-")})).json()
    user = (
        await post_new(
            api,
            "/users",
            token,
            {
                "full_name": "Бухгалтер (cfg)",
                "email": f"{unique('cfg-')}@cfg.test",
                "role_id": str(auth_world.roles["accountant"]),
            },
        )
    ).json()

    granted = await call(
        api,
        "PUT",
        f"/users/{user['id']}/access",
        token,
        json={"project_ids": [], "legal_entity_ids": [entity["id"]]},
    )

    assert granted.status_code == 200 and granted.json()["legal_entity_ids"] == [entity["id"]]


async def test_a_legal_entity_with_projects_cannot_be_removed(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    entity = auth_world.legal_entities[devseed.LE_EAST]

    response = await call(api, "DELETE", f"/legal-entities/{entity}", token)
    listing = (await call(api, "GET", "/legal-entities", token)).json()

    assert response.status_code == 409 and response.json() == {"detail": "legal_entity_in_use"}
    counts = {item["id"]: item["project_count"] for item in listing}
    assert counts[str(entity)] >= 1  # объект «Восток» и проекты, созданные другими тестами


async def test_creating_under_one_key_is_idempotent_and_the_key_is_required(
    api: AsyncClient,
) -> None:
    token, key, name = await token_for(api, "owner"), uuid4(), unique("CFG-Гамма-")
    body = {"name": name}

    first = await call(api, "POST", "/legal-entities", token, json=body, key=key)
    again = await call(api, "POST", "/legal-entities", token, json=body, key=key)
    other_body = await call(
        api, "POST", "/legal-entities", token, json={"name": name + "x"}, key=key
    )
    no_key = await call(api, "POST", "/legal-entities", token, json=body)
    listing = (await call(api, "GET", "/legal-entities", token)).json()

    assert first.status_code == again.status_code == 201
    assert REPLAYED not in first.headers and again.headers[REPLAYED] == "true"
    assert first.json() == again.json()
    assert other_body.status_code == 422 and other_body.json() == {
        "detail": "idempotency_key_reused"
    }
    assert no_key.status_code == 422
    assert sum(item["name"] == name for item in listing) == 1


@pytest.mark.parametrize(
    "body", [{}, {"name": ""}, {"name": "  "}, {"name": "x" * 201}, {"name": "A", "extra": 1}]
)
async def test_a_legal_entity_needs_a_valid_name(api: AsyncClient, body: dict[str, object]) -> None:
    token = await token_for(api, "owner")

    response = await post_new(api, "/legal-entities", token, body)

    assert response.status_code == 422


# --- контрагенты ---


async def test_a_counterparty_can_have_several_types_and_is_found_by_type_and_text(
    api: AsyncClient,
) -> None:
    token = await token_for(api, "owner")
    name = unique("CFG-Поставщик-")
    made = (
        await post_new(
            api,
            "/counterparties",
            token,
            {
                "name": name,
                "tax_id": "99887766",
                "types": ["supplier", "subcontractor", "supplier"],
            },
        )
    ).json()

    def names(rows: list[dict[str, object]]) -> set[object]:
        return {row["name"] for row in rows}

    both = (await call(api, "GET", "/counterparties", token, params={"q": name[4:12]})).json()
    suppliers = (
        await call(api, "GET", "/counterparties", token, params={"type": "supplier", "q": "CFG-"})
    ).json()
    customers = (
        await call(api, "GET", "/counterparties", token, params={"type": "customer", "q": "CFG-"})
    ).json()
    by_tax = (await call(api, "GET", "/counterparties", token, params={"q": "99887766"})).json()
    wildcard = (await call(api, "GET", "/counterparties", token, params={"q": "%"})).json()

    assert sorted(made["types"]) == ["subcontractor", "supplier"]  # повтор типа убран
    assert name in names(both) and name in names(suppliers) and name not in names(customers)
    assert names(by_tax) == {name}
    assert name not in names(wildcard)  # «%» ищется как символ, а не как шаблон


async def test_a_counterparty_can_be_edited_and_removed_unless_people_belong_to_it(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")
    made = (
        await post_new(
            api, "/counterparties", token, {"name": unique("CFG-Подряд-"), "types": ["customer"]}
        )
    ).json()

    edited = await call(
        api, "PATCH", f"/counterparties/{made['id']}", token,
        json={"name": made["name"], "types": ["investor", "customer"]},
    )  # fmt: skip
    removed = await call(api, "DELETE", f"/counterparties/{made['id']}", token)
    in_use = await call(
        api, "DELETE", f"/counterparties/{auth_world.counterparties[devseed.SUB_A]}", token
    )

    assert edited.status_code == 200 and sorted(edited.json()["types"]) == ["customer", "investor"]
    assert removed.status_code == 204
    assert in_use.status_code == 409 and in_use.json() == {"detail": "counterparty_in_use"}
    assert (await call(api, "DELETE", f"/counterparties/{made['id']}", token)).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"name": "CFG-x", "types": []},
        {"name": "CFG-x", "types": ["pirate"]},
        {"name": "CFG-x"},
        {"name": "", "types": ["customer"]},
    ],
)
async def test_a_counterparty_needs_a_name_and_known_types(
    api: AsyncClient, body: dict[str, object]
) -> None:
    token = await token_for(api, "owner")

    assert (await post_new(api, "/counterparties", token, body)).status_code == 422


async def test_roles_and_projects_are_listed_for_the_forms(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "owner")

    roles = (await call(api, "GET", "/roles", token)).json()
    projects = (await call(api, "GET", "/projects", token)).json()

    assert {role["code"] for role in roles} == set(auth_world.roles)
    assert roles[0]["code"] == "owner"  # по убыванию ранга
    assert {str(auth_world.projects["east"]), str(auth_world.projects["west"])} <= {
        p["id"] for p in projects
    }
