"""Инвентаризация (ADR-016 часть 5, S7c): акт пересчёта, маршрут прораб -> техдиректор,
снимок остатка и корректировки движений.
Каждый тест - свой объект и склад (как в test_materials_stock)."""

from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import Response
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.modules.auth.principal import principal_store
from tests.auth_support import AuthWorld
from tests.db_support import INSUFFICIENT_PRIVILEGE, call, expect_error
from tests.documents_support import (  # noqa: F401
    PDF,
    PDF_OK,
    approve,
    give_access,
    leave_no_access_behind,
    project,
    sha256_b64,
    upload,
)
from tests.equipment_support import headers_for, idem
from tests.test_materials_stock import (
    API,
    Yard,
    certificate_document,
    document,
    issue,
    make_yard,
    receipt_line,
    receive,
    who_at,
)

LINE_FIELDS = (
    "position_no",
    "name",
    "unit",
    "quantity",
    "material_id",
    "material_batch_id",
    "warehouse_id",
)
ADJUSTMENTS = (
    "SELECT material_id, batch_id, qty FROM stock_movement"
    " WHERE version_id = CAST(:v AS uuid) AND kind = 'adjustment' ORDER BY qty"
)


async def inventory_yard(
    api, auth_world: AuthWorld, engine: Engine, *, certificate: bool = False
) -> Yard:
    yard = await make_yard(api, auth_world, engine, certificate=certificate)
    give_access(engine, [auth_world.users["techdir"]], (yard.desk.project,), yard.desk.legal)
    yard.desk.heads["technical_director"] = await headers_for(api, "techdir")
    return yard


async def add_material(api, yard: Yard, name: str) -> UUID:
    created = await api.post(
        f"{API}/materials",
        json={
            "legal_entity_id": str(yard.desk.legal),
            "code": f"M-{uuid4().hex[:8].upper()}",
            "name": name,
            "base_unit": yard.base,
            "requires_certificate": False,
        },
        headers=yard.desk.heads["owner"],
    )
    assert created.status_code == 201, created.text
    return UUID(created.json()["id"])


async def receive_material(
    api, engine: Engine, yard: Yard, material: UUID, name: str, quantity: float
) -> None:
    line = {
        "name": name,
        "unit": yard.base,
        "quantity": quantity,
        "material_id": str(material),
        "warehouse_id": str(yard.warehouse),
        "material_batch_id": None,
    }
    _, version = await document(api, yard.desk.heads["storekeeper"], yard, "m7_act", [line])
    approve(engine, version)


async def inventory_act(api, yard: Yard) -> tuple[str, str]:
    """Акт пересчёта: склад выбран, позиции предзаполнены по учётным остаткам, факт пуст."""
    store = yard.desk.heads["storekeeper"]
    doc, version = await document(api, store, yard, "inventory_act", [])
    filled = await api.post(
        f"{API}/documents/{doc}/versions/{version}/inventory/prefill",
        json={"warehouse_id": str(yard.warehouse)},
        headers={**store, **idem()},
    )
    assert filled.status_code == 200, filled.text
    return doc, version


async def lines_of(api, yard: Yard, doc: str, version: str, who: str = "storekeeper") -> list[dict]:
    response = await api.get(
        f"{API}/documents/{doc}/versions/{version}/lines", headers=yard.desk.heads[who]
    )
    assert response.status_code == 200, response.text
    return response.json()["lines"]


async def count(api, yard: Yard, doc: str, version: str, facts: dict[UUID, str]) -> None:
    """Ввод фактов по позициям; ключ - партия, если она есть, иначе материал. Остальные - пустые."""
    store = yard.desk.heads["storekeeper"]
    lines = await lines_of(api, yard, doc, version)
    for line in lines:
        key = UUID(line["material_batch_id"] or line["material_id"])
        if key in facts:
            line["quantity"] = facts[key]
    saved = await api.put(
        f"{API}/documents/{doc}/versions/{version}/lines",
        json={"lines": [{field: line[field] for field in LINE_FIELDS} for line in lines]},
        headers={**store, **idem()},
    )
    assert saved.status_code == 200, saved.text


