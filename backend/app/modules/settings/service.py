import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.context import require_context, use_context
from app.core.rls import apply_context
from app.modules.auth import admin, repository
from app.modules.auth.principal import principal_store
from app.modules.auth.security import new_opaque_token, sha256
from app.modules.identity.models import User, UserLegalEntityAccess, UserProjectAccess
from app.modules.objects.models import Project
from app.modules.org.models import Counterparty, LegalEntity
from app.modules.rbac.models import Role
from app.modules.settings.schemas import (
    AccessChange,
    CounterpartyIn,
    CounterpartyOut,
    EventOut,
    EventPage,
    InviteOut,
    LegalEntityIn,
    LegalEntityOut,
    ProjectOut,
    RoleOut,
    UserAdminOut,
    UserCreate,
)

MAX_LIST = 500


class SettingsError(Exception):
    """Ошибка бизнес-правила: статус и код ответа выбирает сервис, роутер только переводит."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def not_found() -> SettingsError:
    return SettingsError(404, "not_found")


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --- юрлица ----------------------------------------------------------------------------------


async def _project_count(session: AsyncSession, entity_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count(Project.id)).where(
            Project.legal_entity_id == entity_id, Project.deleted_at.is_(None)
        )
    )
    return int(count or 0)


async def list_legal_entities(session: AsyncSession) -> list[LegalEntityOut]:
    rows = await session.execute(
        select(
            LegalEntity.id,
            LegalEntity.name,
            LegalEntity.tax_id,
            func.count(Project.id).label("project_count"),
        )
        .outerjoin(
            Project, (Project.legal_entity_id == LegalEntity.id) & Project.deleted_at.is_(None)
        )
        .group_by(LegalEntity.id, LegalEntity.name, LegalEntity.tax_id)
        .order_by(LegalEntity.name, LegalEntity.id)
    )
    return [LegalEntityOut.model_validate(row, from_attributes=True) for row in rows]


async def create_legal_entity(session: AsyncSession, data: LegalEntityIn) -> LegalEntityOut:
    """Без COMMIT: вызывается внутри идемпотентной транзакции."""
    new_id = uuid.uuid4()
    context = require_context()
    # SELECT-политика юрлица пускает только известные контексту id: без этого INSERT ... RETURNING
    # не увидел бы собственную строку (ADR-001, apply_context).
    with use_context(replace(context, legal_entity_ids=context.legal_entity_ids | {new_id})):
        await apply_context(session)
    session.add(LegalEntity(id=new_id, name=data.name, tax_id=data.tax_id))
    await session.flush()
    return LegalEntityOut(id=new_id, name=data.name, tax_id=data.tax_id, project_count=0)


async def _legal_entity(session: AsyncSession, entity_id: uuid.UUID) -> LegalEntity:
    entity = (
        await session.scalars(select(LegalEntity).where(LegalEntity.id == entity_id))
    ).one_or_none()
    if entity is None:
        raise not_found()
    return entity


async def update_legal_entity(
    session: AsyncSession, entity_id: uuid.UUID, data: LegalEntityIn
) -> LegalEntityOut:
    entity = await _legal_entity(session, entity_id)
    entity.name, entity.tax_id = data.name, data.tax_id
    await session.commit()
    return LegalEntityOut(
        id=entity.id,
        name=entity.name,
        tax_id=entity.tax_id,
        project_count=await _project_count(session, entity.id),
    )


async def delete_legal_entity(
    session: AsyncSession, entity_id: uuid.UUID, actor: uuid.UUID
) -> None:
    await _legal_entity(session, entity_id)
    if await _project_count(session, entity_id):
        raise SettingsError(409, "legal_entity_in_use")
    await session.execute(
        update(LegalEntity)
        .where(LegalEntity.id == entity_id)
        .values(deleted_at=func.now(), updated_by=actor)
        .execution_options(synchronize_session=False)
    )
    await session.commit()


# --- контрагенты -------------------------------------------------------------------------------


def _counterparty_out(entity: Counterparty) -> CounterpartyOut:
    return CounterpartyOut(
        id=entity.id, name=entity.name, tax_id=entity.tax_id, types=list(entity.types)
    )


async def list_counterparties(
    session: AsyncSession, *, kind: str | None, query: str | None, limit: int, offset: int
) -> list[CounterpartyOut]:
    statement = select(Counterparty).order_by(Counterparty.name, Counterparty.id)
    if kind:
        statement = statement.where(Counterparty.types.contains([kind]))
    if query:
        pattern = _like(query)
        statement = statement.where(
            or_(
                Counterparty.name.ilike(pattern, escape="\\"),
                Counterparty.tax_id.ilike(pattern, escape="\\"),
            )
        )
    rows = await session.scalars(statement.limit(min(limit, MAX_LIST)).offset(offset))
    return [_counterparty_out(row) for row in rows]


async def create_counterparty(session: AsyncSession, data: CounterpartyIn) -> CounterpartyOut:
    """Без COMMIT: вызывается внутри идемпотентной транзакции."""
    entity = Counterparty(
        name=data.name, tax_id=data.tax_id, types=[kind.value for kind in data.types]
    )
    session.add(entity)
    await session.flush()
    return _counterparty_out(entity)


async def _counterparty(session: AsyncSession, entity_id: uuid.UUID) -> Counterparty:
    entity = (
        await session.scalars(select(Counterparty).where(Counterparty.id == entity_id))
    ).one_or_none()
    if entity is None:
        raise not_found()
    return entity


async def update_counterparty(
    session: AsyncSession, entity_id: uuid.UUID, data: CounterpartyIn
) -> CounterpartyOut:
    entity = await _counterparty(session, entity_id)
    entity.name, entity.tax_id = data.name, data.tax_id
    entity.types = [kind.value for kind in data.types]
    await session.commit()
    return _counterparty_out(entity)


async def delete_counterparty(
    session: AsyncSession, entity_id: uuid.UUID, actor: uuid.UUID
) -> None:
    await _counterparty(session, entity_id)
    linked = await session.scalar(
        select(func.count(User.id)).where(
            User.counterparty_id == entity_id, User.deleted_at.is_(None)
        )
    )
    if linked:
        raise SettingsError(409, "counterparty_in_use")
    await session.execute(
        update(Counterparty)
        .where(Counterparty.id == entity_id)
        .values(deleted_at=func.now(), updated_by=actor)
        .execution_options(synchronize_session=False)
    )
    await session.commit()


# --- справочники для форм ---------------------------------------------------------------------


async def list_roles(session: AsyncSession) -> list[RoleOut]:
    rows = await session.scalars(select(Role).order_by(Role.rank.desc(), Role.code))
    return [
        RoleOut(id=r.id, code=r.code, name=r.name, scope=r.scope, requires_2fa=r.requires_2fa)
        for r in rows
    ]


async def list_projects(session: AsyncSession) -> list[ProjectOut]:
    rows = await session.execute(
        select(Project.id, Project.name, Project.legal_entity_id).order_by(Project.name, Project.id)
    )
    return [ProjectOut.model_validate(row, from_attributes=True) for row in rows]


# --- пользователи ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UserFilter:
    role: str | None = None
    project_id: uuid.UUID | None = None
    active: bool | None = None
    query: str | None = None
    user_id: uuid.UUID | None = None


async def list_users(
    session: AsyncSession, filters: UserFilter, *, limit: int = 100, offset: int = 0
) -> list[UserAdminOut]:
    statement = (
        select(
            User.id,
            User.full_name,
            User.email,
            User.phone,
            User.is_active,
            User.totp_confirmed_at,
            User.password_changed_at,
            User.counterparty_id,
            User.notify_whatsapp,
            User.notify_whatsapp_consent_at,
            User.notify_whatsapp_consent_version,
            Counterparty.name.label("counterparty_name"),
            Role.id.label("role_id"),
            Role.code.label("role"),
            Role.name.label("role_name"),
            Role.scope,
            Role.requires_2fa,
        )
        .join(Role, Role.id == User.role_id)
        .outerjoin(Counterparty, Counterparty.id == User.counterparty_id)
        .order_by(User.full_name, User.id)
    )
    if filters.user_id is not None:
        statement = statement.where(User.id == filters.user_id)
    if filters.role:
        statement = statement.where(Role.code == filters.role)
    if filters.active is not None:
        statement = statement.where(User.is_active.is_(filters.active))
    if filters.project_id is not None:
        has_access = exists().where(
            UserProjectAccess.user_id == User.id,
            UserProjectAccess.project_id == filters.project_id,
            UserProjectAccess.deleted_at.is_(None),
        )
        # Роли scope=group видят все объекты без записей доступа.
        statement = statement.where(or_(has_access, Role.scope == "group"))
    if filters.query:
        pattern = _like(filters.query)
        statement = statement.where(
            or_(
                User.full_name.ilike(pattern, escape="\\"),
                User.email.ilike(pattern, escape="\\"),
                User.phone.ilike(pattern, escape="\\"),
            )
        )
    rows = (await session.execute(statement.limit(min(limit, MAX_LIST)).offset(offset))).all()
    ids = [row.id for row in rows]
    projects: dict[uuid.UUID, list[uuid.UUID]] = {}
    entities: dict[uuid.UUID, list[uuid.UUID]] = {}
    if ids:
        project_rows = await session.execute(
            select(UserProjectAccess.user_id, UserProjectAccess.project_id)
            .join(Project, Project.id == UserProjectAccess.project_id)
            .where(UserProjectAccess.user_id.in_(ids), UserProjectAccess.deleted_at.is_(None))
            .order_by(Project.name)
        )
        for user_id, project_id in project_rows:
            projects.setdefault(user_id, []).append(project_id)
        entity_rows = await session.execute(
            select(UserLegalEntityAccess.user_id, UserLegalEntityAccess.legal_entity_id).where(
                UserLegalEntityAccess.user_id.in_(ids), UserLegalEntityAccess.deleted_at.is_(None)
            )
        )
        for user_id, entity_id in entity_rows:
            entities.setdefault(user_id, []).append(entity_id)
    return [
        UserAdminOut(
            id=row.id,
            full_name=row.full_name,
            email=row.email,
            phone=row.phone,
            role_id=row.role_id,
            role=row.role,
            role_name=row.role_name,
            scope=row.scope,
            is_active=row.is_active,
            mfa_enabled=row.totp_confirmed_at is not None,
            requires_2fa=row.requires_2fa,
            pending_activation=row.password_changed_at is None,
            counterparty_id=row.counterparty_id,
            counterparty_name=row.counterparty_name,
            project_ids=projects.get(row.id, []),
            legal_entity_ids=entities.get(row.id, []),
            notify_whatsapp=row.notify_whatsapp,
            notify_whatsapp_consent_at=row.notify_whatsapp_consent_at,
            notify_whatsapp_consent_version=row.notify_whatsapp_consent_version,
        )
        for row in rows
    ]


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> UserAdminOut:
    found = await list_users(session, UserFilter(user_id=user_id), limit=1)
    if not found:
        raise not_found()
    return found[0]


async def create_user(session: AsyncSession, data: UserCreate) -> UserAdminOut:
    """Без COMMIT: вызывается внутри идемпотентной транзакции. Пароля у пользователя нет."""
    user_id = await admin.create_user(
        session,
        email=data.email,
        phone=data.phone,
        full_name=data.full_name,
        role_id=data.role_id,
        counterparty_id=data.counterparty_id,
        commit=False,
    )
    return await get_user(session, user_id)


async def change_role(
    session: AsyncSession, user_id: uuid.UUID, role_id: uuid.UUID
) -> UserAdminOut:
    target = await get_user(session, user_id)
    role = (await session.scalars(select(Role).where(Role.id == role_id))).one_or_none()
    if role is None:
        raise not_found()
    # Внешний пользователь привязан к контрагенту, внутренний нет: класс роли не меняется.
    if (target.scope == "counterparty") != (role.scope == "counterparty"):
        raise SettingsError(422, "role_scope_mismatch")
    await admin.assign_role(session, user_id=user_id, role_id=role_id)
    return await get_user(session, user_id)


async def set_active(session: AsyncSession, user_id: uuid.UUID, active: bool) -> UserAdminOut:
    await admin.set_user_active(session, user_id=user_id, active=active)
    return await get_user(session, user_id)


async def whatsapp_off(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Владелец выключает WhatsApp сотруднику (согласие отозвано; дату ставит гард БД)."""
    found = (
        await session.execute(
            text("SELECT app.admin_whatsapp_off(CAST(:u AS uuid))"), {"u": user_id}
        )
    ).scalar_one()
    if not found:
        await session.rollback()
        raise not_found()
    await session.commit()


