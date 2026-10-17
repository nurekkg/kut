"""Документы S5a: справочник видов, создание и номера, загрузка и проверка файла (ADR-015 часть 1
раздел 3, часть 2 раздел 8, DoD S5a). Каждый тест - своё юрлицо и объект (F8)."""

import asyncio
import datetime
import hashlib
import zlib
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.engine import Engine

from app.modules.auth import devseed
from tests.auth_support import AuthWorld
from tests.documents_support import (  # noqa: F401
    DOCX,
    JPEG_OK,
    PDF,
    PDF_JAVASCRIPT,
    PDF_OK,
    PNG_OK,
    XLSM,
    XLSX,
    create_document,
    docx_ok,
    give_access,
    leave_no_access_behind,
    legal_entity,
    project,
    sha256_b64,
    upload,
    xlsx_ok,
    zip_bytes,
)
from tests.equipment_support import headers_for, new_unit

API = "/api/v1"


@pytest.fixture
async def s3() -> AsyncIterator[httpx.AsyncClient]:
    """Клиент «браузера»: ходит по подписанным ссылкам напрямую в S3, мимо API."""
    async with httpx.AsyncClient(timeout=10) as client:
        yield client


async def _setup(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, who: tuple[str, ...]
) -> tuple[UUID, str, UUID, dict[str, dict[str, str]]]:
    """Юрлицо с кодом, объект и доступ перечисленным ролям на время теста."""
    legal, code = legal_entity(worker_sync_engine)
    users = [auth_world.users[name] for name in who]
    identifier = project(worker_sync_engine, legal)
    give_access(worker_sync_engine, users, (identifier,), legal)
    headers = {name: await headers_for(api, name) for name in who}
    return legal, code, identifier, headers


