import asyncio
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.core.context import use_context
from app.core.db import get_sessionmaker
from app.modules.idempotency.service import fingerprint
from tests.auth_support import AuthWorld, bearer, token_for
from tests.db_support import context

pytestmark = pytest.mark.usefixtures("clean_redis")

URL = "/api/v1/_test/projects"
REPLAYED = "idempotent-replayed"


def entity(world: AuthWorld) -> UUID:
    return next(iter(world.legal_entities.values()))


async def create(
    client: AsyncClient,
    token: str,
    key: UUID | str | None,
    world: AuthWorld,
    name: str,
    **extra: object,
) -> Response:
    headers = bearer(token)
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    body = {"name": name, "legal_entity_id": str(entity(world)), **extra}
    return await client.post(URL, headers=headers, json=body)


def count(engine: Engine, name: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM project WHERE name = :name"), {"name": name}
            ).scalar_one()
        )


def unique_name() -> str:
    return f"idem-{uuid4().hex[:10]}"


async def test_the_first_request_runs_and_a_repeat_returns_the_stored_answer(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    token, key, name = await token_for(api, "owner"), uuid4(), unique_name()

    first = await create(api, token, key, auth_world, name)
    second = await create(api, token, key, auth_world, name)
    third = await create(api, token, key, auth_world, name)

    assert first.status_code == 201 and REPLAYED not in first.headers
    assert second.status_code == third.status_code == 201
    assert second.headers[REPLAYED] == third.headers[REPLAYED] == "true"
    assert first.json() == second.json() == third.json()  # тот же id: запись одна
    assert count(worker_sync_engine, name) == 1


async def test_the_same_key_with_another_body_is_422_and_changes_nothing(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    token, key = await token_for(api, "owner"), uuid4()
    name, other = unique_name(), unique_name()
    await create(api, token, key, auth_world, name)

    clash = await create(api, token, key, auth_world, other)

    assert clash.status_code == 422 and clash.json() == {"detail": "idempotency_key_reused"}
    assert count(worker_sync_engine, other) == 0 and count(worker_sync_engine, name) == 1


async def test_the_key_header_is_required_and_must_be_a_uuid(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    token, name = await token_for(api, "owner"), unique_name()

    missing = await create(api, token, None, auth_world, name)
    malformed = await create(api, token, "not-a-uuid", auth_world, name)

    assert missing.status_code == malformed.status_code == 422
    assert count(worker_sync_engine, name) == 0


async def test_a_failed_request_leaves_no_trace_and_the_key_stays_usable(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    token, key, name = await token_for(api, "owner"), uuid4(), unique_name()

    failed = await create(api, token, key, auth_world, name, fail=True)
    retried = await create(api, token, key, auth_world, name)

    assert failed.status_code == 400 and count(worker_sync_engine, name) == 1
    assert retried.status_code == 201 and REPLAYED not in retried.headers


async def test_parallel_identical_requests_create_exactly_one_record(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    token, key, name = await token_for(api, "owner"), uuid4(), unique_name()

    responses = await asyncio.gather(
        *(create(api, token, key, auth_world, name, delay_ms=400) for _ in range(6))
    )

    assert [response.status_code for response in responses] == [201] * 6
    assert len({response.json()["id"] for response in responses}) == 1
    assert sum(REPLAYED not in response.headers for response in responses) == 1  # исполнил один
    assert count(worker_sync_engine, name) == 1


async def test_parallel_requests_with_different_keys_each_create_their_own_record(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    token, name = await token_for(api, "owner"), unique_name()

    responses = await asyncio.gather(
        *(create(api, token, uuid4(), auth_world, name, delay_ms=100) for _ in range(4))
    )

    assert [response.status_code for response in responses] == [201] * 4
    assert count(worker_sync_engine, name) == 4


async def test_a_key_belongs_to_its_user(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    owner, techdir = await token_for(api, "owner"), await token_for(api, "techdir")
    key, name = uuid4(), unique_name()

    by_owner = await create(api, owner, key, auth_world, name)
    by_techdir = await create(api, techdir, key, auth_world, name)
    replay = await create(api, techdir, key, auth_world, name)

    assert by_owner.status_code == by_techdir.status_code == 201
    assert by_owner.json()["id"] != by_techdir.json()["id"]
    assert replay.headers[REPLAYED] == "true" and replay.json() == by_techdir.json()
    assert count(worker_sync_engine, name) == 2


async def test_a_replay_returns_the_stored_response_not_a_fresh_execution(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    token, key, name = await token_for(api, "owner"), uuid4(), unique_name()
    first = await create(api, token, key, auth_world, name)
    with worker_sync_engine.begin() as connection:
        connection.execute(text("UPDATE project SET name = 'renamed' WHERE name = :n"), {"n": name})

    replay = await create(api, token, key, auth_world, name)

    assert replay.json() == first.json() and replay.headers[REPLAYED] == "true"
    assert count(worker_sync_engine, name) == 0  # заново ничего не создавалось


async def test_a_request_still_running_elsewhere_gives_409_after_the_lock_timeout(
    api: AsyncClient,
    auth_world: AuthWorld,
    monkeypatch: pytest.MonkeyPatch,
    worker_sync_engine: Engine,
) -> None:
    token, key, name = await token_for(api, "owner"), uuid4(), unique_name()
    owner_id = auth_world.users["owner"]
    monkeypatch.setenv("IDEMPOTENCY_LOCK_TIMEOUT_MS", "300")
    get_settings.cache_clear()
    try:
        with use_context(context(owner_id, all_users=True)):
            async with get_sessionmaker()() as holder:
                await holder.execute(  # «первый запрос» занял ключ и ещё не завершился
                    text(
                        "INSERT INTO idempotency_key (user_id, key, request_hash, response_code,"
                        " expires_at) VALUES (:u, :k, :h, 0, now() + interval '1 hour')"
                    ),
                    {"u": owner_id, "k": key, "h": b"h" * 32},
                )
                blocked = await create(api, token, key, auth_world, name)
                await holder.rollback()
        retried = await create(api, token, key, auth_world, name)
    finally:
        monkeypatch.delenv("IDEMPOTENCY_LOCK_TIMEOUT_MS")
        get_settings.cache_clear()

    assert blocked.status_code == 409 and blocked.json() == {"detail": "request_in_progress"}
    assert blocked.headers["retry-after"] == "1"
    assert retried.status_code == 201 and count(worker_sync_engine, name) == 1


async def test_the_stored_record_has_a_24_hour_lifetime_and_a_json_body(
    api: AsyncClient, auth_world: AuthWorld, owner_sync_engine: Engine
) -> None:
    token, key = await token_for(api, "owner"), uuid4()
    response = await create(api, token, key, auth_world, unique_name())

    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        row = connection.execute(
            text(
                "SELECT response_code, response_body, expires_at - stored_at AS lifetime"
                " FROM idempotency_key WHERE key = :k"
            ),
            {"k": key},
        ).one()

    assert row.response_code == 201 and row.response_body == response.json()
    assert abs(row.lifetime.total_seconds() - 24 * 3600) < 5


async def test_without_permission_the_endpoint_refuses_before_touching_the_key(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    token, key, name = (
        await token_for(api, "lawyer"),
        uuid4(),
        unique_name(),
    )  # objects: только чтение

    response = await create(api, token, key, auth_world, name)

    assert response.status_code == 403 and count(worker_sync_engine, name) == 0


def test_the_fingerprint_ignores_field_order_but_not_method_path_or_values() -> None:
    base = fingerprint("POST", "/a", {"x": 1, "y": [1, 2]})

    assert base == fingerprint("post", "/a", {"y": [1, 2], "x": 1})
    assert base != fingerprint("PUT", "/a", {"x": 1, "y": [1, 2]})
    assert base != fingerprint("POST", "/b", {"x": 1, "y": [1, 2]})
    assert base != fingerprint("POST", "/a", {"x": 2, "y": [1, 2]})
    assert base != fingerprint("POST", "/a", {"x": 1, "y": [2, 1]})
    assert len(base) == 32
    uid = uuid4()
    assert fingerprint("POST", "/a", {"id": uid}) == fingerprint("POST", "/a", {"id": str(uid)})
