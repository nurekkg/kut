"""Маршруты согласования версий (S6a-2, ADR-015 часть 5): шаблоны, отправка, решения,
замещение, очередь.

Все переходы и решения делают функции БД (`app.route_*`, `app.delegation_*`): права и порядок
шагов держит
БД, сервис вызывает функции и читает маршрут. Причину решения приложение читает только через
`app.decision_reason` (колонки `reason` у приложения нет).
"""

import base64
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rules import db_rules
from app.modules.auth.principal import Principal
from app.modules.documents import service
from app.modules.documents.models import DocumentVersion
from app.modules.documents.schemas import (
    DecisionIn,
    DelegationIn,
    DelegationOut,
    QueueItemOut,
    RouteDecisionOut,
    RouteOut,
    RouteStepOut,
    SignedScanIn,
    TemplateOut,
    TemplateStepOut,
    VersionOut,
)


async def _version_status(
    session: AsyncSession, document_id: uuid.UUID, version_id: uuid.UUID
) -> str:
    """Статус версии, видимой пользователю (RLS); чужая или невидимая - 404."""
    current = await session.scalar(
        text(
            "SELECT v.status FROM document_version v WHERE v.id = CAST(:v AS uuid)"
            " AND v.document_id = CAST(:d AS uuid)"
        ),
        {"v": version_id, "d": document_id},
    )
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return str(current)