async def send(api, yard: Yard, doc: str, version: str) -> Response:
    return await api.post(
        f"{API}/documents/{doc}/versions/{version}/send",
        headers={**yard.desk.heads["storekeeper"], **idem()},
    )


async def decide(
    api,
    headers: dict[str, str],
    doc: str,
    version: str,
    position: int,
    decision: str = "approve",
    reason: str | None = None,
) -> Response:
    return await api.post(
        f"{API}/documents/{doc}/versions/{version}/decisions",
        json={"position": position, "decision": decision, "reason": reason},
        headers={**headers, **idem()},
    )


async def approve_route(api, yard: Yard, doc: str, version: str) -> None:
    first = await decide(api, yard.desk.heads["foreman"], doc, version, 1)
    assert first.status_code == 200, first.text
    last = await decide(api, yard.desk.heads["technical_director"], doc, version, 2)
    assert last.status_code == 200, last.text


async def stock(who, yard: Yard, material: UUID, batch: UUID | None = None) -> Decimal:
    rows = await call(
        who,
        "SELECT app.stock_balance(CAST(:w AS uuid), CAST(:m AS uuid), CAST(:b AS uuid)) AS qty",
        w=yard.warehouse,
        m=material,
        b=batch,
    )
    return Decimal(rows[0].qty)


async def test_the_database_adjusts_only_the_discrepancies(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    gravel = await add_material(api, yard, "Щебень")
    cement = await add_material(api, yard, "Цемент")
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    await receive_material(api, owner_sync_engine, yard, gravel, "Щебень", 5)
    await receive_material(api, owner_sync_engine, yard, cement, "Цемент", 4)
    store = who_at(auth_world.users["storekeeper"], yard)
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "7", gravel: "5", cement: "6"})
    assert (await send(api, yard, doc, version)).status_code == 200
    await approve_route(api, yard, doc, version)
    assert await stock(store, yard, yard.material) == 7
    assert await stock(store, yard, gravel) == 5
    assert await stock(store, yard, cement) == 6
    adjustments = await call(store, ADJUSTMENTS, v=version)
    assert [(row.material_id, row.qty) for row in adjustments] == [
        (yard.material, Decimal("-3")),
        (cement, Decimal("2")),
    ]


async def test_the_route_goes_to_the_foreman_and_then_the_technical_director(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    store = who_at(auth_world.users["storekeeper"], yard)
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "7"})
    assert (await send(api, yard, doc, version)).status_code == 200
    route = (
        await api.get(
            f"{API}/documents/{doc}/versions/{version}/route", headers=yard.desk.heads["foreman"]
        )
    ).json()
    assert [(step["position"], step["role_code"]) for step in route["steps"]] == [
        (1, "foreman"),
        (2, "technical_director"),
    ]
    assert route["current_position"] == 1
    early = await decide(api, yard.desk.heads["technical_director"], doc, version, 1)
    assert early.status_code >= 400, early.text
    first = await decide(api, yard.desk.heads["foreman"], doc, version, 1)
    assert first.json()["current_position"] == 2
    assert await stock(store, yard, yard.material) == 10
    last = await decide(api, yard.desk.heads["technical_director"], doc, version, 2)
    assert last.json()["version_status"] == "approved"
    assert await stock(store, yard, yard.material) == 7


async def test_a_draft_and_an_act_on_approval_do_not_change_the_stock(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    store = who_at(auth_world.users["storekeeper"], yard)
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "7"})
    assert await stock(store, yard, yard.material) == 10
    assert (await send(api, yard, doc, version)).status_code == 200
    assert await stock(store, yard, yard.material) == 10
    assert await call(store, ADJUSTMENTS, v=version) == []


