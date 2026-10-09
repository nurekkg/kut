import uuid
from dataclasses import replace
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.context import require_context, use_context
from app.core.rls import apply_context
from app.core.rules import db_rules
from app.modules.identity.models import User, UserProjectAccess
from app.modules.objects.models import Project, ProjectFinance, ProjectNode
from app.modules.org.models import Counterparty, LegalEntity
from app.modules.projects.schemas import (
    FINANCE_FIELDS,
    NodeCreate,
    NodeDetail,
    NodePatch,
    ProjectCreate,
    ProjectOut,
    ProjectPatch,
)
from app.modules.rbac.models import Role


def not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


# --- объекты ----------------------------------------------------------------------------------

# Внешний пул (kut_portal) читает только выданные колонки; остальное у него закрыто на уровне БД,
# поэтому для внешних ролей запрос строится из безопасного списка, а не из select(Project).
_PROJECT_PORTAL = (Project.id, Project.name, Project.status, Project.legal_entity_id)


def _project_select(*, internal: bool) -> Select[Any]:
    if not internal:
        return select(*_PROJECT_PORTAL)
    live_nodes = (
        select(func.count(ProjectNode.id))
        .where(ProjectNode.project_id == Project.id, ProjectNode.deleted_at.is_(None))
        .correlate(Project)
        .scalar_subquery()
    )
    # Коммерческие поля читаются из project_finance: у ролей без finance:read политика RLS отдаёт
    # ноль строк, и внешнее соединение даёт NULL (границу ставит БД, а не этот запрос).
    return (
        select(
            *_PROJECT_PORTAL,
            Project.lifecycle_stage,
            Project.planned_start,
            Project.planned_end,
            ProjectFinance.planned_budget,
            ProjectFinance.budget_currency,
            ProjectFinance.customer_id,
            Counterparty.name.label("customer_name"),
            ProjectFinance.address,
            ProjectFinance.latitude,
            ProjectFinance.longitude,
            ProjectFinance.warranty_until,
            live_nodes.label("node_count"),
        )
        .outerjoin(ProjectFinance, ProjectFinance.project_id == Project.id)
        .outerjoin(Counterparty, Counterparty.id == ProjectFinance.customer_id)
    )


def _out(row: Any, *, finance: bool) -> ProjectOut:
    return ProjectOut.model_validate(row, from_attributes=True).model_copy(
        update={"finance_visible": finance}
    )


