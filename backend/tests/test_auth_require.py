from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.modules.auth.deps import get_principal, require
from app.modules.auth.principal import Principal
from app.modules.rbac.enums import Action, Module


def principal(
    permissions: set[str],
    projects: set[UUID] | None = None,
    *,
    scope: str = "project",
) -> Principal:
    return Principal(
        user_id=uuid4(),
        full_name="Тест",
        role_code="foreman",
        role_scope=scope,
        role_ver=1,
        counterparty_id=None,
        permissions=frozenset(permissions),
        project_ids=frozenset(projects or ()),
        legal_entity_ids=frozenset(),
        sid=uuid4(),
        amr=("pwd",),
        otp_at=None,
    )


class Holder:
    current: Principal


@pytest.fixture
def probe() -> Iterator[tuple[FastAPI, Holder]]:
    app = FastAPI()
    holder = Holder()

    @app.get(
        "/projects/{project_id}",
        dependencies=[Depends(require(Module.OBJECTS, Action.READ, project_param="project_id"))],
    )
    async def project_read(project_id: str) -> dict[str, str]:
        return {"ok": project_id}

    @app.get("/plain", dependencies=[Depends(require(Module.FINANCE, Action.READ))])
    async def plain() -> dict[str, str]:
        return {"ok": "plain"}

    @app.get(
        "/after-approval",
        dependencies=[
            Depends(require(Module.WORK_ACTUAL, Action.WRITE, qualifier="after_approval"))
        ],
    )
    async def after_approval() -> dict[str, str]:
        return {"ok": "qualified"}

    app.dependency_overrides[get_principal] = lambda: holder.current
    yield app, holder


async def call(probe: tuple[FastAPI, Holder], who: Principal, path: str) -> int:
    app, holder = probe
    holder.current = who
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return (await client.get(path)).status_code


async def test_permission_and_object_present_gives_200(probe: tuple[FastAPI, Holder]) -> None:
    project = uuid4()

    assert await call(probe, principal({"objects:read"}, {project}), f"/projects/{project}") == 200


async def test_a_foreign_object_is_404_whether_or_not_the_permission_is_held(
    probe: tuple[FastAPI, Holder],
) -> None:
    foreign = uuid4()

    with_permission = await call(
        probe, principal({"objects:read"}, {uuid4()}), f"/projects/{foreign}"
    )
    without = await call(probe, principal(set(), {uuid4()}), f"/projects/{foreign}")

    assert with_permission == without == 404  # существование чужого объекта не раскрываем


async def test_a_missing_permission_on_an_own_object_is_403(probe: tuple[FastAPI, Holder]) -> None:
    project = uuid4()

    assert await call(probe, principal({"objects:write"}, {project}), f"/projects/{project}") == 403


async def test_a_path_parameter_that_is_not_a_uuid_is_404(probe: tuple[FastAPI, Holder]) -> None:
    assert await call(probe, principal({"objects:read"}, {uuid4()}), "/projects/12345") == 404


async def test_without_a_project_parameter_only_the_permission_matters(
    probe: tuple[FastAPI, Holder],
) -> None:
    assert await call(probe, principal({"finance:read"}), "/plain") == 200
    assert await call(probe, principal({"finance:write"}), "/plain") == 403
    assert await call(probe, principal(set()), "/plain") == 403


async def test_a_qualified_right_is_not_implied_by_the_plain_one(
    probe: tuple[FastAPI, Holder],
) -> None:
    plain_only = principal({"work_actual:write"})
    qualified = principal({"work_actual:write", "work_actual:write:after_approval"})

    assert await call(probe, plain_only, "/after-approval") == 403
    assert await call(probe, qualified, "/after-approval") == 200


def test_has_and_scope_helpers() -> None:
    group = principal({"settings:admin"}, scope="group")
    external = principal(set(), scope="counterparty")
    admin_in_project_scope = principal({"settings:admin"})
    plain = principal({"objects:read"})

    assert group.has(Module.SETTINGS, Action.ADMIN) and not plain.has(Module.SETTINGS, Action.ADMIN)
    assert plain.has(Module.OBJECTS, Action.READ) and not plain.has(Module.OBJECTS, Action.WRITE)
    assert group.is_internal and not external.is_internal
    assert group.sees_all_users and admin_in_project_scope.sees_all_users
    assert not plain.sees_all_users and not external.sees_all_users


def test_the_context_built_from_a_principal_matches_its_scope() -> None:
    project = uuid4()
    request_id = uuid4()
    who = principal({"objects:read"}, {project})

    context = who.to_context(request_id)

    assert context.user_id == who.user_id and context.is_internal is True
    assert context.project_ids == frozenset({project}) and context.request_id == request_id
    assert context.counterparty_id is None and context.sees_all_users is False