async def test_the_snapshot_is_taken_at_the_send_and_later_receipts_do_not_distort_the_discrepancy(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    store = who_at(auth_world.users["storekeeper"], yard)
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "10"})
    assert (await send(api, yard, doc, version)).status_code == 200
    assert Decimal((await lines_of(api, yard, doc, version))[0]["snapshot_qty"]) == 10
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 5)
    line = (await lines_of(api, yard, doc, version))[0]
    assert Decimal(line["snapshot_qty"]) == 10
    assert Decimal(line["balance_now"]) == 15
    assert Decimal(line["discrepancy"]) == 0
    await approve_route(api, yard, doc, version)
    assert await stock(store, yard, yard.material) == 15
    assert await call(store, ADJUSTMENTS, v=version) == []


async def test_a_repeated_adjustment_adds_no_second_adjustment(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    store = who_at(auth_world.users["storekeeper"], yard)
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "7"})
    assert (await send(api, yard, doc, version)).status_code == 200
    await approve_route(api, yard, doc, version)
    assert len(await call(store, ADJUSTMENTS, v=version)) == 1
    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        connection.execute(text("SELECT app.inventory_adjust(CAST(:v AS uuid))"), {"v": version})
    assert len(await call(store, ADJUSTMENTS, v=version)) == 1
    assert await stock(store, yard, yard.material) == 7


async def test_an_adjustment_and_a_snapshot_cannot_be_written_outside_the_functions(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    store = who_at(auth_world.users["storekeeper"], yard)
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "7"})
    assert (await send(api, yard, doc, version)).status_code == 200
    line_id = (
        await call(
            store,
            "SELECT id FROM document_line"
            " WHERE version_id = CAST(:v AS uuid) AND deleted_at IS NULL",
            v=version,
        )
    )[0].id
    await expect_error(
        store,
        "INSERT INTO stock_movement (legal_entity_id, project_id, warehouse_id, material_id, kind,"
        " qty, unit, quantity_in_unit, factor) VALUES (CAST(:le AS uuid), CAST(:p AS uuid),"
        " CAST(:w AS uuid), CAST(:m AS uuid), 'adjustment', -1, 'кг', 1, 1)",
        INSUFFICIENT_PRIVILEGE,
        le=yard.desk.legal,
        p=yard.desk.project,
        w=yard.warehouse,
        m=yard.material,
    )
    await expect_error(
        store,
        "UPDATE document_line SET snapshot_qty = 99 WHERE id = CAST(:l AS uuid)",
        INSUFFICIENT_PRIVILEGE,
        l=line_id,
    )


async def test_an_adjustment_cannot_take_the_stock_negative(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    store = who_at(auth_world.users["storekeeper"], yard)
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "0"})
    assert (await send(api, yard, doc, version)).status_code == 200
    _, _, posted = await issue(api, yard, 8)
    assert posted.status_code == 200, posted.text
    assert await stock(store, yard, yard.material) == 2
    first = await decide(api, yard.desk.heads["foreman"], doc, version, 1)
    assert first.status_code == 200, first.text
    last = await decide(api, yard.desk.heads["technical_director"], doc, version, 2)
    assert last.status_code == 409, last.text
    assert "inventory.negative" in last.text
    assert await stock(store, yard, yard.material) == 2
    assert await call(store, ADJUSTMENTS, v=version) == []


async def test_a_line_without_a_fact_blocks_the_send(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    gravel = await add_material(api, yard, "Щебень")
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    await receive_material(api, owner_sync_engine, yard, gravel, "Щебень", 5)
    store = who_at(auth_world.users["storekeeper"], yard)
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "7"})
    sent = await send(api, yard, doc, version)
    assert sent.status_code == 422, sent.text
    assert "inventory.not_counted" in sent.text
    status = await call(
        store, "SELECT status FROM document_version WHERE id = CAST(:v AS uuid)", v=version
    )
    assert status[0].status == "draft"


