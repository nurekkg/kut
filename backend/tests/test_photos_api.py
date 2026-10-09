"""S3a: фото факта - загрузка прямо в S3 по подписанной ссылке, запись только после HEAD,
скачивание по короткой ссылке при доступе к объекту. Тесты ходят в настоящий MinIO (бакет
тестов)."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from tests.auth_support import AuthWorld, bearer, token_for
from tests.db_support import owner_rows
from tests.gpr_support import Plan, make_plan, purge

P = "/api/v1/projects"
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-bytes" * 50
PNG = b"\x89PNG\r\n\x1a\n" + b"png-bytes" * 40
MAX_BYTES = 15 * 1024 * 1024


@pytest.fixture(autouse=True)
async def cleanup(worker_engine: AsyncEngine, auth_world: AuthWorld) -> AsyncIterator[None]:
    yield
    await purge(worker_engine, auth_world)


@pytest.fixture
async def plan(worker_engine: AsyncEngine, auth_world: AuthWorld) -> Plan:
    return await make_plan(worker_engine, auth_world)


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    """Клиент «браузера»: ходит по подписанным ссылкам напрямую в S3, мимо API."""
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


def photos(world: AuthWorld, *tail: str, project: str = "east") -> str:
    return "/".join([f"{P}/{world.projects[project]}/photos", *tail])


def facts(world: AuthWorld, *tail: str, project: str = "east") -> str:
    return "/".join([f"{P}/{world.projects[project]}/work-actuals", *tail])


async def send(
    api: AsyncClient,
    method: str,
    path: str,
    who: str,
    *,
    json: Any = None,
    key: UUID | None = None,
    params: Any = None,
) -> Response:
    headers = bearer(await token_for(api, who))
    if key is not None:
        headers["Idempotency-Key"] = str(key)
    return await api.request(method, path, headers=headers, json=json, params=params)


async def new_fact(api: AsyncClient, world: AuthWorld, who: str, plan: Plan, **extra: Any) -> str:
    body = {
        "node_id": str(plan.node),
        "work_id": str(plan.work),
        "volume": "5",
        "work_date": "2026-09-03",
        **extra,
    }
    response = await send(api, "POST", facts(world), who, json=body, key=uuid4())
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def presign(
    api: AsyncClient,
    world: AuthWorld,
    who: str,
    fact: str,
    files: list[tuple[str, int]],
    project: str = "east",
) -> Response:
    body = {
        "work_actual_id": fact,
        "files": [{"content_type": kind, "size_bytes": size} for kind, size in files],
    }
    return await send(api, "POST", photos(world, "presign", project=project), who, json=body)


async def upload(s3: httpx.AsyncClient, item: dict[str, Any], body: bytes) -> httpx.Response:
    return await s3.put(item["upload_url"], content=body, headers=item["headers"])


async def confirm(
    api: AsyncClient,
    world: AuthWorld,
    who: str,
    fact: str,
    keys: list[str],
    *,
    key: UUID | None = None,
    **extra: Any,
) -> Response:
    body = {"work_actual_id": fact, "items": [{"key": item, **extra} for item in keys]}
    return await send(api, "POST", photos(world, "confirm"), who, json=body, key=key or uuid4())


async def uploaded(
    api: AsyncClient, world: AuthWorld, s3: httpx.AsyncClient, who: str, fact: str
) -> list[dict[str, Any]]:
    """Presign двух файлов (jpeg, png) и загрузка их в S3: список выданных ссылок."""
    response = await presign(
        api, world, who, fact, [("image/jpeg", len(JPEG)), ("image/png", len(PNG))]
    )
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    for item, body in zip(items, (JPEG, PNG), strict=True):
        assert (await upload(s3, item, body)).status_code == 200
    return items


# --- загрузка и подтверждение -------------------------------------------------------------------


async def test_files_go_straight_to_s3_and_the_record_appears_only_after_the_head_check(
    api: AsyncClient, auth_world: AuthWorld, s3: httpx.AsyncClient, plan: Plan
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)
    east = auth_world.projects["east"]

    items = await uploaded(api, auth_world, s3, "master", fact)

    assert [item["method"] for item in items] == ["PUT", "PUT"]
    for item, extension in zip(items, ("jpg", "png"), strict=True):
        prefix = f"project/{east}/fact/{fact}/"
        assert item["key"].startswith(prefix) and item["key"].endswith(f".{extension}")
        assert "X-Amz-Signature" in item["upload_url"] and item["expires_in"] == 900
    before = await send(api, "GET", photos(auth_world), "master", params={"work_actual_id": fact})
    assert before.json() == []  # файлы в S3 есть, подтверждения ещё не было

    created = await confirm(
        api,
        auth_world,
        "master",
        fact,
        [item["key"] for item in items],
        latitude="42.8746",
        longitude="74.5698",
    )

    rows = created.json()
    assert created.status_code == 201, created.text
    assert sorted((row["content_type"], row["size_bytes"]) for row in rows) == sorted(
        [("image/jpeg", len(JPEG)), ("image/png", len(PNG))]
    )
    assert all(
        row["latitude"] == "42.874600" and row["uploaded_by"] == str(auth_world.users["master"])
        for row in rows
    )
    state = await send(api, "GET", facts(auth_world, fact), "master")
    assert state.json()["photo_count"] == 2


async def test_a_photo_is_downloaded_by_a_short_link_and_the_bytes_are_the_uploaded_ones(
    api: AsyncClient, auth_world: AuthWorld, s3: httpx.AsyncClient, plan: Plan
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)
    items = await uploaded(api, auth_world, s3, "master", fact)
    rows = (await confirm(api, auth_world, "master", fact, [item["key"] for item in items])).json()
    by_type = {row["content_type"]: row for row in rows}

    link = await send(
        api, "GET", photos(auth_world, by_type["image/png"]["id"], "download-url"), "foreman"
    )

    assert link.status_code == 200 and link.json()["expires_in"] == 120
    fetched = await s3.get(link.json()["url"])
    assert fetched.status_code == 200 and fetched.content == PNG
    assert fetched.headers["content-type"] == "image/png"
    listed = await send(
        api,
        "GET",
        photos(auth_world),
        "foreman",
        params={"work_actual_id": fact, "with_urls": "true"},
    )
    assert all(row["url"] and row["url_expires_in"] == 120 for row in listed.json())
    assert (await s3.get(listed.json()[0]["url"])).status_code == 200


async def test_confirming_a_file_that_was_never_uploaded_is_refused_and_leaves_no_record(
    api: AsyncClient, auth_world: AuthWorld, s3: httpx.AsyncClient, plan: Plan
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)
    issued = (await presign(api, auth_world, "master", fact, [("image/jpeg", 1000)])).json()[
        "items"
    ]

    response = await confirm(api, auth_world, "master", fact, [issued[0]["key"]])

    assert (response.status_code, response.json()["detail"]) == (409, "photo_not_uploaded")
    listed = await send(api, "GET", photos(auth_world), "master", params={"work_actual_id": fact})
    assert listed.json() == []


async def test_one_missing_file_of_several_refuses_the_whole_confirmation(
    api: AsyncClient, auth_world: AuthWorld, s3: httpx.AsyncClient, plan: Plan
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)
    issued = (
        await presign(
            api, auth_world, "master", fact, [("image/jpeg", len(JPEG)), ("image/png", len(PNG))]
        )
    ).json()["items"]
    assert (await upload(s3, issued[0], JPEG)).status_code == 200  # второй файл не загружен

    response = await confirm(api, auth_world, "master", fact, [item["key"] for item in issued])

    assert response.status_code == 409
    listed = await send(api, "GET", photos(auth_world), "master", params={"work_actual_id": fact})
    assert listed.json() == []


@pytest.mark.parametrize(
    "bad_key",
    [
        "project/{east}/fact/{other}/{uid}.jpg",  # ключ чужого факта
        "project/{west}/fact/{fact}/{uid}.jpg",  # ключ чужого объекта
        "project/{east}/fact/{fact}/../../x.jpg",  # выход из каталога
        "project/{east}/fact/{fact}/{uid}.gif",  # неразрешённое расширение
        "project/{east}/fact/{fact}/not-a-uuid.jpg",
        "other-prefix/{uid}.jpg",
        "",
    ],
)
async def test_only_keys_issued_for_this_fact_and_object_are_accepted(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan, bad_key: str
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)
    key = bad_key.format(east=auth_world.projects["east"], west=auth_world.projects["west"],
                         fact=fact, other=uuid4(), uid=uuid4())  # fmt: skip

    response = await confirm(api, auth_world, "master", fact, [key])

    assert response.status_code == 422, (key, response.text)


# --- ограничения загрузки -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "files",
    [
        [("image/gif", 100)],
        [("application/pdf", 100)],
        [("text/html", 100)],
        [("image/jpeg", 0)],
        [("image/jpeg", -5)],
        [("image/jpeg", MAX_BYTES + 1)],
        [("image/jpeg", 100)] * 11,
        [],
    ],
)
async def test_the_type_and_the_size_of_a_file_are_limited_before_a_link_is_issued(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan, files: list[tuple[str, int]]
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)

    response = await presign(api, auth_world, "master", fact, files)

    assert response.status_code == 422, response.text


@pytest.mark.parametrize("kind", ["image/jpeg", "image/png", "image/heic", "image/webp"])
async def test_the_four_allowed_types_get_a_link_up_to_the_size_limit(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan, kind: str
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)

    response = await presign(api, auth_world, "master", fact, [(kind, MAX_BYTES)])

    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["headers"] == {
        "Content-Type": kind,
        "If-None-Match": "*",
    }


async def test_s3_refuses_a_file_that_differs_from_what_was_signed(
    api: AsyncClient, auth_world: AuthWorld, s3: httpx.AsyncClient, plan: Plan
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)
    issued = (await presign(api, auth_world, "master", fact, [("image/jpeg", len(JPEG))])).json()[
        "items"
    ][0]

    wrong_type = await s3.put(
        issued["upload_url"], content=JPEG, headers={"Content-Type": "text/html"}
    )
    longer = await s3.put(issued["upload_url"], content=JPEG + b"x", headers=issued["headers"])
    shorter = await s3.put(issued["upload_url"], content=JPEG[:-1], headers=issued["headers"])

    assert (
        wrong_type.status_code >= 400 and longer.status_code >= 400 and shorter.status_code >= 400
    )
    nothing = await confirm(api, auth_world, "master", fact, [issued["key"]])
    assert nothing.status_code == 409  # ни один из неверных запросов объект не создал
    assert (await upload(s3, issued, JPEG)).status_code == 200  # а точный файл принимается


async def test_a_key_is_written_once_and_the_same_link_cannot_replace_the_file(
    api: AsyncClient, auth_world: AuthWorld, s3: httpx.AsyncClient, plan: Plan
) -> None:
    """Ссылка действует до конца срока, поэтому запись ключа одноразовая (If-None-Match: *): иначе
    автор подменил бы файл, уже подтверждённый и показанный как доказательство факта."""
    fact = await new_fact(api, auth_world, "master", plan)
    issued = (await presign(api, auth_world, "master", fact, [("image/jpeg", len(JPEG))])).json()[
        "items"
    ][0]
    substitute = b"\xff\xd8\xff\xe0" + b"x" * (len(JPEG) - 4)  # той же длины и типа

    without_header = await s3.put(
        issued["upload_url"], content=JPEG, headers={"Content-Type": "image/jpeg"}
    )
    first = await upload(s3, issued, JPEG)
    again = await upload(s3, issued, substitute)

    assert without_header.status_code >= 400  # заголовок в подписи: без него подпись не сходится
    assert first.status_code == 200
    assert again.status_code == 412  # ключ уже записан
    photo = (await confirm(api, auth_world, "master", fact, [issued["key"]])).json()[0]
    link = (
        await send(api, "GET", photos(auth_world, photo["id"], "download-url"), "master")
    ).json()
    assert (await s3.get(link["url"])).content == JPEG  # хранится первый файл


async def test_an_expired_upload_link_is_refused_by_s3(
    api: AsyncClient,
    auth_world: AuthWorld,
    s3: httpx.AsyncClient,
    plan: Plan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)
    monkeypatch.setattr(get_settings(), "photo_upload_ttl_seconds", 1)
    issued = (await presign(api, auth_world, "master", fact, [("image/jpeg", len(JPEG))])).json()[
        "items"
    ][0]
    assert issued["expires_in"] == 1

    await asyncio.sleep(2.5)
    late = await upload(s3, issued, JPEG)

    assert late.status_code == 403
    assert (await confirm(api, auth_world, "master", fact, [issued["key"]])).status_code == 409


async def test_an_expired_download_link_is_refused_by_s3(
    api: AsyncClient,
    auth_world: AuthWorld,
    s3: httpx.AsyncClient,
    plan: Plan,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)
    items = await uploaded(api, auth_world, s3, "master", fact)
    photo = (await confirm(api, auth_world, "master", fact, [items[0]["key"]])).json()[0]
    monkeypatch.setattr(get_settings(), "photo_download_ttl_seconds", 1)

    link = (
        await send(api, "GET", photos(auth_world, photo["id"], "download-url"), "master")
    ).json()
    assert (await s3.get(link["url"])).status_code == 200
    await asyncio.sleep(2.5)

    assert (await s3.get(link["url"])).status_code == 403


# --- доступ ---------------------------------------------------------------------------------------


async def test_a_link_for_a_foreign_object_or_a_foreign_fact_is_not_issued(
    api: AsyncClient, auth_world: AuthWorld, s3: httpx.AsyncClient, plan: Plan
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)

    foreign_object = await presign(
        api, auth_world, "master", fact, [("image/jpeg", 10)], project="west"
    )
    unknown_fact = await presign(api, auth_world, "master", str(uuid4()), [("image/jpeg", 10)])
    through_west = await presign(
        api, auth_world, "techdir", fact, [("image/jpeg", 10)], project="west"
    )

    assert foreign_object.status_code == 404
    assert unknown_fact.status_code == 404
    assert through_west.status_code == 404  # techdir видит «Запад», но факт «Востока» там не его


async def test_a_photo_of_another_object_is_not_downloadable_through_the_wrong_path(
    api: AsyncClient, auth_world: AuthWorld, s3: httpx.AsyncClient, plan: Plan
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)
    items = await uploaded(api, auth_world, s3, "master", fact)
    photo = (await confirm(api, auth_world, "master", fact, [items[0]["key"]])).json()[0]

    own = await send(api, "GET", photos(auth_world, photo["id"], "download-url"), "master")
    foreign = await send(api, "GET", photos(auth_world, photo["id"], "download-url"), "sub_b")
    wrong_path = await send(
        api, "GET", photos(auth_world, photo["id"], "download-url", project="west"), "techdir"
    )

    assert own.status_code == 200
    assert foreign.status_code in (403, 404)
    assert wrong_path.status_code == 404


@pytest.mark.parametrize("who", ["customer", "sub_a"])
async def test_external_roles_do_not_get_photos_at_all(
    api: AsyncClient, auth_world: AuthWorld, s3: httpx.AsyncClient, plan: Plan, who: str
) -> None:
    fact = await new_fact(api, auth_world, "foreman", plan)
    items = await uploaded(api, auth_world, s3, "foreman", fact)
    photo = (await confirm(api, auth_world, "foreman", fact, [items[0]["key"]])).json()[0]

    responses = [
        await presign(api, auth_world, who, fact, [("image/jpeg", 10)]),
        await confirm(api, auth_world, who, fact, [items[0]["key"]]),
        await send(api, "GET", photos(auth_world), who),
        await send(api, "GET", photos(auth_world, photo["id"], "download-url"), who),
        await send(api, "DELETE", photos(auth_world, photo["id"]), who),
    ]

    assert [response.status_code for response in responses] == [403] * 5


@pytest.mark.parametrize(
    ("who", "status", "expected"),
    [
        ("master", "draft", 200),
        ("foreman", "draft", 403),  # чужой черновик
        ("techdir", "draft", 403),
        ("master", "submitted", 403),  # отправленный факт заперт
        ("foreman", "submitted", 403),
    ],
)
async def test_who_may_add_photos_to_a_master_fact_in_each_status(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan, who: str, status: str, expected: int
) -> None:
    fact = await new_fact(api, auth_world, "master", plan, submit=status == "submitted")

    response = await presign(api, auth_world, who, fact, [("image/jpeg", 10)])

    assert response.status_code == expected, response.text


async def test_after_approval_photos_are_added_by_the_self_approver_or_the_technical_director(
    api: AsyncClient, auth_world: AuthWorld, plan: Plan
) -> None:
    own = await new_fact(api, auth_world, "foreman", plan)  # факт прораба согласован сразу
    master_fact = await new_fact(api, auth_world, "master", plan, submit=True)
    await send(api, "POST", facts(auth_world, master_fact, "approve"), "foreman")

    assert (await presign(api, auth_world, "foreman", own, [("image/jpeg", 10)])).status_code == 200
    assert (await presign(api, auth_world, "techdir", own, [("image/jpeg", 10)])).status_code == 200
    assert (
        await presign(api, auth_world, "foreman", master_fact, [("image/jpeg", 10)])
    ).status_code == 403
    assert (
        await presign(api, auth_world, "master", master_fact, [("image/jpeg", 10)])
    ).status_code == 403
    assert (
        await presign(api, auth_world, "techdir", master_fact, [("image/jpeg", 10)])
    ).status_code == 200


# --- идемпотентность, значения, удаление ----------------------------------------------------------


async def test_confirmation_is_idempotent_and_a_file_is_recorded_once(
    api: AsyncClient,
    auth_world: AuthWorld,
    s3: httpx.AsyncClient,
    plan: Plan,
    worker_engine: AsyncEngine,
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)
    items = await uploaded(api, auth_world, s3, "master", fact)
    key = uuid4()

    first = await confirm(api, auth_world, "master", fact, [items[0]["key"]], key=key)
    again = await confirm(api, auth_world, "master", fact, [items[0]["key"]], key=key)
    new_key = await confirm(api, auth_world, "master", fact, [items[0]["key"]])
    keyless = await send(
        api, "POST", photos(auth_world, "confirm"), "master",
        json={"work_actual_id": fact, "items": [{"key": items[1]["key"]}]},
    )  # fmt: skip

    assert first.status_code == 201 and again.status_code == 201
    assert again.json() == first.json() and again.headers["Idempotent-Replayed"] == "true"
    assert (new_key.status_code, new_key.json()["detail"]) == (409, "photo_already_confirmed")
    assert keyless.status_code == 422
    rows = await owner_rows(
        worker_engine,
        "SELECT count(*) AS n FROM work_photo WHERE work_actual_id = :f AND deleted_at IS NULL",
        f=UUID(fact),
    )
    assert rows[0].n == 1


@pytest.mark.parametrize(
    "extra",
    [{"latitude": "91", "longitude": "74"}, {"latitude": "42", "longitude": "181"}, {"unknown": 1}],
)
async def test_the_geolocation_of_a_photo_is_validated(
    api: AsyncClient,
    auth_world: AuthWorld,
    s3: httpx.AsyncClient,
    plan: Plan,
    extra: dict[str, Any],
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)
    items = await uploaded(api, auth_world, s3, "master", fact)

    response = await confirm(api, auth_world, "master", fact, [items[0]["key"]], **extra)

    assert response.status_code == 422, response.text


async def test_a_photo_is_deleted_softly_by_the_draft_author_and_later_by_the_technical_director(
    api: AsyncClient,
    auth_world: AuthWorld,
    s3: httpx.AsyncClient,
    plan: Plan,
    worker_engine: AsyncEngine,
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)
    items = await uploaded(api, auth_world, s3, "master", fact)
    first, second = (
        await confirm(api, auth_world, "master", fact, [i["key"] for i in items])
    ).json()

    assert (
        await send(api, "DELETE", photos(auth_world, first["id"]), "foreman")
    ).status_code == 403
    assert (await send(api, "DELETE", photos(auth_world, first["id"]), "master")).status_code == 204
    assert (await send(api, "DELETE", photos(auth_world, first["id"]), "master")).status_code == 404

    await send(api, "POST", facts(auth_world, fact, "submit"), "master")
    await send(api, "POST", facts(auth_world, fact, "approve"), "foreman")
    assert (
        await send(api, "DELETE", photos(auth_world, second["id"]), "master")
    ).status_code == 403
    assert (
        await send(api, "DELETE", photos(auth_world, second["id"]), "techdir")
    ).status_code == 204
    rows = await owner_rows(
        worker_engine,
        "SELECT count(*) FILTER (WHERE deleted_at IS NULL) AS live, count(*) AS total"
        " FROM work_photo WHERE work_actual_id = :f",
        f=UUID(fact),
    )
    assert (rows[0].live, rows[0].total) == (0, 2)  # записи остались: фото не удаляются физически


async def test_deleting_a_fact_takes_its_photos_along(
    api: AsyncClient, auth_world: AuthWorld, s3: httpx.AsyncClient, plan: Plan
) -> None:
    fact = await new_fact(api, auth_world, "master", plan)
    items = await uploaded(api, auth_world, s3, "master", fact)
    await confirm(api, auth_world, "master", fact, [items[0]["key"]])

    deleted = await send(api, "DELETE", facts(auth_world, fact), "master")

    assert deleted.status_code == 204
    listed = await send(api, "GET", photos(auth_world), "foreman", params={"work_actual_id": fact})
    assert listed.json() == []