async def reset_mfa(session: AsyncSession, user_id: uuid.UUID) -> UserAdminOut:
    await admin.reset_totp(session, user_id=user_id)
    return await get_user(session, user_id)


async def issue_invite(session: AsyncSession, user_id: uuid.UUID) -> InviteOut:
    """Ссылка активации (и сброса пароля) показывается один раз; прежние ссылки отменяются."""
    token = new_opaque_token()
    ttl = timedelta(hours=get_settings().invite_ttl_hours)
    expires = await admin.issue_invite(session, user_id=user_id, token_hash=sha256(token), ttl=ttl)
    return InviteOut(path=f"/activate?token={token}", expires_at=expires)


async def _sync_links(
    session: AsyncSession,
    model: type[UserProjectAccess] | type[UserLegalEntityAccess],
    column: str,
    user_id: uuid.UUID,
    wanted: set[uuid.UUID],
    actor: uuid.UUID,
) -> None:
    target = getattr(model, column)
    current = set(
        await session.scalars(
            select(target).where(model.user_id == user_id, model.deleted_at.is_(None))
        )
    )
    for item in sorted(wanted - current, key=str):
        session.add(model(user_id=user_id, **{column: item}))
    removed = current - wanted
    if removed:
        await session.execute(
            update(model)
            .where(model.user_id == user_id, target.in_(removed), model.deleted_at.is_(None))
            .values(deleted_at=func.now(), updated_by=actor)
            .execution_options(synchronize_session=False)
        )


