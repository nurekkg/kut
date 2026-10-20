"""Помощники тестов документов (ADR-015 часть 2). Каждый тест заводит свои юрлицо и объект с кодом:
данные других тестов не попадают в выборки (урок se2-hardening, решение F8)."""

import base64
import hashlib
import io
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import column, insert, select, table, text, update
from sqlalchemy.engine import Connection, Engine

from tests.auth_support import AuthWorld
from tests.equipment_support import headers_for, idem, second_project

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


PROJECT_ACCESS = table(
    "user_project_access", column("user_id"), column("project_id"), column("deleted_at")
)
ENTITY_ACCESS = table(
    "user_legal_entity_access",
    column("user_id"),
    column("legal_entity_id"),
    column("deleted_at"),
)


def _open(connection: Connection, access: Any, key: str, user: UUID, target: UUID) -> bool:
    """Открывает доступ. True - строка появилась или вернулась из мягкого удаления (такой доступ
    теста отзовём); False - доступ уже действует (его дал сид), его не трогаем."""
    row = connection.execute(
        select(access.c.deleted_at).where(access.c.user_id == user, access.c[key] == target)
    ).first()
    if row is None:
        connection.execute(insert(access).values(user_id=user, **{key: target}))
        return True
    if row.deleted_at is None:
        return False
    connection.execute(
        update(access)
        .where(access.c.user_id == user, access.c[key] == target)
        .values(deleted_at=None)
    )
    return True


def grant(
    engine: Engine, user: UUID, *, projects: tuple[UUID, ...] = (), entities: tuple[UUID, ...] = ()
) -> None:
    with engine.begin() as connection:
        for project in projects:
            if _open(connection, PROJECT_ACCESS, "project_id", user, project):
                GRANTS.append(("project", user, project))
        for entity in entities:
            if _open(connection, ENTITY_ACCESS, "legal_entity_id", user, entity):
                GRANTS.append(("entity", user, entity))


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


@dataclass
class Desk:
    """Юрлицо, объект и заголовки входа одного теста (для реестра, выгрузки и техники)."""

    legal: UUID
    project: UUID
    heads: dict[str, dict[str, str]] = field(default_factory=dict)


async def open_desk(api: AsyncClient, auth_world: AuthWorld, engine: Engine, *who: str) -> Desk:
    legal, _ = legal_entity(engine)
    desk = Desk(legal=legal, project=project(engine, legal))
    for name in who:
        give_access(engine, [auth_world.users[name]], (desk.project,), legal)
        desk.heads[name] = await headers_for(api, name)
    return desk


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
    amount: str | None = None,
) -> Response:
    """Загрузка версии как в браузере: presign, PUT напрямую в S3, подтверждение. Возвращает ответ
    того шага, где остановилась загрузка (при успехе - ответ подтверждения). Сумма - в подтверждении
    (R1)."""
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
        json={"key": item["key"], "comment": comment, "amount": amount},
        headers={**headers, **idem()},
    )


def approve(owner_engine: Engine, version_id: str) -> None:
    """Утверждение без маршрута (маршруты - S6a): переходы выполняет владелец под kut_auth, как
    сид. kut_worker функции app.version_move не имеет (S6b), kut_app переходов не выполняет."""
    for target in ("on_approval", "approved"):
        with owner_engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE kut_auth"))
            connection.execute(
                text("SELECT app.version_move(CAST(:v AS uuid), :to, NULL, NULL)"),
                {"v": version_id, "to": target},
            )
