"""Вызовы SECURITY DEFINER-функций app.* (миграция a56217376426).

Приложение (kut_app) не читает и не пишет учётные данные напрямую: пароль, секрет TOTP, роль и
сессии доступны только этим функциям. Методы вызываются в рамках system_context().
"""

import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class LoginRecord:
    user_id: uuid.UUID
    password_hash: str | None
    is_active: bool
    requires_2fa: bool
    totp_confirmed: bool
    role_scope: str
    role_code: str


@dataclass(frozen=True, slots=True)
class ContextRecord:
    user_id: uuid.UUID
    full_name: str
    is_active: bool
    role_ver: int
    role_code: str
    role_scope: str
    requires_2fa: bool
    counterparty_id: uuid.UUID | None
    totp_confirmed: bool
    permissions: frozenset[str]
    project_ids: frozenset[uuid.UUID]
    legal_entity_ids: frozenset[uuid.UUID]

    def to_json(self) -> str:
        return json.dumps(
            {
                "user_id": str(self.user_id),
                "full_name": self.full_name,
                "is_active": self.is_active,
                "role_ver": self.role_ver,
                "role_code": self.role_code,
                "role_scope": self.role_scope,
                "requires_2fa": self.requires_2fa,
                "counterparty_id": str(self.counterparty_id) if self.counterparty_id else None,
                "totp_confirmed": self.totp_confirmed,
                "permissions": sorted(self.permissions),
                "project_ids": sorted(str(item) for item in self.project_ids),
                "legal_entity_ids": sorted(str(item) for item in self.legal_entity_ids),
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "ContextRecord":
        data = json.loads(raw)
        counterparty = data["counterparty_id"]
        return cls(
            user_id=uuid.UUID(data["user_id"]),
            full_name=data["full_name"],
            is_active=data["is_active"],
            role_ver=data["role_ver"],
            role_code=data["role_code"],
            role_scope=data["role_scope"],
            requires_2fa=data["requires_2fa"],
            counterparty_id=uuid.UUID(counterparty) if counterparty else None,
            totp_confirmed=data["totp_confirmed"],
            permissions=frozenset(data["permissions"]),
            project_ids=frozenset(uuid.UUID(item) for item in data["project_ids"]),
            legal_entity_ids=frozenset(uuid.UUID(item) for item in data["legal_entity_ids"]),
        )


@dataclass(frozen=True, slots=True)
class RoleChange:
    role_ver: int
    families: list[uuid.UUID]


RotateStatus = Literal["ok", "invalid", "reused", "inactive"]


@dataclass(frozen=True, slots=True)
class RotateResult:
    status: RotateStatus
    user_id: uuid.UUID | None
    family_id: uuid.UUID | None
    amr: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TotpRecord:
    secret_enc: bytes | None
    key_id: str | None
    confirmed: bool


async def login_lookup(session: AsyncSession, login: str) -> LoginRecord | None:
    row = (
        await session.execute(text("SELECT * FROM app.auth_login_lookup(:login)"), {"login": login})
    ).one_or_none()
    if row is None:
        return None
    return LoginRecord(
        user_id=row.o_user_id,
        password_hash=row.o_password_hash,
        is_active=row.o_is_active,
        requires_2fa=row.o_requires_2fa,
        totp_confirmed=row.o_totp_confirmed,
        role_scope=row.o_role_scope,
        role_code=row.o_role_code,
    )


async def password_hash(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    value = await session.scalar(
        text("SELECT app.auth_password_hash(:user_id)"), {"user_id": user_id}
    )
    return str(value) if value is not None else None


async def auth_context(session: AsyncSession, user_id: uuid.UUID) -> ContextRecord | None:
    row = (
        await session.execute(
            text("SELECT * FROM app.auth_context(:user_id)"), {"user_id": user_id}
        )
    ).one_or_none()
    if row is None:
        return None
    return ContextRecord(
        user_id=row.o_user_id,
        full_name=row.o_full_name,
        is_active=row.o_is_active,
        role_ver=row.o_role_ver,
        role_code=row.o_role_code,
        role_scope=row.o_role_scope,
        requires_2fa=row.o_requires_2fa,
        counterparty_id=row.o_counterparty_id,
        totp_confirmed=row.o_totp_confirmed,
        permissions=frozenset(row.o_permissions or []),
        project_ids=frozenset(row.o_project_ids or []),
        legal_entity_ids=frozenset(row.o_legal_entity_ids or []),
    )


async def refresh_issue(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    token_hash: bytes,
    ttl: timedelta,
    amr: tuple[str, ...],
) -> None:
    await session.execute(
        text(
            "SELECT app.refresh_issue(:user_id, :family_id, :token_hash,"
            " CAST(:ttl AS interval), CAST(:amr AS text[]))"
        ),
        {
            "user_id": user_id,
            "family_id": family_id,
            "token_hash": token_hash,
            "ttl": ttl,
            "amr": list(amr),
        },
    )


async def refresh_rotate(
    session: AsyncSession, *, old_hash: bytes, new_hash: bytes
) -> RotateResult:
    row = (
        await session.execute(
            text("SELECT * FROM app.refresh_rotate(:old, :new)"),
            {"old": old_hash, "new": new_hash},
        )
    ).one()
    return RotateResult(
        status=row.o_status,
        user_id=row.o_user_id,
        family_id=row.o_family_id,
        amr=tuple(row.o_amr or ()),
    )


async def refresh_logout(
    session: AsyncSession, token_hash: bytes
) -> tuple[uuid.UUID, uuid.UUID] | None:
    row = (
        await session.execute(
            text("SELECT * FROM app.refresh_logout(:token_hash)"), {"token_hash": token_hash}
        )
    ).one_or_none()
    return (row.o_user_id, row.o_family_id) if row is not None else None


async def totp_get(session: AsyncSession, user_id: uuid.UUID) -> TotpRecord | None:
    row = (
        await session.execute(text("SELECT * FROM app.totp_get(:user_id)"), {"user_id": user_id})
    ).one_or_none()
    if row is None:
        return None
    return TotpRecord(
        secret_enc=bytes(row.o_secret_enc) if row.o_secret_enc is not None else None,
        key_id=row.o_key_id,
        confirmed=row.o_confirmed,
    )


async def totp_set_pending(
    session: AsyncSession, *, user_id: uuid.UUID, secret_enc: bytes, key_id: str
) -> bool:
    return bool(
        await session.scalar(
            text("SELECT app.totp_set_pending(:user_id, :secret_enc, :key_id)"),
            {"user_id": user_id, "secret_enc": secret_enc, "key_id": key_id},
        )
    )


async def totp_confirm(
    session: AsyncSession, *, user_id: uuid.UUID, code_hashes: list[bytes]
) -> bool:
    return bool(
        await session.scalar(
            text("SELECT app.totp_confirm(:user_id, CAST(:hashes AS bytea[]))"),
            {"user_id": user_id, "hashes": code_hashes},
        )
    )


async def recovery_code_use(session: AsyncSession, *, user_id: uuid.UUID, code_hash: bytes) -> bool:
    return bool(
        await session.scalar(
            text("SELECT app.recovery_code_use(:user_id, :code_hash)"),
            {"user_id": user_id, "code_hash": code_hash},
        )
    )


async def change_password(
    session: AsyncSession, *, user_id: uuid.UUID, new_hash: str, keep_family: uuid.UUID
) -> list[uuid.UUID]:
    families = await session.scalar(
        text("SELECT app.change_password(:user_id, :new_hash, :keep)"),
        {"user_id": user_id, "new_hash": new_hash, "keep": keep_family},
    )
    return list(families or [])


async def audit_event(
    session: AsyncSession,
    action: str,
    *,
    table: str | None = None,
    row_id: uuid.UUID | None = None,
    actor: uuid.UUID | None = None,
    ip: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    await session.execute(
        text(
            "SELECT app.audit_event(:action, :table, :row_id, :actor,"
            " CAST(:ip AS inet), CAST(:meta AS jsonb))"
        ),
        {
            "action": action,
            "table": table,
            "row_id": row_id,
            "actor": actor,
            "ip": ip,
            "meta": json.dumps(meta) if meta is not None else None,
        },
    )


async def assign_role(
    session: AsyncSession, *, user_id: uuid.UUID, role_id: uuid.UUID
) -> RoleChange:
    row = (
        await session.execute(
            text("SELECT * FROM app.assign_role(:user_id, :role_id)"),
            {"user_id": user_id, "role_id": role_id},
        )
    ).one()
    return RoleChange(role_ver=row.o_role_ver, families=list(row.o_families or []))


async def set_user_active(session: AsyncSession, *, user_id: uuid.UUID, active: bool) -> RoleChange:
    row = (
        await session.execute(
            text("SELECT * FROM app.set_user_active(:user_id, :active)"),
            {"user_id": user_id, "active": active},
        )
    ).one()
    return RoleChange(role_ver=row.o_role_ver, families=list(row.o_families or []))
