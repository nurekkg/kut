"""Помощники тестов документов (ADR-015 часть 2). Каждый тест заводит свои юрлицо и объект с кодом:
данные других тестов не попадают в выборки (урок se2-hardening, решение F8)."""

import base64
import hashlib
import io
import zipfile
from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.context import system_context
from app.core.db import get_sessionmaker
from tests.auth_support import AuthWorld
from tests.equipment_support import grant as _grant
from tests.equipment_support import idem, second_project

API = "/api/v1"

PDF_OK = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
PDF_JAVASCRIPT = b"%PDF-1.4\n1 0 obj << /Type /Catalog /OpenAction << /S /JavaScript >> >> endobj\n"
PDF_LAUNCH = b"%PDF-1.4\n1 0 obj << /S /Launch /F (cmd.exe) >> endobj\n%%EOF\n"
PNG_OK = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
JPEG_OK = b"\xff\xd8\xff\xe0" + b"\x00" * 24

PDF = "application/pdf"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSM = "application/vnd.ms-excel.sheet.macroEnabled.12"

# Доступы, выданные тестом: отзываются после него, чтобы не менять выборки других тестов
# (например, видимость пользователей по объектам). Пользователи сида - общие.
GRANTS: list[tuple[str, UUID, UUID]] = []


def grant(
    engine: Engine, user: UUID, *, projects: tuple[UUID, ...] = (), entities: tuple[UUID, ...] = ()
) -> None:
    _grant(engine, user, projects=projects, entities=entities)
    GRANTS.extend(("project", user, item) for item in projects)
    GRANTS.extend(("entity", user, item) for item in entities)


def revoke_test_grants(engine: Engine) -> None:
    """Мягко отзывает доступы теста; запись в журнале аудита остаётся."""
    with engine.begin() as connection:
        for kind, user, target in GRANTS:
            if kind == "project":
                connection.execute(
                    text(
                        "UPDATE user_project_access SET deleted_at = now()"
                        " WHERE user_id = :u AND project_id = :t AND deleted_at IS NULL"
                    ),
                    {"u": user, "t": target},
                )
            else:
                connection.execute(
                    text(
                        "UPDATE user_legal_entity_access SET deleted_at = now()"
                        " WHERE user_id = :u AND legal_entity_id = :t AND deleted_at IS NULL"
                    ),
                    {"u": user, "t": target},
                )
    GRANTS.clear()


@pytest.fixture(autouse=True)
def leave_no_access_behind(worker_sync_engine: Engine) -> Iterator[None]:
    yield
    revoke_test_grants(worker_sync_engine)


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def xlsx_ok() -> bytes:
    return zip_bytes(
        {
            "[Content_Types].xml": b'<Types><Override PartName="/xl/workbook.xml"'
            b' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml'
            b'.sheet.main+xml"/></Types>',
            "xl/workbook.xml": b"<workbook/>",
        }
    )


def docx_ok() -> bytes:
    return zip_bytes(
        {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<document/>",
        }
    )


def sha256_b64(content: bytes) -> str:
    return base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")


def legal_entity(engine: Engine) -> tuple[UUID, str]:
    """Юрлицо с кодом для номера: отдельное на тест."""
    code = "T" + uuid4().hex[:6].upper()
    with engine.begin() as connection:
        identifier = connection.execute(
            text(
                "INSERT INTO legal_entity (id, name, code) VALUES (gen_random_uuid(), :name, :code)"
                " RETURNING id"
            ),
            {"name": f"ЮЛ документы {code}", "code": code},
        ).scalar_one()
    return identifier, code


def contract(engine: Engine, legal: UUID, counterparty: UUID) -> UUID:
    """Шапка договора аренды техники (K = A): нужна видам contract_rental и addendum."""
    number = f"Д-{uuid4().hex[:8]}"
    with engine.begin() as connection:
        return connection.execute(
            text(
                "INSERT INTO contract (id, legal_entity_id, counterparty_id, kind, number,"
                " date_from) VALUES (gen_random_uuid(), :le, :cp, 'rental_equipment', :n,"
                " DATE '2026-01-01') RETURNING id"
            ),
            {"le": legal, "cp": counterparty, "n": number},
        ).scalar_one()


def project(engine: Engine, legal: UUID, users: tuple[UUID, ...] = ()) -> UUID:
    """Объект на тест; доступ выдаётся перечисленным пользователям."""
    identifier = second_project(engine, legal)
    for user in users:
        grant(engine, user, projects=(identifier,))
    return identifier


def give_access(engine: Engine, users: list[UUID], projects: tuple[UUID, ...], legal: UUID) -> None:
    for user in users:
        grant(engine, user, projects=projects, entities=(legal,))


def user_id(auth_world: AuthWorld, who: str) -> UUID:
    return auth_world.users[who]


async def create_document(
    api: AsyncClient, headers: dict[str, str], body: dict[str, Any]
) -> Response:
    return await api.post(f"{API}/documents", json=body, headers={**headers, **idem()})


async def upload(
    api: AsyncClient,
    s3: AsyncClient,
    document_id: str,
    headers: dict[str, str],
    content: bytes,
    content_type: str,
    *,
    comment: str | None = None,
) -> Response:
    """Загрузка версии как в браузере: presign, PUT напрямую в S3, подтверждение. Возвращает ответ
    того шага, где остановилась загрузка (при успехе - ответ подтверждения)."""
    presigned = await api.post(
        f"{API}/documents/{document_id}/versions/presign",
        json={
            "content_type": content_type,
            "size_bytes": len(content),
            "checksum_sha256": sha256_b64(content),
        },
        headers=headers,
    )
    if presigned.status_code != 200:
        return presigned
    item = presigned.json()
    uploaded = await s3.put(item["upload_url"], content=content, headers=item["headers"])
    if uploaded.status_code != 200:
        return uploaded
    return await api.post(
        f"{API}/documents/{document_id}/versions/confirm",
        json={"key": item["key"], "comment": comment},
        headers={**headers, **idem()},
    )


async def approve(version_id: str) -> None:
    """Утверждение без маршрута (маршруты - S6a): переход системной сессией, как у сида."""
    with system_context():
        async with get_sessionmaker()() as db:
            for target in ("on_approval", "approved"):
                await db.execute(
                    text("SELECT app.document_version_transition(:v, :to, NULL)"),
                    {"v": version_id, "to": target},
                )
            await db.commit()
