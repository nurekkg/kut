"""Сквозной путь «логин → токен → контекст → RLS» по HTTP, без подмены контекста в тесте."""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import Engine, text

from app.core.redis import get_redis
from tests.auth_support import AuthWorld, bearer, token_for

pytestmark = pytest.mark.usefixtures("clean_redis")

API = "/api/v1"
EAST_USERS = {"foreman", "master", "estimator", "storekeeper", "lawyer", "customer", "sub_a"}
WEST_USERS = {"accountant", "procurement", "lawyer", "supplier", "sub_b"}
GROUP_USERS = {"owner", "finance", "techdir"}


async def get(client: AsyncClient, who: str, path: str) -> Response:
    token = await token_for(client, who)
    return await client.get(f"{API}{path}", headers=bearer(token))


def project_ids(response_json: list[dict[str, str]]) -> set[UUID]:
    return {UUID(item["id"]) for item in response_json}


def live_projects(engine: Engine) -> set[UUID]:
    with engine.connect() as connection:
        return set(connection.scalars(text("SELECT id FROM project WHERE deleted_at IS NULL")))


@pytest.mark.parametrize(
    ("who", "visible"),
    [
        ("foreman", {"east"}),
        ("master", {"east"}),
        ("estimator", {"east"}),
        ("storekeeper", {"east"}),
        ("customer", {"east"}),
        ("sub_a", {"east"}),
        ("procurement", {"west"}),
        ("accountant", {"west"}),
        ("sub_b", {"west"}),
        ("lawyer", {"east", "west"}),
    ],
)
async def test_scoped_roles_list_only_their_own_projects(
    api: AsyncClient, auth_world: AuthWorld, who: str, visible: set[str]
) -> None:
    response = await get(api, who, "/projects")

    assert response.status_code == 200
    assert project_ids(response.json()) == {auth_world.projects[name] for name in visible}
    assert set(response.json()[0]) == {"id", "name", "status", "legal_entity_id"}


@pytest.mark.parametrize("who", ["owner", "finance", "techdir"])
async def test_group_roles_see_every_live_project_including_techdir_by_pos_decision(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, who: str
) -> None:
    response = await get(api, who, "/projects")

    seen = project_ids(response.json())
    assert response.status_code == 200
    assert seen == live_projects(worker_sync_engine)
    assert set(auth_world.projects.values()) <= seen