async def test_the_plan_price_and_the_value_are_seen_only_with_the_procurement_right(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    priced = await api.put(
        f"{API}/materials/{yard.material}/price",
        json={"unit_price": "150.5"},
        headers=yard.desk.heads["owner"],
    )
    assert priced.status_code == 200, priced.text
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "7"})
    assert (await send(api, yard, doc, version)).status_code == 200
    await approve_route(api, yard, doc, version)
    for who in ("owner", "procurement"):
        line = (await lines_of(api, yard, doc, version, who))[0]
        assert Decimal(line["plan_price"]) == Decimal("150.5"), who
        assert Decimal(line["value"]) == Decimal("-451.5"), who
    for who in ("storekeeper", "foreman"):
        line = (await lines_of(api, yard, doc, version, who))[0]
        assert line["plan_price"] is None and line["value"] is None, who


async def test_a_certified_material_is_counted_by_batch(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine, certificate=True)
    owner = yard.desk.heads["owner"]
    batches = []
    for batch_no in ("П-1", "П-2"):
        created = await api.post(
            f"{API}/materials/{yard.material}/batches",
            json={
                "batch_no": batch_no,
                "certificate_document_id": await certificate_document(api, yard),
            },
            headers=owner,
        )
        assert created.status_code == 201, created.text
        batches.append(UUID(created.json()["id"]))
    first, second = batches
    await receive(api, owner_sync_engine, yard, 4, batch=first)
    await receive(api, owner_sync_engine, yard, 6, batch=second)
    store = who_at(auth_world.users["storekeeper"], yard)
    doc, version = await inventory_act(api, yard)
    assert len(await lines_of(api, yard, doc, version)) == 2
    await count(api, yard, doc, version, {first: "3", second: "6"})
    assert (await send(api, yard, doc, version)).status_code == 200
    await approve_route(api, yard, doc, version)
    adjustments = await call(store, ADJUSTMENTS, v=version)
    assert [(row.batch_id, row.qty) for row in adjustments] == [(first, Decimal("-1"))]
    assert await stock(store, yard, yard.material, first) == 3
    assert await stock(store, yard, yard.material, second) == 6


async def test_an_edited_template_does_not_change_the_running_route(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "7"})
    assert (await send(api, yard, doc, version)).status_code == 200
    try:
        edited = await api.put(
            f"{API}/documents/route-templates/inventory",
            json={"roles": ["technical_director"]},
            headers=yard.desk.heads["owner"],
        )
        assert edited.status_code == 200, edited.text
        route = (
            await api.get(
                f"{API}/documents/{doc}/versions/{version}/route",
                headers=yard.desk.heads["foreman"],
            )
        ).json()
        assert [step["role_code"] for step in route["steps"]] == ["foreman", "technical_director"]
    finally:
        # Шаблон общий для всех тестов: возвращаем прежние шаги (прораб, техдиректор).
        restored = await api.put(
            f"{API}/documents/route-templates/inventory",
            json={"roles": ["foreman", "technical_director"]},
            headers=yard.desk.heads["owner"],
        )
        assert restored.status_code == 200, restored.text


async def test_a_rejected_act_returns_to_draft_without_touching_the_stock(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    store = who_at(auth_world.users["storekeeper"], yard)
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "7"})
    assert (await send(api, yard, doc, version)).status_code == 200
    rejected = await decide(
        api, yard.desk.heads["foreman"], doc, version, 1, "reject", reason="пересчитать полку"
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["version_status"] == "draft"
    assert await stock(store, yard, yard.material) == 10
    assert await call(store, ADJUSTMENTS, v=version) == []


async def test_a_correction_is_reversed_only_by_the_act(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    store = who_at(auth_world.users["storekeeper"], yard)
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "7"})
    assert (await send(api, yard, doc, version)).status_code == 200
    await approve_route(api, yard, doc, version)
    movement = (
        await call(
            store,
            "SELECT id FROM stock_movement"
            " WHERE version_id = CAST(:v AS uuid) AND kind = 'adjustment'",
            v=version,
        )
    )[0].id
    reversed_by_hand = await api.post(
        f"{API}/stock/movements/{movement}/reverse",
        headers={**yard.desk.heads["storekeeper"], **idem()},
    )
    assert reversed_by_hand.status_code == 422, reversed_by_hand.text
    assert "stock.adjustment_reverse" in reversed_by_hand.text
    assert await stock(store, yard, yard.material) == 7


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


