"""Материалы и склад (ADR-016 часть 5, S7a): справочник и цены, М-7, ВН, сторно, гарды БД и права.
Каждый тест - своё юрлицо и объект (F8); материалы и склады заводятся с уникальными кодами."""

import asyncio
from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import Response
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.modules.auth.principal import principal_store
from tests.auth_support import AuthWorld
from tests.db_support import INSUFFICIENT_PRIVILEGE, auth_rows, call, context
from tests.documents_support import (  # noqa: F401
    PDF,
    PDF_OK,
    Desk,
    approve,
    give_access,
    leave_no_access_behind,
    legal_entity,
    open_desk,
    project,
    upload,
)
from tests.equipment_support import headers_for, idem

API = "/api/v1"


@dataclass
class Yard:
    desk: Desk
    node: UUID
    warehouse: UUID
    material: UUID
    material_name: str
    base: str
    responsible: UUID


async def make_yard(
    api, auth_world: AuthWorld, engine: Engine, *, base: str = "кг", certificate: bool = False
) -> Yard:
    desk = await open_desk(
        api, auth_world, engine, "owner", "storekeeper", "procurement", "foreman", "master"
    )
    owner = desk.heads["owner"]
    node = await api.post(
        f"{API}/projects/{desk.project}/nodes",
        json={"type": "stage", "name": "Этап склада"},
        headers={**owner, **idem()},
    )
    assert node.status_code == 201, node.text
    warehouse = await api.post(
        f"{API}/warehouses",
        json={"project_id": str(desk.project), "name": f"Склад {uuid4().hex[:6]}"},
        headers=owner,
    )
    assert warehouse.status_code == 201, warehouse.text
    code = f"M-{uuid4().hex[:8].upper()}"
    name = f"Материал {code}"
    material = await api.post(
        f"{API}/materials",
        json={
            "legal_entity_id": str(desk.legal),
            "code": code,
            "name": name,
            "base_unit": base,
            "requires_certificate": certificate,
        },
        headers=owner,
    )
    assert material.status_code == 201, material.text
    return Yard(
        desk=desk,
        node=UUID(node.json()["id"]),
        warehouse=UUID(warehouse.json()["id"]),
        material=UUID(material.json()["id"]),
        material_name=name,
        base=base,
        responsible=auth_world.users["foreman"],
    )


def who_at(user: UUID, yard: Yard):
    """Контекст пользователя на объекте теста: объект и юрлицо, как у запроса API."""
    base = context(user, projects={yard.desk.project})
    return replace(base, legal_entity_ids=frozenset({yard.desk.legal}))


def receipt_line(yard: Yard, quantity: float, unit: str | None = None, batch: UUID | None = None):
    return {
        "name": yard.material_name,
        "unit": unit or yard.base,
        "quantity": quantity,
        "material_id": str(yard.material),
        "warehouse_id": str(yard.warehouse),
        "material_batch_id": str(batch) if batch else None,
    }


def issue_line(yard: Yard, quantity: float, *, responsible: bool = True) -> dict:
    return {
        "name": yard.material_name,
        "unit": yard.base,
        "quantity": quantity,
        "material_id": str(yard.material),
        "warehouse_id": str(yard.warehouse),
        "project_node_id": str(yard.node),
        "responsible_user_id": str(yard.responsible) if responsible else None,
    }


async def document(
    api, headers: dict[str, str], yard: Yard, kind: str, lines: list[dict]
) -> tuple[str, str]:
    body = {
        "kind": kind,
        "legal_entity_id": str(yard.desk.legal),
        "project_id": str(yard.desk.project),
        "title": f"{kind} {uuid4().hex[:6]}",
    }
    created = await api.post(f"{API}/documents", json=body, headers={**headers, **idem()})
    assert created.status_code == 201, created.text
    doc_id = created.json()["id"]
    if kind in ("waybill_internal", "inventory_act"):
        # Электронные виды (S7b): версия создана при создании документа, без файла.
        version_id = created.json()["versions"][0]["id"]
    else:
        # Версия М-7 появляется со сканом (загрузка), позиции редактируются в черновике версии.
        async with httpx.AsyncClient(timeout=10) as s3:
            scan = await upload(api, s3, doc_id, headers, PDF_OK, PDF)
        assert scan.status_code == 201, scan.text
        version_id = scan.json()["id"]
    saved = await api.put(
        f"{API}/documents/{doc_id}/versions/{version_id}/lines",
        json={"lines": lines},
        headers={**headers, **idem()},
    )
    assert saved.status_code == 200, saved.text
    return doc_id, version_id