async def test_subcontractor_a_sees_only_own_and_a_foreign_id_is_404_not_403(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    east, west = auth_world.projects["east"], auth_world.projects["west"]
    token = await token_for(api, "sub_a")

    own = await api.get(f"{API}/projects/{east}", headers=bearer(token))
    foreign = await api.get(f"{API}/projects/{west}", headers=bearer(token))
    unknown = await api.get(f"{API}/projects/{uuid4()}", headers=bearer(token))
    garbage = await api.get(f"{API}/projects/not-a-uuid", headers=bearer(token))

    assert own.status_code == 200 and own.json()["id"] == str(east)
    assert foreign.status_code == unknown.status_code == garbage.status_code == 404
    assert foreign.json() == unknown.json() == garbage.json() == {"detail": "not_found"}


async def test_subcontractor_b_mirrors_subcontractor_a(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "sub_b")

    own = await api.get(f"{API}/projects/{auth_world.projects['west']}", headers=bearer(token))
    foreign = await api.get(f"{API}/projects/{auth_world.projects['east']}", headers=bearer(token))

    assert (own.status_code, foreign.status_code) == (200, 404)


async def test_foreman_reads_own_project_and_gets_404_for_the_other_one(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "foreman")

    east = await api.get(f"{API}/projects/{auth_world.projects['east']}", headers=bearer(token))
    west = await api.get(f"{API}/projects/{auth_world.projects['west']}", headers=bearer(token))

    assert (east.status_code, west.status_code) == (200, 404)


async def test_group_roles_reach_both_projects_by_id(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    for who in ("owner", "finance", "techdir"):
        token = await token_for(api, who)
        for project in auth_world.projects.values():
            response = await api.get(f"{API}/projects/{project}", headers=bearer(token))
            assert response.status_code == 200, (who, project)


async def test_a_role_without_the_module_gets_403_on_its_own_object_and_404_on_a_foreign_one(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    token = await token_for(api, "supplier")  # objects: «—» в матрице ТЗ §7.3

    own = await api.get(f"{API}/projects/{auth_world.projects['west']}", headers=bearer(token))
    foreign = await api.get(f"{API}/projects/{auth_world.projects['east']}", headers=bearer(token))
    listing = await api.get(f"{API}/projects", headers=bearer(token))

    assert own.status_code == 403 and own.json() == {"detail": "forbidden"}
    assert foreign.status_code == 404
    assert listing.status_code == 403


async def test_soft_deleted_projects_are_invisible_even_to_the_owner(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    with worker_sync_engine.begin() as connection:
        ghost = connection.execute(
            text(
                "INSERT INTO project (name, legal_entity_id, deleted_at)"
                " VALUES ('Удалённый (e2e)', :le, now()) RETURNING id"
            ),
            {"le": next(iter(auth_world.legal_entities.values()))},
        ).scalar_one()
    token = await token_for(api, "owner")

    listing = await api.get(f"{API}/projects", headers=bearer(token))
    direct = await api.get(f"{API}/projects/{ghost}", headers=bearer(token))

    assert ghost not in project_ids(listing.json())
    assert direct.status_code == 404


@pytest.mark.parametrize("path", ["/projects", "/users", f"/projects/{uuid4()}", "/_test/whoami"])
async def test_every_protected_endpoint_answers_401_without_a_token(
    api: AsyncClient, path: str
) -> None:
    assert (await api.get(f"{API}{path}")).status_code == 401


@pytest.mark.parametrize(
    ("who", "colleagues"),
    [
        ("master", EAST_USERS),
        ("foreman", EAST_USERS),
        ("accountant", WEST_USERS | {"accountant"}),
        ("lawyer", EAST_USERS | WEST_USERS),
    ],
)
async def test_internal_roles_see_themselves_and_users_who_share_a_project(
    api: AsyncClient, auth_world: AuthWorld, who: str, colleagues: set[str]
) -> None:
    response = await get(api, who, "/users")

    seen = {UUID(item["id"]) for item in response.json()}
    assert response.status_code == 200
    assert seen == {auth_world.users[name] for name in colleagues | {who}}
    assert not seen & {
        auth_world.users[name] for name in GROUP_USERS
    }  # у группы нет объектов в UPA
    assert {"id", "full_name", "role"} == set(response.json()[0])


async def test_the_master_does_not_see_the_other_object_or_the_management(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    response = await get(api, "master", "/users")

    seen = {UUID(item["id"]) for item in response.json()}
    for hidden in ("procurement", "accountant", "supplier", "sub_b", "owner", "finance", "techdir"):
        assert auth_world.users[hidden] not in seen, hidden


@pytest.mark.parametrize("who", ["owner", "finance", "techdir"])
async def test_group_roles_see_all_users(api: AsyncClient, auth_world: AuthWorld, who: str) -> None:
    response = await get(api, who, "/users")

    seen = {UUID(item["id"]) for item in response.json()}
    assert response.status_code == 200
    assert set(auth_world.users.values()) <= seen


@pytest.mark.parametrize("who", ["customer", "sub_a", "sub_b"])
async def test_external_roles_see_only_themselves_in_the_user_list(
    api: AsyncClient, auth_world: AuthWorld, who: str
) -> None:
    response = await get(api, who, "/users")

    assert response.status_code == 200
    assert [UUID(item["id"]) for item in response.json()] == [auth_world.users[who]]


async def test_a_role_with_settings_admin_sees_all_users_even_without_group_scope(
    api: AsyncClient, auth_world: AuthWorld, owner_sync_engine: Engine
) -> None:
    before = {UUID(item["id"]) for item in (await get(api, "estimator", "/users")).json()}
    with owner_sync_engine.begin() as connection:
        grant = connection.execute(
            text(
                "INSERT INTO role_permission (role_id, permission_id)"
                " SELECT :role, p.id FROM permission p"
                " WHERE p.module = 'settings' AND p.action = 'admin' AND p.qualifier IS NULL"
                " RETURNING id"
            ),
            {"role": auth_world.roles["estimator"]},
        ).scalar_one()
    try:
        await get_redis().flushdb()  # кэш прав живёт до минуты: сбрасываем, чтобы не ждать
        after = {UUID(item["id"]) for item in (await get(api, "estimator", "/users")).json()}
    finally:
        with owner_sync_engine.begin() as connection:
            connection.execute(
                text("UPDATE role_permission SET deleted_at = now() WHERE id = :id"), {"id": grant}
            )
        await get_redis().flushdb()

    assert set(auth_world.users.values()) <= after and after > before


async def test_external_roles_run_on_the_portal_login_and_internal_ones_on_the_app_login(
    api: AsyncClient,
) -> None:
    seen = {}
    for who in ("sub_a", "customer", "foreman", "techdir"):
        response = await get(api, who, "/_test/whoami")
        seen[who] = response.json()

    assert {who: item["db_user"] for who, item in seen.items()} == {
        "sub_a": "kut_portal",
        "customer": "kut_portal",
        "foreman": "kut_app",
        "techdir": "kut_app",
    }
    assert seen["sub_a"]["internal"] == "false" and seen["foreman"]["internal"] == "true"
    assert seen["techdir"]["all_users"] == "true" and seen["foreman"]["all_users"] == "false"
    assert len(seen["foreman"]["request_id"]) == 36


async def test_the_context_does_not_leak_between_requests_on_pooled_connections(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    owner = await token_for(api, "owner")
    sub = await token_for(api, "sub_a")

    for _ in range(6):  # пул переиспользует соединения: GUC не должны переходить между запросами
        big = await api.get(f"{API}/projects", headers=bearer(owner))
        small = await api.get(f"{API}/projects", headers=bearer(sub))
        assert len(big.json()) >= 2
        assert project_ids(small.json()) == {auth_world.projects["east"]}