async def signed_copy(api, s3: httpx.AsyncClient, yard: Yard, doc: str, version: str) -> Response:
    """Подписанный скан к согласованной версии: загрузка в S3 и версия `signed` от техдиректора."""
    headers = yard.desk.heads["technical_director"]
    presigned = await api.post(
        f"{API}/documents/{doc}/versions/presign",
        json={
            "content_type": PDF,
            "size_bytes": len(PDF_OK),
            "checksum_sha256": sha256_b64(PDF_OK),
        },
        headers=headers,
    )
    assert presigned.status_code == 200, presigned.text
    item = presigned.json()
    stored = await s3.put(item["upload_url"], content=PDF_OK, headers=item["headers"])
    assert stored.status_code == 200, stored.text
    return await api.post(
        f"{API}/documents/{doc}/versions/{version}/signed",
        json={"key": item["key"], "comment": "подписанный скан"},
        headers={**headers, **idem()},
    )


async def test_a_warehouse_of_another_object_is_refused_on_the_lines(
    api, auth_world, worker_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    # Второй объект того же юрлица. Склад заводим под владельцем уже залогиненного объекта:
    # второй вход владельца в том же окне TOTP не проходит.
    other_project = project(worker_sync_engine, yard.desk.legal)
    give_access(worker_sync_engine, [auth_world.users["owner"]], (other_project,), yard.desk.legal)
    await principal_store.bump_scope()
    created = await api.post(
        f"{API}/warehouses",
        json={"project_id": str(other_project), "name": f"Чужой {uuid4().hex[:6]}"},
        headers=yard.desk.heads["owner"],
    )
    assert created.status_code == 201, created.text
    foreign_warehouse = created.json()["id"]
    doc, version = await document(api, yard.desk.heads["storekeeper"], yard, "inventory_act", [])
    refused = await api.put(
        f"{API}/documents/{doc}/versions/{version}/lines",
        json={
            "lines": [
                {
                    "name": yard.material_name,
                    "unit": yard.base,
                    "quantity": "1",
                    "material_id": str(yard.material),
                    "warehouse_id": foreign_warehouse,
                }
            ]
        },
        headers={**yard.desk.heads["storekeeper"], **idem()},
    )
    assert refused.status_code == 422, refused.text
    assert "stock.warehouse_project" in refused.text


async def test_only_the_storekeeper_creates_an_inventory_act(api, auth_world, worker_sync_engine):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    for who in ("foreman", "technical_director"):
        created = await api.post(
            f"{API}/documents",
            json={
                "kind": "inventory_act",
                "legal_entity_id": str(yard.desk.legal),
                "project_id": str(yard.desk.project),
                "title": f"Пересчёт {who}",
            },
            headers={**yard.desk.heads[who], **idem()},
        )
        assert created.status_code == 403, f"{who}: {created.text}"


async def test_a_unit_other_than_the_base_is_refused_on_send(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "7"})
    lines = await lines_of(api, yard, doc, version)
    lines[0]["unit"] = "т"
    saved = await api.put(
        f"{API}/documents/{doc}/versions/{version}/lines",
        json={"lines": [{field: line[field] for field in LINE_FIELDS} for line in lines]},
        headers={**yard.desk.heads["storekeeper"], **idem()},
    )
    assert saved.status_code == 200, saved.text
    sent = await send(api, yard, doc, version)
    assert sent.status_code == 422, sent.text
    assert "inventory.unit_mismatch" in sent.text