async def _draft(api: AsyncClient, headers: dict[str, str], legal: UUID, identifier: UUID) -> str:
    response = await create_document(
        api,
        headers,
        {
            "kind": "estimate_summary",
            "legal_entity_id": str(legal),
            "project_id": str(identifier),
            "title": "Сводная смета",
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def test_the_kinds_catalog_lists_every_kind_with_its_scope(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    response = await api.get(f"{API}/documents/kinds", headers=await headers_for(api, "foreman"))
    assert response.status_code == 200, response.text
    kinds = {item["code"]: item for item in response.json()}
    assert len(kinds) == 24
    assert kinds["ks2"]["prefix"] == "КС2"
    assert kinds["ks2"]["scope"] == "object" and kinds["ks2"]["financial"] is True
    assert kinds["hidden_works_act"]["financial"] is False
    assert kinds["waybill_m15"]["scope"] == "either"
    assert kinds["equipment_passport"]["links"] == ["equipment_unit"]


async def test_a_draft_is_numbered_with_the_legal_entity_code_and_the_year(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    legal, code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    response = await create_document(
        api,
        heads["estimator"],
        {
            "kind": "ks2",
            "legal_entity_id": str(legal),
            "project_id": str(identifier),
            "counterparty_id": str(auth_world.counterparties[devseed.CUSTOMER]),
            "title": "КС-2 за октябрь",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["registration_no"] == f"КС2-{code}-{datetime.date.today().year}-0001"
    assert body["financial"] is True
    assert body["amount"] is None
    assert body["versions"] == []


async def test_a_creation_that_breaks_a_scope_or_link_rule_is_refused_with_its_code(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator", "equipment_head")
    )
    customer = str(auth_world.counterparties[devseed.CUSTOMER])
    base = {"legal_entity_id": str(legal), "title": "Тест"}
    cases = [
        ("estimator", {"kind": "ks2", "counterparty_id": customer}, "document.scope_invalid"),
        (
            "estimator",
            {"kind": "ks2", "project_id": str(identifier)},
            "document.counterparty_required",
        ),
        ("estimator", {"kind": "addendum"}, "document.contract_required"),
        ("estimator", {"kind": "ird", "project_id": str(identifier)}, "document.link_required"),
        ("equipment_head", {"kind": "equipment_passport"}, "document.link_required"),
        ("estimator", {"kind": "unknown_kind"}, "document.kind_invalid"),
    ]
    for who, extra, code in cases:
        response = await create_document(api, heads[who], {**base, **extra})
        assert response.status_code == 422, (extra, response.text)
        assert response.json()["detail"] == code, extra
    # Виды техники создаёт право техники (R2): сметчику документ паспорта не открыть.
    refused = await create_document(api, heads["estimator"], {**base, "kind": "equipment_passport"})
    assert refused.status_code == 403, refused.text


async def test_a_legal_entity_without_a_code_cannot_issue_numbers(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    with worker_sync_engine.begin() as connection:
        bare = connection.exec_driver_sql(
            "INSERT INTO legal_entity (id, name) VALUES (gen_random_uuid(), 'ЮЛ без кода')"
            " RETURNING id"
        ).scalar_one()
    identifier = project(worker_sync_engine, bare)
    estimator = auth_world.users["estimator"]
    give_access(worker_sync_engine, [estimator], (identifier,), bare)
    response = await create_document(
        api,
        await headers_for(api, "estimator"),
        {
            "kind": "estimate_summary",
            "legal_entity_id": str(bare),
            "project_id": str(identifier),
            "title": "Смета",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "document.legal_entity_code_missing"


async def test_numbers_are_contiguous_under_parallel_creation(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    legal, code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    bodies = [
        {
            "kind": "estimate_local",
            "legal_entity_id": str(legal),
            "project_id": str(identifier),
            "title": f"Смета {index}",
        }
        for index in range(20)
    ]
    responses = await asyncio.gather(
        *(create_document(api, heads["estimator"], body) for body in bodies)
    )
    assert [response.status_code for response in responses] == [201] * 20
    numbers = sorted(int(r.json()["registration_no"].rsplit("-", 1)[1]) for r in responses)
    assert numbers == list(range(1, 21))
    assert all(r.json()["registration_no"].startswith(f"СМЛ-{code}-") for r in responses)


async def test_a_creation_rolled_back_by_the_database_leaves_no_gap_in_the_numbers(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    headers = heads["estimator"]
    first = await create_document(
        api,
        headers,
        {
            "kind": "estimate_summary",
            "legal_entity_id": str(legal),
            "project_id": str(identifier),
            "title": "Первая",
        },
    )
    assert first.status_code == 201, first.text
    refused = await create_document(
        api,
        headers,
        {
            "kind": "hidden_works_act",
            "legal_entity_id": str(legal),
            "project_id": str(identifier),
            "title": "Узел, которого нет",
            "links": [
                {"kind": "project_node", "id": "00000000-0000-4000-8000-000000000001"},
                {"kind": "work", "id": "00000000-0000-4000-8000-000000000002"},
            ],
        },
    )
    assert refused.status_code == 422, refused.text
    second = await create_document(
        api,
        headers,
        {
            "kind": "estimate_summary",
            "legal_entity_id": str(legal),
            "project_id": str(identifier),
            "title": "Вторая",
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["registration_no"].endswith("-0002")


async def test_the_same_idempotency_key_returns_the_same_document(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    headers = {**heads["estimator"], "Idempotency-Key": str(uuid4())}
    body = {
        "kind": "estimate_summary",
        "legal_entity_id": str(legal),
        "project_id": str(identifier),
        "title": "Смета",
    }
    first = await api.post(f"{API}/documents", json=body, headers=headers)
    second = await api.post(f"{API}/documents", json=body, headers=headers)
    assert first.status_code == 201, first.text
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]


async def test_a_pdf_is_uploaded_confirmed_and_becomes_draft_version_one(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    document_id = await _draft(api, heads["estimator"], legal, identifier)
    response = await upload(api, s3, document_id, heads["estimator"], PDF_OK, PDF, comment="первая")
    assert response.status_code == 201, response.text
    version = response.json()
    assert version["version_no"] == 1
    assert version["status"] == "draft"
    assert version["size_bytes"] == len(PDF_OK)
    assert version["checksum_sha256"] == hashlib.sha256(PDF_OK).hexdigest()


async def test_the_same_object_key_is_refused_twice(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    headers = heads["estimator"]
    document_id = await _draft(api, headers, legal, identifier)
    presigned = await api.post(
        f"{API}/documents/{document_id}/versions/presign",
        json={
            "content_type": PDF,
            "size_bytes": len(PDF_OK),
            "checksum_sha256": sha256_b64(PDF_OK),
        },
        headers=headers,
    )
    item = presigned.json()
    first = await s3.put(item["upload_url"], content=PDF_OK, headers=item["headers"])
    assert first.status_code == 200, first.text
    again = await s3.put(item["upload_url"], content=PDF_OK, headers=item["headers"])
    assert again.status_code == 412


async def test_wrong_signature_macros_and_active_pdf_are_refused_with_their_codes(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    headers = heads["estimator"]
    document_id = await _draft(api, headers, legal, identifier)
    not_a_pdf = await upload(api, s3, document_id, headers, b"hello, not a pdf at all", PDF)
    assert not_a_pdf.status_code == 422
    assert not_a_pdf.json()["detail"] == "document.type_mismatch"
    macro_book = zip_bytes(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
            "xl/vbaProject.bin": b"macro",
        }
    )
    with_macros = await upload(api, s3, document_id, headers, macro_book, XLSX)
    assert with_macros.status_code == 422
    assert with_macros.json()["detail"] == "document.macros_forbidden"
    active = await upload(api, s3, document_id, headers, PDF_JAVASCRIPT, PDF)
    assert active.status_code == 422
    assert active.json()["detail"] == "document.active_content"
    macro_type = await api.post(
        f"{API}/documents/{document_id}/versions/presign",
        json={"content_type": XLSM, "size_bytes": 10, "checksum_sha256": sha256_b64(b"0" * 10)},
        headers=headers,
    )
    assert macro_type.status_code == 422
    assert macro_type.json()["detail"] == "document.type_not_allowed"


async def test_a_file_over_thirty_megabytes_is_refused_before_upload(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    headers = heads["estimator"]
    document_id = await _draft(api, headers, legal, identifier)
    too_big = await api.post(
        f"{API}/documents/{document_id}/versions/presign",
        json={
            "content_type": PDF,
            "size_bytes": 30 * 1024 * 1024 + 1,
            "checksum_sha256": sha256_b64(b"x"),
        },
        headers=headers,
    )
    assert too_big.status_code == 422
    assert "upload_url" not in too_big.text


async def test_office_and_image_files_with_the_right_structure_pass(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    headers = heads["estimator"]
    document_id = await _draft(api, headers, legal, identifier)
    for content, content_type in (
        (docx_ok(), DOCX),
        (xlsx_ok(), XLSX),
        (PNG_OK, "image/png"),
        (JPEG_OK, "image/jpeg"),
    ):
        response = await upload(api, s3, document_id, headers, content, content_type)
        assert response.status_code == 201, (content_type, response.text)
    document = (await api.get(f"{API}/documents/{document_id}", headers=headers)).json()
    assert [version["version_no"] for version in document["versions"]] == [1, 2, 3, 4]


async def test_an_unknown_document_is_not_found(api: AsyncClient, auth_world: AuthWorld) -> None:
    response = await api.get(
        f"{API}/documents/00000000-0000-4000-8000-000000000999",
        headers=await headers_for(api, "estimator"),
    )
    assert response.status_code == 404


async def test_a_user_without_any_document_right_is_refused_at_the_registry(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    response = await api.get(f"{API}/documents", headers=await headers_for(api, "machinist"))
    assert response.status_code == 403


async def test_an_equipment_passport_is_linked_to_its_unit_and_keeps_its_kind(
    api: AsyncClient, auth_world: AuthWorld
) -> None:
    unit = await new_unit(api, await headers_for(api, "equipment_head"), auth_world)
    response = await create_document(
        api,
        await headers_for(api, "techdir"),
        {
            "kind": "equipment_passport",
            "legal_entity_id": str(auth_world.legal_entities[devseed.LE_EAST]),
            "title": "Паспорт техники",
            "links": [{"kind": "equipment_unit", "id": unit["id"]}],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["registration_no"].startswith("ПСП-")
    assert response.json()["links"] == [{"kind": "equipment_unit", "id": unit["id"]}]


async def test_a_pdf_with_an_escaped_script_name_is_refused(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    """Имя /JavaScript записано с #xx: проверка видит раскодированное имя."""
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    document_id = await _draft(api, heads["estimator"], legal, identifier)
    escaped = b"%PDF-1.4\n1 0 obj << /OpenAction << /S /J#61vaScript >> >> endobj\n%%EOF\n"
    refused = await upload(api, s3, document_id, heads["estimator"], escaped, PDF)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == "document.active_content"


async def test_a_script_inside_a_compressed_object_stream_is_refused(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    """Словарь скрипта сжат FlateDecode, как объектный поток: проверка смотрит распакованное."""
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    document_id = await _draft(api, heads["estimator"], legal, identifier)
    payload = zlib.compress(b"<< /Type /ObjStm /S /JavaScript /JS (app.alert(1)) >>")
    header = b"%PDF-1.5\n1 0 obj << /Type /ObjStm /Filter /FlateDecode /Length "
    pdf = header + str(len(payload)).encode() + b" >>\nstream\n" + payload
    pdf += b"\nendstream\nendobj\n%%EOF\n"
    refused = await upload(api, s3, document_id, heads["estimator"], pdf, PDF)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == "document.active_content"


async def test_an_ordinary_compressed_pdf_passes(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    document_id = await _draft(api, heads["estimator"], legal, identifier)
    payload = zlib.compress(b"BT /F1 12 Tf 72 712 Td (Akt No 1) Tj ET")
    header = b"%PDF-1.5\n1 0 obj << /Filter /FlateDecode /Length "
    pdf = header + str(len(payload)).encode() + b" >>\nstream\n" + payload
    pdf += b"\nendstream\nendobj\n%%EOF\n"
    accepted = await upload(api, s3, document_id, heads["estimator"], pdf, PDF)
    assert accepted.status_code == 201, accepted.text


async def test_the_office_macro_check_ignores_the_case_of_the_part_names(
    api: AsyncClient, auth_world: AuthWorld, worker_sync_engine: Engine, s3: httpx.AsyncClient
) -> None:
    legal, _code, identifier, heads = await _setup(
        api, auth_world, worker_sync_engine, ("estimator",)
    )
    document_id = await _draft(api, heads["estimator"], legal, identifier)
    upper = zip_bytes(
        {
            "[Content_Types].xml": b"<Types/>",
            "XL/WORKBOOK.XML": b"<workbook/>",
            "XL/VBAPROJECT.BIN": b"macro",
        }
    )
    refused = await upload(api, s3, document_id, heads["estimator"], upper, XLSX)
    assert refused.status_code == 422
    assert refused.json()["detail"] == "document.macros_forbidden"
    declared_type = (
        b'<Types><Override ContentType="application/vnd.ms-excel.sheet.macroenabled.main+xml"/>'
        b"</Types>"
    )
    declared = zip_bytes({"[Content_Types].xml": declared_type, "xl/workbook.xml": b"<workbook/>"})
    refused_type = await upload(api, s3, document_id, heads["estimator"], declared, XLSX)
    assert refused_type.status_code == 422
    assert refused_type.json()["detail"] == "document.macros_forbidden"