async def _names(session: AsyncSession, ids: set[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    present = [item for item in ids if item is not None]
    if not present:
        return {}
    rows = (
        await session.execute(
            text('SELECT u.id, u.full_name FROM "user" u WHERE u.id = ANY(CAST(:ids AS uuid[]))'),
            {"ids": [str(item) for item in present]},
        )
    ).all()
    return {row.id: row.full_name for row in rows}


async def templates(session: AsyncSession) -> list[TemplateOut]:
    rows = (
        await session.execute(
            text(
                "SELECT t.code, t.name, s.position, s.role_code"
                " FROM approval_route_template t"
                " LEFT JOIN approval_route_step_template s"
                " ON s.template_id = t.id AND s.deleted_at IS NULL"
                " WHERE t.deleted_at IS NULL ORDER BY t.code, s.position"
            )
        )
    ).all()
    result: dict[str, TemplateOut] = {}
    for row in rows:
        item = result.setdefault(row.code, TemplateOut(code=row.code, name=row.name, steps=[]))
        if row.position is not None:
            item.steps.append(TemplateStepOut(position=row.position, role_code=row.role_code))
    return list(result.values())


async def save_template(session: AsyncSession, code: str, roles: list[str]) -> list[TemplateOut]:
    """Шаги шаблона целиком: владелец и техдиректор (право documents:write:routes проверяет
    функция)."""
    async with db_rules(session):
        await session.execute(
            text("SELECT app.route_template_save(CAST(:c AS text), CAST(:r AS text[]))"),
            {"c": code, "r": roles},
        )
    return await templates(session)


async def _route(session: AsyncSession, document_id: uuid.UUID, version_id: uuid.UUID) -> RouteOut:
    version_status = await _version_status(session, document_id, version_id)
    steps = (
        await session.execute(
            text(
                "SELECT s.attempt, s.position, s.role_code, s.status, s.decided_by, s.on_behalf_of,"
                " s.decided_at FROM version_route_step s WHERE s.version_id = CAST(:v AS uuid)"
                " ORDER BY s.attempt, s.position"
            ),
            {"v": version_id},
        )
    ).all()
    decisions = (
        await session.execute(
            text(
                "SELECT d.id, d.attempt, d.position, d.role_code, d.decision, d.actor_id,"
                " d.on_behalf_of, d.decided_at FROM approval_decision d"
                " WHERE d.version_id = CAST(:v AS uuid) ORDER BY d.decided_at, d.id"
            ),
            {"v": version_id},
        )
    ).all()
    names = await _names(
        session,
        {row.decided_by for row in steps}
        | {row.on_behalf_of for row in steps}
        | {row.actor_id for row in decisions}
        | {row.on_behalf_of for row in decisions},
    )
    decision_rows = []
    for row in decisions:
        reason = await session.scalar(
            text("SELECT app.decision_reason(CAST(:d AS uuid))"), {"d": row.id}
        )
        decision_rows.append(
            RouteDecisionOut(
                id=row.id,
                attempt=row.attempt,
                position=row.position,
                role_code=row.role_code,
                decision=row.decision,
                reason=reason,
                actor_name=names.get(row.actor_id),
                on_behalf_of_name=names.get(row.on_behalf_of) if row.on_behalf_of else None,
                decided_at=row.decided_at,
            )
        )
    attempts = max((row.attempt for row in steps), default=0)
    current = await session.scalar(
        text("SELECT app.route_current_position(CAST(:v AS uuid))"), {"v": version_id}
    )
    can_decide = await session.scalar(
        text("SELECT app.route_can_decide(CAST(:v AS uuid))"), {"v": version_id}
    )
    return RouteOut(
        version_id=version_id,
        version_status=version_status,
        attempts=attempts,
        current_position=current,
        can_decide=bool(can_decide),
        steps=[
            RouteStepOut(
                attempt=row.attempt,
                position=row.position,
                role_code=row.role_code,
                status=row.status,
                decided_by_name=names.get(row.decided_by) if row.decided_by else None,
                on_behalf_of_name=names.get(row.on_behalf_of) if row.on_behalf_of else None,
                decided_at=row.decided_at,
            )
            for row in steps
        ],
        decisions=decision_rows,
    )


async def route(session: AsyncSession, document_id: uuid.UUID, version_id: uuid.UUID) -> RouteOut:
    return await _route(session, document_id, version_id)


async def send(session: AsyncSession, document_id: uuid.UUID, version_id: uuid.UUID) -> RouteOut:
    """Отправка: draft -> on_approval, снимок шагов шаблона; функция проверяет право записи и
    файл."""
    await _version_status(session, document_id, version_id)
    async with db_rules(session):
        await session.execute(text("SELECT app.route_send(CAST(:v AS uuid))"), {"v": version_id})
    return await _route(session, document_id, version_id)


async def decide(
    session: AsyncSession, document_id: uuid.UUID, version_id: uuid.UUID, body: DecisionIn
) -> RouteOut:
    await _version_status(session, document_id, version_id)
    async with db_rules(session):
        await session.execute(
            text("SELECT app.route_decide(CAST(:v AS uuid), :p, :d, :r)"),
            {"v": version_id, "p": body.position, "d": body.decision, "r": body.reason},
        )
    return await _route(session, document_id, version_id)


_DELEGATION_BY_ID = (
    "SELECT dl.id, dl.delegator_id, dl.delegate_id, dl.role_code, dl.valid_from, dl.valid_to,"
    " dl.revoked_at FROM document_delegation dl WHERE dl.id = CAST(:i AS uuid)"
)
_DELEGATIONS_MINE = (
    "SELECT dl.id, dl.delegator_id, dl.delegate_id, dl.role_code, dl.valid_from, dl.valid_to,"
    " dl.revoked_at FROM document_delegation dl"
    " WHERE (dl.delegator_id = app.user_id() OR dl.delegate_id = app.user_id())"
    " AND (dl.revoked_at IS NULL OR dl.valid_to >= app.local_today())"
    " ORDER BY dl.valid_from DESC"
)


async def _delegation_rows(
    session: AsyncSession, sql: str, params: dict[str, Any]
) -> list[DelegationOut]:
    rows = (await session.execute(text(sql), params)).all()
    names = await _names(
        session, {row.delegator_id for row in rows} | {row.delegate_id for row in rows}
    )
    return [
        DelegationOut(
            id=row.id,
            delegator_id=row.delegator_id,
            delegator_name=names.get(row.delegator_id),
            delegate_id=row.delegate_id,
            delegate_name=names.get(row.delegate_id),
            role_code=row.role_code,
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            revoked=row.revoked_at is not None,
        )
        for row in rows
    ]


async def create_delegation(session: AsyncSession, body: DelegationIn) -> DelegationOut:
    async with db_rules(session):
        new_id = await session.scalar(
            text("SELECT app.delegation_create(:d, :r, :f, :t)"),
            {"d": body.delegate_id, "r": body.role_code, "f": body.valid_from, "t": body.valid_to},
        )
    rows = await _delegation_rows(session, _DELEGATION_BY_ID, {"i": new_id})
    return rows[0]


async def revoke_delegation(session: AsyncSession, delegation_id: uuid.UUID) -> None:
    async with db_rules(session):
        await session.execute(
            text("SELECT app.delegation_revoke(CAST(:i AS uuid))"), {"i": delegation_id}
        )


async def delegations(session: AsyncSession) -> list[DelegationOut]:
    """Замещения, в которых участвует пользователь (делегирующий или замещающий) и действующие."""
    return await _delegation_rows(session, _DELEGATIONS_MINE, {})


async def queue(session: AsyncSession) -> list[QueueItemOut]:
    """Очередь «На согласовании»: версии, где текущий шаг мой (роль или замещение) и есть право
    решать."""
    rows = (
        await session.execute(
            text(
                "SELECT v.document_id, v.id AS version_id, v.version_no, d.title, d.kind,"
                " d.project_id, p.name AS project_name, s.attempt, s.position, s.role_code"
                " FROM document_version v"
                " JOIN document d ON d.id = v.document_id"
                " LEFT JOIN project p ON p.id = d.project_id"
                " JOIN version_route_step s ON s.version_id = v.id AND s.status = 'pending'"
                " WHERE v.status = 'on_approval'"
                " AND s.attempt = (SELECT max(x.attempt) FROM version_route_step x"
                "                  WHERE x.version_id = v.id)"
                " AND s.position = (SELECT min(y.position) FROM version_route_step y"
                "                   WHERE y.version_id = v.id AND y.attempt = s.attempt"
                "                     AND y.status = 'pending')"
                " AND v.created_by IS DISTINCT FROM app.user_id()"
                " AND app.has_permission('documents', 'approve')"
                " AND (s.role_code = app.user_role_code() OR EXISTS ("
                "   SELECT 1 FROM document_delegation dl WHERE dl.delegate_id = app.user_id()"
                "   AND dl.role_code = s.role_code AND dl.revoked_at IS NULL"
                "   AND app.local_today() BETWEEN dl.valid_from AND dl.valid_to))"
                " ORDER BY v.created_at"
            )
        )
    ).all()
    return [
        QueueItemOut(
            document_id=row.document_id,
            version_id=row.version_id,
            version_no=row.version_no,
            document_title=row.title,
            kind=row.kind,
            project_id=row.project_id,
            project_name=row.project_name,
            attempt=row.attempt,
            position=row.position,
            role_code=row.role_code,
        )
        for row in rows
    ]


async def sign_scan(
    session: AsyncSession,
    principal: Principal,
    document_id: uuid.UUID,
    parent_id: uuid.UUID,
    body: SignedScanIn,
) -> VersionOut:
    """Подписанный скан: версия `signed` от согласованной версии; гард БД проверяет родителя,
    триггер
    делает её действующей и переводит родителя в архив."""
    document = await service._writable(session, document_id)
    parent = await session.scalar(
        text(
            "SELECT v.status FROM document_version v WHERE v.id = CAST(:p AS uuid)"
            " AND v.document_id = CAST(:d AS uuid)"
        ),
        {"p": parent_id, "d": document_id},
    )
    if parent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if parent != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, "document.parent_not_approved")
    stored = await service.stored_upload(document, body.key)
    await service._lock_live(session, document_id)
    version_no = await session.scalar(
        text("SELECT app.document_next_version(:document)"), {"document": document_id}
    )
    version = DocumentVersion(
        document_id=document_id,
        legal_entity_id=document.legal_entity_id,
        project_id=document.project_id,
        version_no=version_no,
        s3_key=body.key,
        content_type=stored.content_type,
        size_bytes=stored.size,
        checksum_sha256=_hex_checksum(stored.checksum_sha256),
        comment=body.comment,
        status="signed",
        parent_version_id=parent_id,
    )
    session.add(version)
    async with db_rules(session):
        await session.flush()
    return service._version_out(
        (
            await session.execute(service._versions(True).where(DocumentVersion.id == version.id))
        ).one(),
        internal=True,
    )


def _hex_checksum(encoded: str) -> str:
    return base64.b64decode(encoded).hex()