async def certificate_document(api, yard: Yard) -> str:
    """Сертификат материала (S7b): вид material_certificate, юрлица, файл загружает кладовщик."""
    headers = yard.desk.heads["storekeeper"]
    created = await api.post(
        f"{API}/documents",
        json={
            "kind": "material_certificate",
            "legal_entity_id": str(yard.desk.legal),
            "title": f"Сертификат {uuid4().hex[:6]}",
        },
        headers={**headers, **idem()},
    )
    assert created.status_code == 201, created.text
    async with httpx.AsyncClient(timeout=10) as s3:
        scan = await upload(api, s3, created.json()["id"], headers, PDF_OK, PDF)
    assert scan.status_code == 201, scan.text
    return created.json()["id"]


async def receive(
    api, engine: Engine, yard: Yard, quantity: float, unit: str | None = None, batch=None
) -> str:
    """Приход по М-7: черновик, позиции и утверждение (маршрут обходит владелец, как сид)."""
    _, version = await document(
        api,
        yard.desk.heads["storekeeper"],
        yard,
        "m7_act",
        [receipt_line(yard, quantity, unit, batch)],
    )
    approve(engine, version)
    return version


async def issue(
    api, yard: Yard, quantity: float, *, responsible: bool = True
) -> tuple[str, str, Response]:
    """ВН: черновик с позицией расхода и проведение кладовщиком."""
    store = yard.desk.heads["storekeeper"]
    doc_id, version = await document(
        api, store, yard, "waybill_internal", [issue_line(yard, quantity, responsible=responsible)]
    )
    posted = await api.post(
        f"{API}/stock/waybills/{doc_id}/versions/{version}/post", headers={**store, **idem()}
    )
    return doc_id, version, posted


async def balance(api, yard: Yard) -> Decimal:
    rows = (
        await api.get(
            f"{API}/stock/balances",
            params={"warehouse_id": str(yard.warehouse)},
            headers=yard.desk.heads["storekeeper"],
        )
    ).json()
    return sum((Decimal(row["qty"]) for row in rows), Decimal(0))


