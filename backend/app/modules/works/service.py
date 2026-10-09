import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rules import db_rules
from app.modules.works.models import Work
from app.modules.works.schemas import WorkCreate, WorkOut, WorkPatch

MAX_LIST = 500


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _out(work: Work) -> WorkOut:
    return WorkOut(
        id=work.id, code=work.code, name=work.name, unit=work.unit, category=work.category
    )


def not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")


async def list_works(
    session: AsyncSession, *, query: str | None, category: str | None, limit: int, offset: int
) -> list[WorkOut]:
    statement = select(Work).order_by(Work.category, Work.code, Work.id)
    if category:
        statement = statement.where(Work.category == category)
    if query:
        pattern = _like(query)
        statement = statement.where(
            or_(Work.code.ilike(pattern, escape="\\"), Work.name.ilike(pattern, escape="\\"))
        )
    rows = await session.scalars(statement.limit(min(limit, MAX_LIST)).offset(offset))
    return [_out(row) for row in rows]


async def list_categories(session: AsyncSession) -> list[str]:
    rows = await session.scalars(select(Work.category).distinct().order_by(Work.category))
    return list(rows)


async def create_work(session: AsyncSession, data: WorkCreate) -> WorkOut:
    """Без COMMIT: вызывается внутри идемпотентной транзакции."""
    work = Work(**data.model_dump())
    async with db_rules(session):
        session.add(work)
        await session.flush()
    return _out(work)


async def _work(session: AsyncSession, work_id: uuid.UUID) -> Work:
    work = (await session.scalars(select(Work).where(Work.id == work_id))).one_or_none()
    if work is None:
        raise not_found()
    return work


async def get_work(session: AsyncSession, work_id: uuid.UUID) -> WorkOut:
    return _out(await _work(session, work_id))


async def update_work(session: AsyncSession, work_id: uuid.UUID, patch: WorkPatch) -> WorkOut:
    work = await _work(session, work_id)
    for field in patch.model_fields_set:
        setattr(work, field, getattr(patch, field))
    async with db_rules(session):
        await session.commit()
    return _out(work)


async def delete_work(session: AsyncSession, work_id: uuid.UUID, actor: uuid.UUID) -> None:
    """Вид работ со строками ГПР или фактом БД не отпускает (409 work_has_dependents)."""
    async with db_rules(session):
        deleted = await session.scalar(
            update(Work)
            .where(Work.id == work_id, Work.deleted_at.is_(None))
            .values(deleted_at=func.now(), updated_by=actor)
            .returning(Work.id)
            .execution_options(synchronize_session=False)
        )
        if deleted is None:
            raise not_found()
        await session.commit()