def _split(values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Поля карточки: свои (project) и коммерческие (project_finance)."""
    finance = {key: value for key, value in values.items() if key in FINANCE_FIELDS}
    core = {key: value for key, value in values.items() if key not in FINANCE_FIELDS}
    return core, finance


async def list_projects(
    session: AsyncSession, *, internal: bool, finance: bool
) -> list[ProjectOut]:
    """Объекты, видимые пользователю: границу доступа ставит RLS, а не условие запроса."""
    rows = await session.execute(
        _project_select(internal=internal).order_by(Project.name, Project.id)
    )
    return [_out(row, finance=finance) for row in rows]


async def get_project(
    session: AsyncSession, project_id: uuid.UUID, *, internal: bool, finance: bool
) -> ProjectOut:
    row = (
        await session.execute(_project_select(internal=internal).where(Project.id == project_id))
    ).one_or_none()
    if row is None:
        raise not_found()
    return _out(row, finance=finance)


async def create_project(
    session: AsyncSession,
    data: ProjectCreate,
    legal_entity_ids: frozenset[uuid.UUID],
    *,
    finance: bool,
) -> ProjectOut:
    """Без COMMIT: вызывается внутри идемпотентной транзакции."""
    if data.legal_entity_id not in legal_entity_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_legal_entity")
    entity = await session.scalar(
        select(LegalEntity.id).where(LegalEntity.id == data.legal_entity_id)
    )
    if entity is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_legal_entity")
    new_id = uuid.uuid4()
    context = require_context()
    # SELECT-политика объекта пускает только известные контексту id: без этого INSERT ... RETURNING
    # не увидел бы собственную строку (ADR-001, apply_context).
    with use_context(replace(context, project_ids=context.project_ids | {new_id})):
        await apply_context(session)
    core, commercial = _split(data.model_dump())
    async with db_rules(session):
        session.add(Project(id=new_id, **core))
        await session.flush()
        if any(value is not None for value in commercial.values()):
            session.add(
                ProjectFinance(
                    project_id=new_id, legal_entity_id=data.legal_entity_id, **commercial
                )
            )
            await session.flush()
    return await get_project(session, new_id, internal=True, finance=finance)


async def update_project(
    session: AsyncSession, project_id: uuid.UUID, patch: ProjectPatch, *, finance: bool
) -> ProjectOut:
    project = (await session.scalars(select(Project).where(Project.id == project_id))).one_or_none()
    if project is None:
        raise not_found()
    core, commercial = _split({field: getattr(patch, field) for field in patch.model_fields_set})
    for field, value in core.items():
        setattr(project, field, value)
    if commercial:
        row = (
            await session.scalars(
                select(ProjectFinance).where(ProjectFinance.project_id == project_id)
            )
        ).one_or_none()
        if row is None:
            row = ProjectFinance(project_id=project_id, legal_entity_id=project.legal_entity_id)
            session.add(row)
        for field, value in commercial.items():
            setattr(row, field, value)
    async with db_rules(session):
        await session.commit()
    return await get_project(session, project_id, internal=True, finance=finance)


async def delete_project(session: AsyncSession, project_id: uuid.UUID, actor: uuid.UUID) -> None:
    async with db_rules(session):
        deleted = await session.scalar(
            update(Project)
            .where(Project.id == project_id, Project.deleted_at.is_(None))
            .values(deleted_at=func.now(), updated_by=actor)
            .returning(Project.id)
            .execution_options(synchronize_session=False)
        )
        if deleted is None:
            raise not_found()
        await session.commit()


# --- узлы дерева ------------------------------------------------------------------------------

_NODE_PUBLIC = (
    ProjectNode.id,
    ProjectNode.project_id,
    ProjectNode.parent_id,
    ProjectNode.type,
    ProjectNode.name,
    ProjectNode.code,
    ProjectNode.sort_order,
    ProjectNode.path,
    ProjectNode.planned_start,
    ProjectNode.planned_end,
    ProjectNode.polygon,
)
_NODE_INTERNAL = (*_NODE_PUBLIC, ProjectNode.foreman_user_id, ProjectNode.master_user_id)


# Дерево отдаётся готовым JSON из БД: на тысячах узлов сборка и проверка объектов Pydantic стоила
# в десятки раз дороже самой выборки. Форма записи та же, что у NodeOut; внешним ролям колонки
# ответственных заменены на NULL (их нет в выданном kut_portal списке).
_TREE_COLUMNS = (
    "id, project_id, parent_id, type, name, code, sort_order, path, planned_start, planned_end,"
    " polygon"
)
_TREE_SQL = {
    True: text(
        "SELECT COALESCE(json_agg(row_to_json(n)), '[]'::json)::text FROM ("  # noqa: S608
        f"SELECT {_TREE_COLUMNS}, foreman_user_id, master_user_id FROM project_node"
        " WHERE project_id = :project AND deleted_at IS NULL"
        " ORDER BY parent_id NULLS FIRST, sort_order, name, id) n"
    ),
    False: text(
        "SELECT COALESCE(json_agg(row_to_json(n)), '[]'::json)::text FROM ("  # noqa: S608
        f"SELECT {_TREE_COLUMNS}, NULL::uuid AS foreman_user_id, NULL::uuid AS master_user_id"
        " FROM project_node WHERE project_id = :project AND deleted_at IS NULL"
        " ORDER BY parent_id NULLS FIRST, sort_order, name, id) n"
    ),
}


async def tree_json(session: AsyncSession, project_id: uuid.UUID, *, internal: bool) -> str:
    """Всё дерево объекта одним запросом: плоский JSON-массив с parent_id и path."""
    document = await session.scalar(_TREE_SQL[internal], {"project": project_id})
    return str(document)


async def get_node(
    session: AsyncSession, project_id: uuid.UUID, node_id: uuid.UUID, *, internal: bool
) -> NodeDetail:
    if not internal:
        row = (
            await session.execute(
                select(*_NODE_PUBLIC).where(
                    ProjectNode.id == node_id, ProjectNode.project_id == project_id
                )
            )
        ).one_or_none()
    else:
        foreman, master = aliased(User), aliased(User)
        row = (
            await session.execute(
                select(
                    *_NODE_INTERNAL,
                    foreman.full_name.label("foreman_name"),
                    master.full_name.label("master_name"),
                )
                .outerjoin(foreman, foreman.id == ProjectNode.foreman_user_id)
                .outerjoin(master, master.id == ProjectNode.master_user_id)
                .where(ProjectNode.id == node_id, ProjectNode.project_id == project_id)
            )
        ).one_or_none()
    if row is None:
        raise not_found()
    return NodeDetail.model_validate(row, from_attributes=True)


async def _node(session: AsyncSession, project_id: uuid.UUID, node_id: uuid.UUID) -> ProjectNode:
    node = (
        await session.scalars(
            select(ProjectNode).where(
                ProjectNode.id == node_id, ProjectNode.project_id == project_id
            )
        )
    ).one_or_none()
    if node is None:
        raise not_found()
    return node


async def _check_responsible(
    session: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID | None, role_code: str
) -> None:
    """Прораб (мастер) узла - активный пользователь этой роли с доступом к этому объекту."""
    if user_id is None:
        return
    found = await session.scalar(
        select(User.id)
        .join(Role, Role.id == User.role_id)
        .join(UserProjectAccess, UserProjectAccess.user_id == User.id)
        .where(
            User.id == user_id,
            User.is_active.is_(True),
            Role.code == role_code,
            UserProjectAccess.project_id == project_id,
            UserProjectAccess.deleted_at.is_(None),
        )
    )
    if found is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{role_code}_invalid")


async def _next_position(
    session: AsyncSession, project_id: uuid.UUID, parent_id: uuid.UUID | None
) -> int:
    last = await session.scalar(
        select(func.max(ProjectNode.sort_order)).where(
            ProjectNode.project_id == project_id,
            ProjectNode.parent_id.is_(None)
            if parent_id is None
            else ProjectNode.parent_id == parent_id,
        )
    )
    return 0 if last is None else int(last) + 1


async def create_node(session: AsyncSession, project_id: uuid.UUID, data: NodeCreate) -> NodeDetail:
    """Без COMMIT: вызывается внутри идемпотентной транзакции."""
    legal_entity_id = await session.scalar(
        select(Project.legal_entity_id).where(Project.id == project_id)
    )
    if legal_entity_id is None:
        raise not_found()
    await _check_responsible(session, project_id, data.foreman_user_id, "foreman")
    await _check_responsible(session, project_id, data.master_user_id, "master")
    fields = data.model_dump(exclude={"sort_order"})
    sort_order = (
        data.sort_order
        if data.sort_order is not None
        else await _next_position(session, project_id, data.parent_id)
    )
    node = ProjectNode(
        project_id=project_id, legal_entity_id=legal_entity_id, sort_order=sort_order, **fields
    )
    async with db_rules(session):
        session.add(node)
        await session.flush()
    return await get_node(session, project_id, node.id, internal=True)


async def update_node(
    session: AsyncSession, project_id: uuid.UUID, node_id: uuid.UUID, patch: NodePatch
) -> NodeDetail:
    node = await _node(session, project_id, node_id)
    if "foreman_user_id" in patch.model_fields_set:
        await _check_responsible(session, project_id, patch.foreman_user_id, "foreman")
    if "master_user_id" in patch.model_fields_set:
        await _check_responsible(session, project_id, patch.master_user_id, "master")
    for field in patch.model_fields_set:
        setattr(node, field, getattr(patch, field))
    async with db_rules(session):
        await session.commit()
    return await get_node(session, project_id, node_id, internal=True)


async def delete_node(
    session: AsyncSession, project_id: uuid.UUID, node_id: uuid.UUID, actor: uuid.UUID
) -> None:
    async with db_rules(session):
        deleted = await session.scalar(
            update(ProjectNode)
            .where(
                ProjectNode.id == node_id,
                ProjectNode.project_id == project_id,
                ProjectNode.deleted_at.is_(None),
            )
            .values(deleted_at=func.now(), updated_by=actor)
            .returning(ProjectNode.id)
            .execution_options(synchronize_session=False)
        )
        if deleted is None:
            raise not_found()
        await session.commit()


_RESEQUENCE = text(
    "UPDATE project_node SET sort_order = v.position, updated_at = now(),"
    " updated_by = (SELECT app.user_id())"
    " FROM unnest(CAST(:ids AS uuid[]), CAST(:positions AS int[])) AS v(id, position)"
    " WHERE project_node.id = v.id AND project_node.sort_order <> v.position"
)


async def move_node(
    session: AsyncSession,
    project_id: uuid.UUID,
    node_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    index: int | None,
) -> NodeDetail:
    """Перенос под другого родителя и/или на другую позицию среди его детей.

    Допустимость (тип, цикл, тот же объект) проверяет триггер БД; путь поддерева он же и
    пересчитывает. Порядок братьев переписывается подряд (0, 1, 2, ...).
    """
    node = await _node(session, project_id, node_id)
    async with db_rules(session):
        if node.parent_id != parent_id:
            node.parent_id = parent_id
            await session.flush()
        siblings = list(
            await session.scalars(
                select(ProjectNode.id)
                .where(
                    ProjectNode.project_id == project_id,
                    ProjectNode.parent_id.is_(None)
                    if parent_id is None
                    else ProjectNode.parent_id == parent_id,
                    ProjectNode.id != node_id,
                )
                .order_by(ProjectNode.sort_order, ProjectNode.created_at, ProjectNode.id)
            )
        )
        position = len(siblings) if index is None else min(index, len(siblings))
        ordered = [*siblings[:position], node_id, *siblings[position:]]
        await session.execute(_RESEQUENCE, {"ids": ordered, "positions": list(range(len(ordered)))})
        await session.commit()
    return await get_node(session, project_id, node_id, internal=True)