async def movements(api, yard: Yard) -> list[dict]:
    response = await api.get(
        f"{API}/stock/movements",
        params={"warehouse_id": str(yard.warehouse), "limit": 500},
        headers=yard.desk.heads["storekeeper"],
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_receipt_converts_units_to_the_base_unit(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    unit = await api.post(
        f"{API}/materials/{yard.material}/units",
        json={"unit": "мешок", "factor": 25},
        headers=yard.desk.heads["owner"],
    )
    assert unit.status_code == 201, unit.text
    await receive(api, owner_sync_engine, yard, 2, unit="мешок")
    assert await balance(api, yard) == 50
    (receipt,) = [row for row in await movements(api, yard) if row["kind"] == "receipt"]
    assert Decimal(receipt["qty"]) == 50
    assert Decimal(receipt["quantity_in_unit"]) == 2
    assert Decimal(receipt["factor"]) == 25


async def test_a_draft_m7_adds_nothing_to_stock_until_approved(api, auth_world, worker_sync_engine):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    await document(api, yard.desk.heads["storekeeper"], yard, "m7_act", [receipt_line(yard, 5)])
    assert await balance(api, yard) == 0


async def test_a_certified_material_needs_a_batch_with_a_certificate(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine, certificate=True)
    owner = yard.desk.heads["owner"]
    certificate = await certificate_document(api, yard)
    _, version = await document(
        api, yard.desk.heads["storekeeper"], yard, "m7_act", [receipt_line(yard, 3)]
    )
    with pytest.raises(DBAPIError, match=r"material\.certificate_required"):
        approve(owner_sync_engine, version)
    batch = await api.post(
        f"{API}/materials/{yard.material}/batches",
        json={"batch_no": "П-1", "certificate_document_id": certificate},
        headers=owner,
    )
    assert batch.status_code == 201, batch.text
    await receive(api, owner_sync_engine, yard, 3, batch=UUID(batch.json()["id"]))
    assert await balance(api, yard) == 3


async def test_an_unknown_unit_is_refused_on_receipt(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    _, version = await document(
        api, yard.desk.heads["storekeeper"], yard, "m7_act", [receipt_line(yard, 1, unit="ведро")]
    )
    with pytest.raises(DBAPIError, match=r"material\.unit_unknown"):
        approve(owner_sync_engine, version)
    assert await balance(api, yard) == 0


@pytest.mark.parametrize(
    ("kind", "financial", "code"),
    [
        ("m7_act", False, "warehouse"),
        ("material_request", False, "procurement"),
        ("inventory_act", False, "inventory"),
        ("contract_supply", True, "money"),
        ("waybill_internal", False, "other"),
    ],
)
async def test_the_route_template_follows_the_kind_group(kind, financial, code):
    rows = await call(None, "SELECT app.route_template_code(:k, :f)", k=kind, f=financial)
    assert rows[0][0] == code


async def test_each_group_template_carries_its_steps(owner_engine):
    rows = await auth_rows(
        owner_engine,
        "SELECT t.code, s.position, s.role_code FROM approval_route_step_template s"
        " JOIN approval_route_template t ON t.id = s.template_id"
        " WHERE t.code IN ('warehouse', 'procurement', 'inventory')"
        " ORDER BY t.code, s.position",
    )
    assert [tuple(row) for row in rows] == [
        ("inventory", 1, "foreman"),
        ("inventory", 2, "technical_director"),
        ("procurement", 1, "technical_director"),
        ("procurement", 2, "finance_director"),
        ("warehouse", 1, "foreman"),
    ]


async def test_the_plan_price_is_seen_only_with_the_procurement_right(
    api, auth_world, worker_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    priced = await api.put(
        f"{API}/materials/{yard.material}/price",
        json={"unit_price": "150.5"},
        headers=yard.desk.heads["owner"],
    )
    assert priced.status_code == 200, priced.text
    assert priced.json()["plan_price"] == "150.50"
    for who in ("procurement", "owner"):
        body = (
            await api.get(f"{API}/materials/{yard.material}", headers=yard.desk.heads[who])
        ).json()
        assert body["plan_price"] == "150.50", who
    for who in ("foreman", "master", "storekeeper"):
        body = (
            await api.get(f"{API}/materials/{yard.material}", headers=yard.desk.heads[who])
        ).json()
        assert body["plan_price"] is None, who
    hidden = await call(
        who_at(auth_world.users["foreman"], yard),
        "SELECT unit_price FROM material_price WHERE material_id = :m",
        m=yard.material,
    )
    assert hidden == []
    visible = await call(
        who_at(auth_world.users["procurement"], yard),
        "SELECT unit_price FROM material_price WHERE material_id = :m",
        m=yard.material,
    )
    assert len(visible) == 1


async def test_the_supplier_portal_reads_neither_stock_nor_prices(api, auth_world):
    supplier = await headers_for(api, "supplier")
    listing = await api.get(f"{API}/materials", headers=supplier)
    assert listing.status_code == 403
    who = context(auth_world.users["supplier"], internal=False)
    for sql in ("SELECT 1 FROM stock_movement", "SELECT 1 FROM material_price"):
        with pytest.raises(DBAPIError) as info:
            await call(who, sql, portal=True)
        assert info.value.orig.sqlstate == INSUFFICIENT_PRIVILEGE


async def test_a_storekeeper_cannot_write_the_catalog(api, auth_world, worker_sync_engine):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    refused = await api.post(
        f"{API}/materials",
        json={
            "legal_entity_id": str(yard.desk.legal),
            "code": f"X-{uuid4().hex[:6]}",
            "name": "Чужой",
            "base_unit": "шт",
        },
        headers=yard.desk.heads["storekeeper"],
    )
    assert refused.status_code == 403


async def test_an_import_is_all_or_nothing(api, auth_world, worker_sync_engine):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    owner = yard.desk.heads["owner"]
    query = {"legal_entity_id": str(yard.desk.legal), "filename": "materials.csv"}
    broken = "артикул;наименование;базовая единица\nИМП-1;Цемент;мешок\nИМП-2;;кг\n"
    refused = await api.post(
        f"{API}/materials/import",
        params=query,
        content=broken.encode("utf-8"),
        headers={**owner, "Content-Type": "text/csv"},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "material.import_invalid"
    assert refused.json()["detail"]["issues"][0]["row"] == 3
    listed = (
        await api.get(
            f"{API}/materials", params={"legal_entity_id": str(yard.desk.legal)}, headers=owner
        )
    ).json()
    assert "ИМП-1" not in {row["code"] for row in listed}
    good = "артикул;наименование;базовая единица\nИМП-1;Цемент;мешок\nИМП-2;Песок;кг\n"
    created = await api.post(
        f"{API}/materials/import",
        params=query,
        content=good.encode("utf-8"),
        headers={**owner, "Content-Type": "text/csv"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["created"] == 2


async def test_a_waybill_takes_stock_and_refuses_a_shortage(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    await receive(api, owner_sync_engine, yard, 10)
    _, _, posted = await issue(api, yard, 4)
    assert posted.status_code == 200, posted.text
    assert await balance(api, yard) == 6
    _, _, short = await issue(api, yard, 7)
    assert short.status_code == 409, short.text
    assert "stock.insufficient" in short.text
    assert await balance(api, yard) == 6


async def test_twenty_parallel_issues_leave_exactly_ten(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    await receive(api, owner_sync_engine, yard, 10)
    results = await asyncio.gather(*(issue(api, yard, 1) for _ in range(20)))
    statuses = sorted(posted.status_code for _, _, posted in results)
    assert statuses.count(200) == 10, statuses
    assert statuses.count(409) == 10, statuses
    issued = [row for row in await movements(api, yard) if row["kind"] == "issue"]
    assert len(issued) == 10
    assert await balance(api, yard) == 0


async def test_a_posted_waybill_cannot_be_posted_again(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    await receive(api, owner_sync_engine, yard, 5)
    doc_id, version, posted = await issue(api, yard, 1)
    assert posted.status_code == 200, posted.text
    again = await api.post(
        f"{API}/stock/waybills/{doc_id}/versions/{version}/post",
        headers=yard.desk.heads["storekeeper"],
    )
    assert again.status_code == 409, again.text
    assert "stock.version_not_draft" in again.text
    assert await balance(api, yard) == 4


async def test_a_waybill_line_needs_a_responsible_person(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    await receive(api, owner_sync_engine, yard, 10)
    _, _, posted = await issue(api, yard, 1, responsible=False)
    assert posted.status_code == 422, posted.text
    assert "stock.issue_incomplete" in posted.text
    assert await balance(api, yard) == 10


async def test_a_reversal_restores_the_balance_once(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    store = yard.desk.heads["storekeeper"]
    await receive(api, owner_sync_engine, yard, 10)
    _, _, posted = await issue(api, yard, 3)
    assert posted.status_code == 200, posted.text
    issued = next(row for row in await movements(api, yard) if row["kind"] == "issue")
    undone = await api.post(f"{API}/stock/movements/{issued['id']}/reverse", headers=store)
    assert undone.status_code == 200, undone.text
    assert await balance(api, yard) == 10
    twice = await api.post(f"{API}/stock/movements/{issued['id']}/reverse", headers=store)
    assert twice.status_code == 409, twice.text
    assert "stock.already_reversed" in twice.text
    reversal = next(row for row in await movements(api, yard) if row["kind"] == "reversal")
    chain = await api.post(f"{API}/stock/movements/{reversal['id']}/reverse", headers=store)
    assert chain.status_code == 409, chain.text
    assert "stock.reversal_of_reversal" in chain.text
    assert await balance(api, yard) == 10


async def test_the_application_role_cannot_change_or_delete_movements(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    await receive(api, owner_sync_engine, yard, 5)
    who = who_at(auth_world.users["storekeeper"], yard)
    for sql in ("UPDATE stock_movement SET qty = 0", "DELETE FROM stock_movement"):
        with pytest.raises(DBAPIError) as info:
            await call(who, sql)
        assert info.value.orig.sqlstate == INSUFFICIENT_PRIVILEGE
    assert await balance(api, yard) == 5


async def test_a_warehouse_of_another_object_is_refused_by_the_database(
    api, auth_world, worker_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    other = project(worker_sync_engine, yard.desk.legal)
    who = who_at(auth_world.users["owner"], yard)
    with pytest.raises(DBAPIError) as info:
        await call(
            who,
            "INSERT INTO warehouse (id, project_id, legal_entity_id, kind, name)"
            " VALUES (gen_random_uuid(), :p, :le, 'object', :n)",
            p=other,
            le=yard.desk.legal,
            n="Чужой склад",
        )
    assert info.value.orig.sqlstate == INSUFFICIENT_PRIVILEGE


async def test_only_the_storekeeper_posts_stock_documents(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    await receive(api, owner_sync_engine, yard, 5)
    doc_id, version = await document(
        api, yard.desk.heads["storekeeper"], yard, "waybill_internal", [issue_line(yard, 1)]
    )
    denied = await api.post(
        f"{API}/stock/waybills/{doc_id}/versions/{version}/post",
        headers=yard.desk.heads["foreman"],
    )
    assert denied.status_code == 403, denied.text
    assert await balance(api, yard) == 5


async def test_a_waybill_cannot_take_stock_from_another_object(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    await receive(api, owner_sync_engine, yard, 10)
    other = project(worker_sync_engine, yard.desk.legal)
    give_access(worker_sync_engine, [auth_world.users["owner"]], (other,), yard.desk.legal)
    await principal_store.bump_scope()
    created = await api.post(
        f"{API}/warehouses",
        json={"project_id": str(other), "name": f"Чужой склад {uuid4().hex[:6]}"},
        headers=yard.desk.heads["owner"],
    )
    assert created.status_code == 201, created.text
    line = issue_line(yard, 1)
    line["warehouse_id"] = created.json()["id"]
    store = yard.desk.heads["storekeeper"]
    doc_id, version = await document(api, store, yard, "waybill_internal", [line])
    posted = await api.post(f"{API}/stock/waybills/{doc_id}/versions/{version}/post", headers=store)
    assert posted.status_code == 422, posted.text
    assert "stock.warehouse_project" in posted.text
    assert await balance(api, yard) == 10


async def test_a_reversal_is_refused_outside_the_object(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    await receive(api, owner_sync_engine, yard, 5)
    (receipt,) = [row for row in await movements(api, yard) if row["kind"] == "receipt"]
    other = project(worker_sync_engine, yard.desk.legal)
    outside = replace(
        context(auth_world.users["storekeeper"], projects={other}),
        legal_entity_ids=frozenset({yard.desk.legal}),
    )
    with pytest.raises(DBAPIError, match=r"stock\.not_found"):
        await call(outside, "SELECT app.stock_reverse(:m)", m=UUID(receipt["id"]))
    assert await balance(api, yard) == 5


def move(engine: Engine, version_id: str, target: str) -> None:
    """Перевод версии по маршруту без шагов согласования (как сид): владелец под kut_auth."""
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        connection.execute(
            text("SELECT app.version_move(CAST(:v AS uuid), :to, NULL, NULL)"),
            {"v": version_id, "to": target},
        )


async def test_a_stock_document_needs_an_object(api, auth_world, worker_sync_engine):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    refused = await api.post(
        f"{API}/documents",
        json={
            "kind": "m7_act",
            "legal_entity_id": str(yard.desk.legal),
            "title": "М-7 без объекта",
        },
        headers={**yard.desk.heads["storekeeper"], **idem()},
    )
    assert refused.status_code == 422, refused.text
    assert "document.scope_invalid" in refused.text


async def test_an_m7_is_routed_to_the_warehouse_group():
    rows = await call(None, "SELECT app.route_template_code('m7_act', false)")
    assert rows[0][0] == "warehouse"


async def test_an_m7_on_approval_adds_nothing_to_stock(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    _, version = await document(
        api, yard.desk.heads["storekeeper"], yard, "m7_act", [receipt_line(yard, 4)]
    )
    move(owner_sync_engine, version, "on_approval")
    assert await balance(api, yard) == 0
    move(owner_sync_engine, version, "approved")
    assert await balance(api, yard) == 4


async def test_a_reversal_of_a_consumed_receipt_is_refused(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    await receive(api, owner_sync_engine, yard, 10)
    _, _, posted = await issue(api, yard, 8)
    assert posted.status_code == 200, posted.text
    receipt = next(row for row in await movements(api, yard) if row["kind"] == "receipt")
    undone = await api.post(
        f"{API}/stock/movements/{receipt['id']}/reverse", headers=yard.desk.heads["storekeeper"]
    )
    assert undone.status_code == 409, undone.text
    assert "stock.insufficient" in undone.text
    assert await balance(api, yard) == 2


async def test_an_m7_line_without_material_is_refused_on_approval(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    _, version = await document(
        api,
        yard.desk.heads["storekeeper"],
        yard,
        "m7_act",
        [
            {
                "name": "Без материала",
                "unit": "кг",
                "quantity": 1,
                "warehouse_id": str(yard.warehouse),
            }
        ],
    )
    with pytest.raises(DBAPIError, match=r"stock\.material_required"):
        approve(owner_sync_engine, version)
    assert await balance(api, yard) == 0


async def test_a_waybill_is_created_without_a_file(api, auth_world, worker_sync_engine):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    created = await api.post(
        f"{API}/documents",
        json={
            "kind": "waybill_internal",
            "legal_entity_id": str(yard.desk.legal),
            "project_id": str(yard.desk.project),
            "title": "ВН без файла",
        },
        headers={**yard.desk.heads["storekeeper"], **idem()},
    )
    assert created.status_code == 201, created.text
    (version,) = created.json()["versions"]
    assert version["status"] == "draft"
    assert version["content_type"] is None
    assert version["size_bytes"] is None


async def test_an_m7_version_without_a_file_is_refused_by_the_database(
    api, auth_world, worker_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    created = await api.post(
        f"{API}/documents",
        json={
            "kind": "m7_act",
            "legal_entity_id": str(yard.desk.legal),
            "project_id": str(yard.desk.project),
            "title": "М-7 без скана",
        },
        headers={**yard.desk.heads["storekeeper"], **idem()},
    )
    assert created.status_code == 201, created.text
    with pytest.raises(DBAPIError, match=r"document\.file_required"):
        await call(
            who_at(auth_world.users["storekeeper"], yard),
            "INSERT INTO document_version (id, document_id, legal_entity_id, project_id,"
            " version_no, status) VALUES (gen_random_uuid(), :d, :le, :p, 1, 'draft')",
            d=UUID(created.json()["id"]),
            le=yard.desk.legal,
            p=yard.desk.project,
        )


async def test_a_certified_waybill_line_needs_a_batch(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine, certificate=True)
    store = yard.desk.heads["storekeeper"]
    batch = await api.post(
        f"{API}/materials/{yard.material}/batches",
        json={"batch_no": "С-1", "certificate_document_id": await certificate_document(api, yard)},
        headers=yard.desk.heads["owner"],
    )
    assert batch.status_code == 201, batch.text
    await receive(api, owner_sync_engine, yard, 10, batch=UUID(batch.json()["id"]))
    line = issue_line(yard, 2)
    doc, version = await document(api, store, yard, "waybill_internal", [line])
    posted = await api.post(f"{API}/stock/waybills/{doc}/versions/{version}/post", headers=store)
    assert posted.status_code == 422, posted.text
    assert "stock.batch_required" in posted.text
    line["material_batch_id"] = batch.json()["id"]
    doc, version = await document(api, store, yard, "waybill_internal", [line])
    posted = await api.post(f"{API}/stock/waybills/{doc}/versions/{version}/post", headers=store)
    assert posted.status_code == 200, posted.text
    assert await balance(api, yard) == 8


async def test_a_batch_is_refused_on_a_waybill_for_a_material_without_certificate(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    batch = await api.post(
        f"{API}/materials/{yard.material}/batches",
        json={"batch_no": "Б-1"},
        headers=yard.desk.heads["owner"],
    )
    assert batch.status_code == 201, batch.text
    await receive(api, owner_sync_engine, yard, 5)
    store = yard.desk.heads["storekeeper"]
    line = issue_line(yard, 1)
    line["material_batch_id"] = batch.json()["id"]
    doc, version = await document(api, store, yard, "waybill_internal", [line])
    posted = await api.post(f"{API}/stock/waybills/{doc}/versions/{version}/post", headers=store)
    assert posted.status_code == 422, posted.text
    assert "stock.batch_not_applicable" in posted.text
    assert await balance(api, yard) == 5


async def test_a_storekeeper_creates_a_batch_only_with_a_certificate_document(
    api, auth_world, worker_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine, certificate=True)
    store = yard.desk.heads["storekeeper"]
    missing = await api.post(
        f"{API}/materials/{yard.material}/batches",
        json={"batch_no": "С-2"},
        headers=store,
    )
    assert missing.status_code == 422, missing.text
    assert "material.certificate_required" in missing.text
    # Документ М-7 со сканом: единственная преграда - вид (файл есть, юрлицо то же).
    m7_doc, _ = await document(api, store, yard, "m7_act", [receipt_line(yard, 1)])
    wrong_kind = await api.post(
        f"{API}/materials/{yard.material}/batches",
        json={"batch_no": "С-3", "certificate_document_id": m7_doc},
        headers=store,
    )
    assert wrong_kind.status_code == 422, wrong_kind.text
    assert "material.certificate_invalid" in wrong_kind.text
    created = await api.post(
        f"{API}/materials/{yard.material}/batches",
        json={"batch_no": "С-4", "certificate_document_id": await certificate_document(api, yard)},
        headers=store,
    )
    assert created.status_code == 201, created.text


async def test_material_factor_is_hidden_for_a_foreign_legal_entity(
    api, auth_world, worker_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    owner = yard.desk.heads["owner"]
    other_entity, _ = legal_entity(worker_sync_engine)
    give_access(worker_sync_engine, [auth_world.users["owner"]], (), other_entity)
    await principal_store.bump_scope()
    foreign = await api.post(
        f"{API}/materials",
        json={
            "legal_entity_id": str(other_entity),
            "code": f"F-{uuid4().hex[:6]}",
            "name": "Чужой материал",
            "base_unit": "кг",
        },
        headers=owner,
    )
    assert foreign.status_code == 201, foreign.text
    unit = await api.post(
        f"{API}/materials/{foreign.json()['id']}/units",
        json={"unit": "мешок", "factor": 25},
        headers=owner,
    )
    assert unit.status_code == 201, unit.text
    own = await api.post(
        f"{API}/materials/{yard.material}/units",
        json={"unit": "мешок", "factor": 25},
        headers=owner,
    )
    assert own.status_code == 201, own.text
    who = who_at(auth_world.users["storekeeper"], yard)
    own_factor = await call(who, "SELECT app.material_factor(:m, 'мешок')", m=yard.material)
    assert own_factor[0][0] == 25
    hidden = await call(
        who, "SELECT app.material_factor(:m, 'мешок')", m=UUID(foreign.json()["id"])
    )
    assert hidden[0][0] is None


async def test_an_archived_receipt_partly_issued_says_receipt_consumed(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    store = yard.desk.heads["storekeeper"]
    doc, first = await document(api, store, yard, "m7_act", [receipt_line(yard, 10)])
    approve(owner_sync_engine, first)
    _, _, posted = await issue(api, yard, 8)
    assert posted.status_code == 200, posted.text
    assert await balance(api, yard) == 2
    async with httpx.AsyncClient(timeout=10) as s3:
        scan = await upload(api, s3, doc, store, PDF_OK, PDF)
    assert scan.status_code == 201, scan.text
    second = scan.json()["id"]
    saved = await api.put(
        f"{API}/documents/{doc}/versions/{second}/lines",
        json={"lines": [receipt_line(yard, 5)]},
        headers={**store, **idem()},
    )
    assert saved.status_code == 200, saved.text
    with pytest.raises(DBAPIError, match=r"stock\.receipt_consumed"):
        approve(owner_sync_engine, second)
    assert await balance(api, yard) == 2


async def test_a_new_version_with_the_same_receipt_keeps_the_balance(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    store = yard.desk.heads["storekeeper"]
    doc, first = await document(api, store, yard, "m7_act", [receipt_line(yard, 10)])
    approve(owner_sync_engine, first)
    _, _, posted = await issue(api, yard, 8)
    assert posted.status_code == 200, posted.text
    async with httpx.AsyncClient(timeout=10) as s3:
        scan = await upload(api, s3, doc, store, PDF_OK, PDF)
    second = scan.json()["id"]
    saved = await api.put(
        f"{API}/documents/{doc}/versions/{second}/lines",
        json={"lines": [receipt_line(yard, 10)]},
        headers={**store, **idem()},
    )
    assert saved.status_code == 200, saved.text
    approve(owner_sync_engine, second)
    assert await balance(api, yard) == 2


async def test_a_dry_run_import_writes_nothing(api, auth_world, worker_sync_engine):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    owner = yard.desk.heads["owner"]
    data = "артикул;наименование;базовая единица\nПРЕВ-1;Цемент;мешок\nПРЕВ-2;Песок;кг\n"
    preview = await api.post(
        f"{API}/materials/import",
        params={
            "legal_entity_id": str(yard.desk.legal),
            "filename": "materials.csv",
            "dry_run": "true",
        },
        content=data.encode("utf-8"),
        headers={**owner, "Content-Type": "text/csv"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["created"] == 2
    listed = await api.get(
        f"{API}/materials", params={"legal_entity_id": str(yard.desk.legal)}, headers=owner
    )
    assert not {row["code"] for row in listed.json()} & {"ПРЕВ-1", "ПРЕВ-2"}


async def test_a_batch_is_refused_on_a_receipt_for_a_material_without_certificate(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    batch = await api.post(
        f"{API}/materials/{yard.material}/batches",
        json={"batch_no": "Б-2"},
        headers=yard.desk.heads["owner"],
    )
    assert batch.status_code == 201, batch.text
    _, version = await document(
        api,
        yard.desk.heads["storekeeper"],
        yard,
        "m7_act",
        [receipt_line(yard, 5, batch=UUID(batch.json()["id"]))],
    )
    with pytest.raises(DBAPIError, match=r"stock\.batch_not_applicable"):
        approve(owner_sync_engine, version)
    assert await balance(api, yard) == 0


async def test_a_certificate_without_a_file_is_refused_for_a_batch(
    api, auth_world, worker_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine, certificate=True)
    store = yard.desk.heads["storekeeper"]
    created = await api.post(
        f"{API}/documents",
        json={
            "kind": "material_certificate",
            "legal_entity_id": str(yard.desk.legal),
            "title": "Сертификат без файла",
        },
        headers={**store, **idem()},
    )
    assert created.status_code == 201, created.text
    refused = await api.post(
        f"{API}/materials/{yard.material}/batches",
        json={"batch_no": "С-5", "certificate_document_id": created.json()["id"]},
        headers=yard.desk.heads["owner"],
    )
    assert refused.status_code == 422, refused.text
    assert "material.certificate_invalid" in refused.text


async def test_the_procurement_sees_the_legal_entities_of_the_catalog(
    api, auth_world, worker_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    listed = await api.get(
        f"{API}/materials/legal-entities", headers=yard.desk.heads["procurement"]
    )
    assert listed.status_code == 200, listed.text
    assert str(yard.desk.legal) in {row["id"] for row in listed.json()}
    foreman_view = await api.get(
        f"{API}/materials/legal-entities", headers=yard.desk.heads["foreman"]
    )
    assert foreman_view.status_code == 200, foreman_view.text


async def test_a_certificate_flag_cannot_change_once_the_material_has_stock(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine)
    await receive(api, owner_sync_engine, yard, 5)
    flipped = await api.patch(
        f"{API}/materials/{yard.material}",
        json={"requires_certificate": True},
        headers=yard.desk.heads["owner"],
    )
    assert flipped.status_code == 422, flipped.text
    assert "material.certificate_flag_locked" in flipped.text


async def test_a_repeated_batch_creation_returns_the_same_batch(
    api, auth_world, worker_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine, certificate=True)
    body = {"batch_no": "П-9", "certificate_document_id": await certificate_document(api, yard)}
    first = await api.post(
        f"{API}/materials/{yard.material}/batches", json=body, headers=yard.desk.heads["owner"]
    )
    second = await api.post(
        f"{API}/materials/{yard.material}/batches", json=body, headers=yard.desk.heads["owner"]
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"]


async def test_a_certificate_cannot_serve_another_material(api, auth_world, worker_sync_engine):
    yard = await make_yard(api, auth_world, worker_sync_engine, certificate=True)
    owner = yard.desk.heads["owner"]
    other = await api.post(
        f"{API}/materials",
        json={
            "legal_entity_id": str(yard.desk.legal),
            "code": f"M-{uuid4().hex[:6]}",
            "name": "Другой материал",
            "base_unit": "кг",
            "requires_certificate": True,
        },
        headers=owner,
    )
    assert other.status_code == 201, other.text
    certificate = await certificate_document(api, yard)
    first = await api.post(
        f"{API}/materials/{yard.material}/batches",
        json={"batch_no": "П-1", "certificate_document_id": certificate},
        headers=owner,
    )
    assert first.status_code == 201, first.text
    shared = await api.post(
        f"{API}/materials/{other.json()['id']}/batches",
        json={"batch_no": "П-2", "certificate_document_id": certificate},
        headers=owner,
    )
    assert shared.status_code == 422, shared.text
    assert "material.certificate_invalid" in shared.text


async def test_an_archived_receipt_checks_the_balance_of_its_batch(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine, certificate=True)
    owner = yard.desk.heads["owner"]
    store = yard.desk.heads["storekeeper"]
    batches = []
    for number in ("П-1", "П-2"):
        created = await api.post(
            f"{API}/materials/{yard.material}/batches",
            json={
                "batch_no": number,
                "certificate_document_id": await certificate_document(api, yard),
            },
            headers=owner,
        )
        assert created.status_code == 201, created.text
        batches.append(created.json()["id"])
    doc, first = await document(
        api, store, yard, "m7_act", [receipt_line(yard, 10, batch=UUID(batches[0]))]
    )
    approve(owner_sync_engine, first)
    line = issue_line(yard, 5)
    line["material_batch_id"] = batches[0]
    waybill, waybill_version = await document(api, store, yard, "waybill_internal", [line])
    posted = await api.post(
        f"{API}/stock/waybills/{waybill}/versions/{waybill_version}/post", headers=store
    )
    assert posted.status_code == 200, posted.text
    async with httpx.AsyncClient(timeout=10) as s3:
        scan = await upload(api, s3, doc, store, PDF_OK, PDF)
    assert scan.status_code == 201, scan.text
    second = scan.json()["id"]
    saved = await api.put(
        f"{API}/documents/{doc}/versions/{second}/lines",
        json={"lines": [receipt_line(yard, 10, batch=UUID(batches[1]))]},
        headers={**store, **idem()},
    )
    assert saved.status_code == 200, saved.text
    with pytest.raises(DBAPIError, match=r"stock\.receipt_consumed"):
        approve(owner_sync_engine, second)
    assert await balance(api, yard) == 5


async def test_a_batch_with_movements_keeps_its_number_and_certificate(
    api, auth_world, worker_sync_engine, owner_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine, certificate=True)
    created = await api.post(
        f"{API}/materials/{yard.material}/batches",
        json={"batch_no": "П-10", "certificate_document_id": await certificate_document(api, yard)},
        headers=yard.desk.heads["owner"],
    )
    assert created.status_code == 201, created.text
    batch = created.json()["id"]
    await receive(api, owner_sync_engine, yard, 5, batch=UUID(batch))
    who = who_at(auth_world.users["owner"], yard)
    with pytest.raises(DBAPIError, match=r"batch\.frozen"):
        await call(
            who, "UPDATE material_batch SET batch_no = 'П-11' WHERE id = :id", id=UUID(batch)
        )
    assert await balance(api, yard) == 5


async def test_a_customs_document_must_be_visible_in_the_legal_entity(
    api, auth_world, worker_sync_engine
):
    yard = await make_yard(api, auth_world, worker_sync_engine, certificate=True)
    response = await api.post(
        f"{API}/materials/{yard.material}/batches",
        json={
            "batch_no": "П-12",
            "certificate_document_id": await certificate_document(api, yard),
            "customs_document_id": str(uuid4()),
        },
        headers=yard.desk.heads["owner"],
    )
    assert response.status_code == 422, response.text
    assert "material.customs_invalid" in response.text