async def set_access(
    session: AsyncSession, user_id: uuid.UUID, data: AccessChange, actor: uuid.UUID
) -> UserAdminOut:
    target = await get_user(session, user_id)
    projects, entities = set(data.project_ids), set(data.legal_entity_ids)
    if target.scope == "group":
        raise SettingsError(422, "group_role_has_all_access")
    if target.scope == "counterparty" and entities:
        raise SettingsError(422, "external_has_no_legal_entity_access")
    known_projects = set(await session.scalars(select(Project.id).where(Project.id.in_(projects))))
    known_entities = set(
        await session.scalars(select(LegalEntity.id).where(LegalEntity.id.in_(entities)))
    )
    if known_projects != projects:
        raise SettingsError(422, "unknown_project")
    if known_entities != entities:
        raise SettingsError(422, "unknown_legal_entity")
    await _sync_links(session, UserProjectAccess, "project_id", user_id, projects, actor)
    await _sync_links(session, UserLegalEntityAccess, "legal_entity_id", user_id, entities, actor)
    await session.commit()
    await principal_store.forget(
        user_id
    )  # объекты в /projects меняются сразу, а не через минуту кэша
    return await get_user(session, user_id)


# --- журнал событий входа -----------------------------------------------------------------------


async def list_events(
    session: AsyncSession,
    *,
    limit: int,
    before: int | None,
    action: str | None,
    actor: uuid.UUID | None,
    ip: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> EventPage:
    events = await repository.auth_events(
        session,
        limit=limit,
        before=before,
        action=action,
        actor=actor,
        ip=ip,
        date_from=date_from,
        date_to=date_to,
    )
    items: list[Any] = [
        EventOut(
            id=e.id,
            occurred_at=e.occurred_at,
            action=e.action,
            actor_id=e.actor_id,
            actor_name=e.actor_name,
            actor_kind=e.actor_kind,
            ip=e.ip,
            subject_id=e.subject_id,
            subject_name=e.subject_name,
            meta=e.meta,
        )
        for e in events
    ]
    return EventPage(items=items, next_before=items[-1].id if len(items) >= limit else None)
