"""Матрица видимости документов (ADR-015 часть 2 разделы 6-8, решения PO Q3 и Q4, DoD S5a).

Прораб без documents:read:financial не видит сумм и файлов финансовых видов; заказчик видит свои
утверждённые документы с суммами и не видит внутренних смет, чужих актов и черновиков; субподрядчик
и поставщик - только свои виды. Проверяется и через API, и через SQL под ролями БД."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.context import RequestContext, use_context
from app.core.db import get_portal_sessionmaker, get_sessionmaker
from app.modules.auth import devseed
from tests.auth_support import AuthWorld
from tests.documents_support import (  # noqa: F401
    PDF,
    PDF_OK,
    approve,
    contract,
    create_document,
    give_access,
    leave_no_access_behind,
    legal_entity,
    project,
    upload,
)
from tests.equipment_support import headers_for, in_context, rows

API = "/api/v1"


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


class Office:
    """Юрлицо, объект и пользователи одного теста: данные других тестов в выборки не попадают."""

    def __init__(
        self, api: AsyncClient, auth_world: AuthWorld, engine: Engine, owner_engine: Engine
    ) -> None:
        self.api = api
        self.world = auth_world
        self.engine = engine
        self.owner_engine = owner_engine
        self.legal, self.code = legal_entity(engine)
        self.project = project(engine, self.legal)
        self.heads: dict[str, dict[str, str]] = {}
        self.users: dict[str, UUID] = {}
        self.amounts: dict[str, str] = {}

    async def open(self, *who: str) -> "Office":
        for name in who:
            user = self.world.users.get(name)
            if user is None:
                raise KeyError(name)
            self.users[name] = user
            give_access(self.engine, [user], (self.project,), self.legal)
            self.heads[name] = await headers_for(self.api, name)
        return self

    def counterparty(self, name: str) -> UUID:
        return self.world.counterparties[name]

    async def create(self, who: str, body: dict[str, Any]) -> dict[str, Any]:
        body = dict(body)
        amount = body.pop("amount", None)  # сумма принадлежит версии (R1): уходит в подтверждение
        response = await create_document(
            self.api,
            self.heads[who],
            {
                "legal_entity_id": str(self.legal),
                "project_id": str(self.project),
                "title": "Тест",
                **body,
            },
        )
        assert response.status_code == 201, response.text
        document = response.json()
        if amount is not None:
            self.amounts[document["id"]] = amount
        return document

    async def get(self, who: str, document_id: str) -> httpx.Response:
        return await self.api.get(f"{API}/documents/{document_id}", headers=self.heads[who])

    async def listing(self, who: str) -> list[dict[str, Any]]:
        response = await self.api.get(
            f"{API}/documents", params={"project_id": str(self.project)}, headers=self.heads[who]
        )
        assert response.status_code == 200, response.text
        return response.json()

    async def publish(self, s3: httpx.AsyncClient, author: str, document_id: str) -> dict[str, Any]:
        response = await upload(
            self.api,
            s3,
            document_id,
            self.heads[author],
            PDF_OK,
            PDF,
            amount=self.amounts.get(document_id),
        )
        assert response.status_code == 201, response.text
        approve(self.owner_engine, response.json()["id"])
        return response.json()


async def test_the_foreman_sees_a_financial_document_without_its_sum_or_file(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    owner_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    office = await Office(api, auth_world, worker_sync_engine, owner_sync_engine).open(
        "estimator", "foreman"
    )
    ks2 = await office.create(
        "estimator",
        {
            "kind": "ks2",
            "counterparty_id": str(office.counterparty(devseed.CUSTOMER)),
            "amount": "98000.00",
        },
    )
    uploaded = await upload(
        api,
        s3,
        ks2["id"],
        office.heads["estimator"],
        PDF_OK,
        PDF,
        amount=office.amounts.get(ks2["id"]),
    )
    assert uploaded.status_code == 201, uploaded.text
    version = uploaded.json()["id"]

    seen = (await office.get("foreman", ks2["id"])).json()
    assert seen["registration_no"].startswith("КС2-")
    assert seen["amount"] is None
    assert [item["version_no"] for item in seen["versions"]] == [1]
    assert seen["versions"][0]["amount"] is None
    assert len(seen["versions"][0]["checksum_sha256"]) == 64  # внутренним - хэш версии
    file_link = await api.get(
        f"{API}/documents/{ks2['id']}/versions/{version}/download-url",
        headers=office.heads["foreman"],
    )
    assert file_link.status_code == 403
    amount = await api.put(
        f"{API}/documents/{ks2['id']}/versions/{version}/amount",
        json={"amount": "1"},
        headers=office.heads["foreman"],
    )
    assert amount.status_code == 403

    sums = await rows(
        "SELECT count(*) FROM document_version_amount WHERE version_id = CAST(:v AS uuid)",
        in_context(office.users["foreman"], (office.project,), (office.legal,)),
        {"v": version},
    )
    assert sums == [(0,)]


async def test_the_estimator_reads_the_sum_and_the_file_of_a_financial_document(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    owner_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    office = await Office(api, auth_world, worker_sync_engine, owner_sync_engine).open("estimator")
    ks2 = await office.create(
        "estimator",
        {
            "kind": "ks2",
            "counterparty_id": str(office.counterparty(devseed.CUSTOMER)),
            "amount": "98000.00",
        },
    )
    uploaded = await upload(
        api,
        s3,
        ks2["id"],
        office.heads["estimator"],
        PDF_OK,
        PDF,
        amount=office.amounts.get(ks2["id"]),
    )
    link = await api.get(
        f"{API}/documents/{ks2['id']}/versions/{uploaded.json()['id']}/download-url",
        headers=office.heads["estimator"],
    )
    assert link.status_code == 200, link.text
    assert link.json()["expires_in"] == 120
    assert (await office.get("estimator", ks2["id"])).json()["amount"] == "98000.00"


async def test_a_non_financial_file_is_open_to_the_foreman(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    owner_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    office = await Office(api, auth_world, worker_sync_engine, owner_sync_engine).open(
        "estimator", "foreman"
    )
    act = await office.create("estimator", {"kind": "commissioning_act"})
    uploaded = await upload(api, s3, act["id"], office.heads["estimator"], PDF_OK, PDF)
    link = await api.get(
        f"{API}/documents/{act['id']}/versions/{uploaded.json()['id']}/download-url",
        headers=office.heads["foreman"],
    )
    assert link.status_code == 200, link.text


async def test_the_customer_sees_his_approved_contract_with_its_sum_and_nothing_else(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    owner_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    office = await Office(api, auth_world, worker_sync_engine, owner_sync_engine).open(
        "estimator", "customer"
    )
    customer = office.counterparty(devseed.CUSTOMER)
    sub = office.counterparty(devseed.SUB_A)
    ours = await office.create(
        "estimator",
        {"kind": "contract_customer", "counterparty_id": str(customer), "amount": "5000000.00"},
    )
    published = await office.publish(s3, "estimator", ours["id"])
    newer = await upload(api, s3, ours["id"], office.heads["estimator"], PDF_OK, PDF)
    assert newer.status_code == 201, newer.text  # черновик второй версии утверждённого документа
    draft = await office.create(
        "estimator", {"kind": "contract_customer", "counterparty_id": str(customer)}
    )
    await upload(api, s3, draft["id"], office.heads["estimator"], PDF_OK, PDF)
    internal_estimate = await office.create("estimator", {"kind": "estimate_summary"})
    subcontract_act = await office.create(
        "estimator", {"kind": "ks2", "counterparty_id": str(sub), "amount": "1.00"}
    )
    await office.publish(s3, "estimator", subcontract_act["id"])

    listed = {item["id"] for item in await office.listing("customer")}
    assert listed == {ours["id"]}
    seen = (await office.get("customer", ours["id"])).json()
    assert seen["amount"] == "5000000.00"
    assert seen["registration_no"] is None and seen["legal_entity_id"] is None
    assert seen["legal_entity_name"] is None
    # Связь документа есть, но заказчику её не отдают: внутренний видит её, заказчик - нет.
    with worker_sync_engine.begin() as connection:
        node = connection.scalar(
            text(
                "INSERT INTO project_node (project_id, legal_entity_id, type, name)"
                " VALUES (:p, :le, 'building', :n) RETURNING id"
            ),
            {"p": office.project, "le": office.legal, "n": f"Узел связи {uuid4()}"},
        )
        connection.execute(
            text("INSERT INTO document_link (document_id, project_node_id) VALUES (:d, :n)"),
            {"d": ours["id"], "n": node},
        )
    internal_view = (await office.get("estimator", ours["id"])).json()
    assert [item["kind"] for item in internal_view["links"]] == ["project_node"]
    assert seen["links"] == []
    assert [item["id"] for item in seen["versions"]] == [published["id"]]
    assert seen["versions"][0]["amount"] == "5000000.00"
    assert seen["versions"][0]["checksum_sha256"] is None
    link = await api.get(
        f"{API}/documents/{ours['id']}/versions/{published['id']}/download-url",
        headers=office.heads["customer"],
    )
    assert link.status_code == 200, link.text
    hidden = await api.get(
        f"{API}/documents/{ours['id']}/versions/{newer.json()['id']}/download-url",
        headers=office.heads["customer"],
    )
    assert hidden.status_code == 404, hidden.text  # черновик версии - заказчику не виден
    assert (await office.get("customer", draft["id"])).status_code == 404
    assert (await office.get("customer", internal_estimate["id"])).status_code == 404
    assert (await office.get("customer", subcontract_act["id"])).status_code == 404


async def test_a_subcontractor_sees_only_its_own_approved_documents(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    owner_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    office = await Office(api, auth_world, worker_sync_engine, owner_sync_engine).open(
        "estimator", "sub_a"
    )
    own = await office.create(
        "estimator",
        {
            "kind": "ks2",
            "counterparty_id": str(office.counterparty(devseed.SUB_A)),
            "amount": "42000.00",
        },
    )
    await office.publish(s3, "estimator", own["id"])
    other = await office.create(
        "estimator",
        {
            "kind": "ks2",
            "counterparty_id": str(office.counterparty(devseed.SUB_B)),
            "amount": "7.00",
        },
    )
    await office.publish(s3, "estimator", other["id"])
    draft = await office.create(
        "estimator", {"kind": "ks2", "counterparty_id": str(office.counterparty(devseed.SUB_A))}
    )
    supply = await office.create(
        "estimator",
        {"kind": "waybill_torg12", "counterparty_id": str(office.counterparty(devseed.SUB_A))},
    )
    # Накладная утверждена и принадлежит субподрядчику: 404 даёт правило вида, а не нет версии.
    await office.publish(s3, "estimator", supply["id"])
    listed = {item["id"] for item in await office.listing("sub_a")}
    assert listed == {own["id"]}
    seen = (await office.get("sub_a", own["id"])).json()
    assert seen["amount"] == "42000.00"
    assert (await office.get("sub_a", other["id"])).status_code == 404
    assert (await office.get("sub_a", draft["id"])).status_code == 404
    assert (await office.get("sub_a", supply["id"])).status_code == 404


async def test_a_supplier_sees_its_own_waybill_and_no_contract_of_the_customer(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    owner_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    office = await Office(api, auth_world, worker_sync_engine, owner_sync_engine).open(
        "estimator", "supplier"
    )
    waybill = await office.create(
        "estimator",
        {
            "kind": "waybill_torg12",
            "counterparty_id": str(office.counterparty(devseed.SUPPLIER)),
            "amount": "1250.00",
        },
    )
    await office.publish(s3, "estimator", waybill["id"])
    contract_customer = await office.create(
        "estimator",
        {
            "kind": "contract_customer",
            "counterparty_id": str(office.counterparty(devseed.CUSTOMER)),
        },
    )
    await office.publish(s3, "estimator", contract_customer["id"])
    assert {item["id"] for item in await office.listing("supplier")} == {waybill["id"]}
    assert (await office.get("supplier", contract_customer["id"])).status_code == 404


async def test_archiving_removes_the_document_from_outside_views_and_deletes_nothing(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    owner_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    office = await Office(api, auth_world, worker_sync_engine, owner_sync_engine).open(
        "estimator", "owner", "customer", "foreman"
    )
    contract_doc = await office.create(
        "estimator",
        {
            "kind": "contract_customer",
            "counterparty_id": str(office.counterparty(devseed.CUSTOMER)),
        },
    )
    await office.publish(s3, "estimator", contract_doc["id"])
    assert (await office.get("customer", contract_doc["id"])).status_code == 200
    refused = await api.post(
        f"{API}/documents/{contract_doc['id']}/archive", headers=office.heads["foreman"]
    )
    assert refused.status_code == 403
    archived = await api.post(
        f"{API}/documents/{contract_doc['id']}/archive", headers=office.heads["owner"]
    )
    assert archived.status_code == 204, archived.text
    assert (await office.get("customer", contract_doc["id"])).status_code == 404
    kept = (await office.get("owner", contract_doc["id"])).json()
    assert kept["archived_at"] is not None and kept["current_version"] is not None


async def test_a_user_cannot_create_a_document_in_an_object_outside_his_access(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, owner_sync_engine: Engine
) -> None:
    office = await Office(api, auth_world, worker_sync_engine, owner_sync_engine).open("estimator")
    foreign = project(worker_sync_engine, office.legal)
    response = await create_document(
        api,
        office.heads["estimator"],
        {
            "kind": "estimate_summary",
            "legal_entity_id": str(office.legal),
            "project_id": str(foreign),
            "title": "Чужой объект",
        },
    )
    assert response.status_code == 403, response.text


async def test_the_audit_journal_shows_the_sum_only_to_the_documents_financial_right(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    owner_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    office = await Office(api, auth_world, worker_sync_engine, owner_sync_engine).open(
        "estimator", "foreman"
    )
    ks3 = await office.create(
        "estimator",
        {
            "kind": "ks3",
            "counterparty_id": str(office.counterparty(devseed.CUSTOMER)),
            "amount": "3.00",
        },
    )
    # Черновик с суммой и её правка: в журнале две строки - вставка и UPDATE (журнал пишет только
    # изменённые колонки, поэтому право проверяет строка по row_id, а не по JSON).
    uploaded = await upload(
        api, s3, ks3["id"], office.heads["estimator"], PDF_OK, PDF, amount="3.00"
    )
    assert uploaded.status_code == 201, uploaded.text
    edited = await api.put(
        f"{API}/documents/{ks3['id']}/versions/{uploaded.json()['id']}/amount",
        json={"amount": "4.00"},
        headers=office.heads["estimator"],
    )
    assert edited.status_code == 204, edited.text
    counted = (
        "SELECT count(*) FROM audit_log WHERE table_name = 'document_version_amount'"
        " AND action = :action"
    )
    assert await rows(
        counted,
        in_context(office.users["foreman"], (office.project,), (office.legal,)),
        {"action": "update"},
    ) == [(0,)]
    estimator = in_context(office.users["estimator"], (office.project,), (office.legal,))
    assert await rows(counted, estimator, {"action": "insert"}) == [(1,)]
    assert await rows(counted, estimator, {"action": "update"}) == [(1,)]


async def test_the_portal_login_cannot_read_the_storage_key_the_comment_or_the_number(
    worker_sync_engine: Engine, auth_world: AuthWorld
) -> None:
    context = RequestContext(
        user_id=auth_world.users["customer"],
        is_internal=False,
        project_ids=frozenset(),
        legal_entity_ids=frozenset(),
        counterparty_id=auth_world.counterparties[devseed.CUSTOMER],
        request_id=None,
        sees_all_users=False,
    )
    for column in ("s3_key", "comment", "checksum_sha256", "created_by"):
        with use_context(context):
            async with get_portal_sessionmaker()() as session:
                with pytest.raises(DBAPIError, match="permission denied"):
                    await session.execute(text(f"SELECT {column} FROM document_version"))  # noqa: S608
    with use_context(context):
        async with get_portal_sessionmaker()() as session:
            with pytest.raises(DBAPIError, match="permission denied"):
                await session.execute(text("SELECT registration_no FROM document"))


_ = (AsyncIterator, AsyncEngine, get_sessionmaker, contract, PDF)


async def test_an_archived_document_takes_no_new_version(
    api: AsyncClient,
    auth_world: AuthWorld,
    worker_sync_engine: Engine,
    owner_sync_engine: Engine,
    s3: httpx.AsyncClient,
) -> None:
    office = await Office(api, auth_world, worker_sync_engine, owner_sync_engine).open(
        "estimator", "owner"
    )
    document = await office.create("estimator", {"kind": "commissioning_act"})
    archived = await api.post(
        f"{API}/documents/{document['id']}/archive", headers=office.heads["owner"]
    )
    assert archived.status_code == 204, archived.text
    refused = await api.post(
        f"{API}/documents/{document['id']}/versions/presign",
        json={"content_type": PDF, "size_bytes": 10, "checksum_sha256": "A" * 43 + "="},
        headers=office.heads["estimator"],
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "document.archived"