async def test_a_signed_copy_keeps_the_stock_of_its_approved_receipt(
    api, auth_world, worker_sync_engine, owner_sync_engine, s3: httpx.AsyncClient
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    store = who_at(auth_world.users["storekeeper"], yard)
    doc, version = await document(
        api, yard.desk.heads["storekeeper"], yard, "m7_act", [receipt_line(yard, 10)]
    )
    approve(owner_sync_engine, version)
    assert await stock(store, yard, yard.material) == 10
    signed = await signed_copy(api, s3, yard, doc, version)
    assert signed.status_code == 201, signed.text
    assert await stock(store, yard, yard.material) == 10
    reversals = await call(
        store,
        "SELECT count(*) AS n FROM stock_movement"
        " WHERE version_id = CAST(:v AS uuid) AND kind = 'reversal'",
        v=version,
    )
    assert reversals[0].n == 0


async def test_a_batch_of_a_material_without_certificate_is_refused_on_send(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    batch = await api.post(
        f"{API}/materials/{yard.material}/batches",
        json={
            "batch_no": "Б-1",
            "certificate_document_id": None,
            "customs_document_id": None,
            "country_of_origin": None,
        },
        headers=yard.desk.heads["owner"],
    )
    assert batch.status_code == 201, batch.text
    doc, version = await inventory_act(api, yard)
    lines = await lines_of(api, yard, doc, version)
    lines[0]["quantity"] = "7"
    lines[0]["material_batch_id"] = batch.json()["id"]
    saved = await api.put(
        f"{API}/documents/{doc}/versions/{version}/lines",
        json={"lines": [{field: line[field] for field in LINE_FIELDS} for line in lines]},
        headers={**yard.desk.heads["storekeeper"], **idem()},
    )
    assert saved.status_code == 200, saved.text
    sent = await send(api, yard, doc, version)
    assert sent.status_code == 422, sent.text
    assert "stock.batch_not_applicable" in sent.text


async def test_a_new_version_of_an_inventory_act_is_refused_once_it_is_approved(
    api, auth_world, worker_sync_engine, owner_sync_engine, s3: httpx.AsyncClient
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    doc, version = await inventory_act(api, yard)
    await count(api, yard, doc, version, {yard.material: "7"})
    assert (await send(api, yard, doc, version)).status_code == 200
    await approve_route(api, yard, doc, version)
    scanned = await upload(api, s3, doc, yard.desk.heads["storekeeper"], PDF_OK, PDF)
    assert scanned.status_code == 409, scanned.text
    assert "inventory.version_locked" in scanned.text
    assert await stock(who_at(auth_world.users["storekeeper"], yard), yard, yard.material) == 7


async def test_a_second_act_of_the_same_warehouse_waits_for_the_first_one(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    store = who_at(auth_world.users["storekeeper"], yard)
    first_doc, first = await inventory_act(api, yard)
    await count(api, yard, first_doc, first, {yard.material: "7"})
    assert (await send(api, yard, first_doc, first)).status_code == 200
    second_doc, second = await inventory_act(api, yard)
    await count(api, yard, second_doc, second, {yard.material: "5"})
    refused = await send(api, yard, second_doc, second)
    assert refused.status_code == 409, refused.text
    assert "inventory.in_progress" in refused.text
    await approve_route(api, yard, first_doc, first)
    assert await stock(store, yard, yard.material) == 7
    assert (await send(api, yard, second_doc, second)).status_code == 200
    await approve_route(api, yard, second_doc, second)
    assert await stock(store, yard, yard.material) == 5


async def test_a_version_started_before_the_approval_cannot_be_sent_after_it(
    api, auth_world, worker_sync_engine, owner_sync_engine, s3: httpx.AsyncClient
):
    yard = await inventory_yard(api, auth_world, worker_sync_engine)
    await receive_material(api, owner_sync_engine, yard, yard.material, yard.material_name, 10)
    store = who_at(auth_world.users["storekeeper"], yard)
    doc, first = await inventory_act(api, yard)
    await count(api, yard, doc, first, {yard.material: "7"})
    later = await upload(api, s3, doc, yard.desk.heads["storekeeper"], PDF_OK, PDF)
    assert later.status_code == 201, later.text
    assert (await send(api, yard, doc, first)).status_code == 200
    await approve_route(api, yard, doc, first)
    assert await stock(store, yard, yard.material) == 7
    refused = await send(api, yard, doc, str(later.json()["id"]))
    assert refused.status_code == 409, refused.text
    assert "inventory.version_locked" in refused.text
    assert await stock(store, yard, yard.material) == 7
