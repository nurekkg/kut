"""Импорт реестра техники: проверка по строкам, всё или ничего, повтор (ADR-014 часть 3 §9)."""

import importlib.util
import io
import time
import uuid
import zipfile
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.equipment.devseed import TAX_IDS
from tests.auth_support import AuthWorld
from tests.equipment_support import API, headers_for, idem

HEADER = "ИНН юрлица;Код типа;Владение;Номер;Номер договора аренды"
TAX = TAX_IDS["east"]


def good_row(prefix: str, index: int) -> list[str]:
    return [TAX, "SAM", "own", f"{prefix}{index:05d}", ""]


def body(rows: list[list[str]]) -> bytes:
    lines = [HEADER] + [";".join(row) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8-sig")


async def check(
    api: AsyncClient, headers: dict[str, str], data: bytes, name: str = "units.csv"
) -> Any:
    return await api.post(
        f"{API}/import/check",
        params={"filename": name},
        content=data,
        headers={**headers, "Content-Type": "text/csv"},
    )


async def commit(
    api: AsyncClient, headers: dict[str, str], data: bytes, name: str = "units.csv"
) -> Any:
    return await api.post(
        f"{API}/import",
        params={"filename": name},
        content=data,
        headers={**headers, "Content-Type": "text/csv", **idem()},
    )


async def units_named(
    api: AsyncClient, headers: dict[str, str], query: str
) -> list[dict[str, Any]]:
    return (
        await api.get(f"{API}/units", headers=headers, params={"q": query, "limit": 500})
    ).json()


async def test_bad_rows_are_reported_by_line_and_nothing_is_loaded(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    prefix = f"ERR{uuid.uuid4().hex[:6].upper()}"
    rows = [good_row(prefix, index) for index in range(300)]
    rows[2][1] = "XXX"  # строка 4 файла: нет такого типа
    rows[15][2] = "lease"  # строка 17: владение не распознано
    rows[228] = [TAX, "SAM", "rented", f"{prefix}RENT", ""]  # строка 230: арендованная без договора
    data = body(rows)
    checked = (await check(api, head, data)).json()
    assert checked["status"] == "rejected"
    assert [issue["row"] for issue in checked["errors"]] == [4, 17, 230]
    assert {issue["code"] for issue in checked["errors"]} == {
        "type_unknown",
        "ownership_invalid",
        "contract_required",
    }
    refused = await commit(api, head, data)
    assert refused.status_code == 422
    assert refused.json()["detail"]["status"] == "rejected"
    assert [issue["row"] for issue in refused.json()["detail"]["errors"]] == [4, 17, 230]
    assert await units_named(api, head, prefix) == []


async def test_a_fixed_file_loads_and_a_repeat_changes_nothing(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    prefix = f"OK{uuid.uuid4().hex[:6].upper()}"
    data = body([good_row(prefix, index) for index in range(40)])
    loaded = await commit(api, head, data)
    assert loaded.status_code == 201, loaded.text
    assert (loaded.json()["created"], loaded.json()["updated"]) == (40, 0)
    again = await commit(api, head, data)
    assert again.status_code == 201
    assert (again.json()["created"], again.json()["updated"], again.json()["unchanged"]) == (
        0,
        0,
        40,
    )
    assert len(await units_named(api, head, prefix)) == 40


async def test_a_changed_number_updates_one_unit_and_a_duplicate_key_is_a_row_error(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    prefix = f"CH{uuid.uuid4().hex[:6].upper()}"
    rows = [good_row(prefix, index) for index in range(5)]
    await commit(api, head, body(rows))
    rows[0][3] = rows[0][3].lower().replace("ch", "ch-")  # тот же ключ, другой написанный номер
    changed = await commit(api, head, body(rows))
    assert changed.status_code == 201, changed.text
    assert (changed.json()["updated"], changed.json()["unchanged"]) == (1, 4)
    duplicate = [good_row(prefix, 1), [TAX, "SAM", "own", f"{prefix.lower()} 00001", ""]]
    twin = (await check(api, head, body(duplicate))).json()
    assert [issue["code"] for issue in twin["errors"]] == ["duplicate_key"]
    assert twin["errors"][0]["row"] == 3


async def test_a_file_over_the_row_limit_is_refused_before_any_write(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    data = body([good_row("BIG", index) for index in range(5001)])
    checked = (await check(api, head, data)).json()
    assert checked["status"] == "rejected"
    assert checked["errors"][0]["code"] == "too_many_rows"


async def test_five_thousand_rows_are_checked_and_loaded_within_the_recorded_time(
    api: AsyncClient, auth_world: AuthWorld, worker_engine: AsyncEngine
) -> None:
    head = await headers_for(api, "equipment_head")
    prefix = f"BULK{uuid.uuid4().hex[:6].upper()}"
    data = body([good_row(prefix, index) for index in range(5000)])
    started = time.perf_counter()
    checked = await check(api, head, data)
    check_seconds = time.perf_counter() - started
    assert checked.status_code == 200 and checked.json()["status"] == "checked"
    assert checked.json()["created"] == 5000
    started = time.perf_counter()
    loaded = await commit(api, head, data)
    commit_seconds = time.perf_counter() - started
    assert loaded.status_code == 201, loaded.text[:300]
    print(f"\nimport 5000 rows: check {check_seconds:.2f}s, commit {commit_seconds:.2f}s")
    async with worker_engine.begin() as connection:
        await connection.execute(
            text("UPDATE equipment_unit SET deleted_at = now() WHERE registry_key LIKE :pattern"),
            {"pattern": f"{prefix}%"},
        )


async def test_a_workbook_with_macros_is_refused(api: AsyncClient, auth_world: AuthWorld) -> None:
    head = await headers_for(api, "equipment_head")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/vbaProject.bin", b"macro")
    response = await api.post(
        f"{API}/import/check",
        params={"filename": "units.xlsx"},
        content=buffer.getvalue(),
        headers=head,
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "equipment.import_macros_forbidden"}


async def test_the_xlsx_path_needs_openpyxl_and_says_so_when_it_is_missing(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    response = await api.post(
        f"{API}/import/check",
        params={"filename": "units.xlsx"},
        content=buffer.getvalue(),
        headers=head,
    )
    if importlib.util.find_spec("openpyxl") is None:
        assert response.status_code == 501
        assert response.json() == {"detail": "equipment.xlsx_unavailable"}
    else:  # pragma: no cover - путь зависит от установленного пакета
        assert response.status_code in (200, 422)


async def test_templates_and_the_journal_are_available_to_the_registry_writers(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    head = await headers_for(api, "equipment_head")
    template = await api.get(f"{API}/import/template", params={"format": "csv"}, headers=head)
    assert template.status_code == 200
    assert "Код типа" in template.text and "Номер договора аренды" in template.text
    journal = (await api.get(f"{API}/import/batches", headers=head)).json()
    assert journal and {item["status"] for item in journal} <= {"checked", "committed", "rejected"}
    foreman = await headers_for(api, "foreman")
    assert (await api.get(f"{API}/import/batches", headers=foreman)).status_code == 403
