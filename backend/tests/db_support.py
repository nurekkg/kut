"""Общие помощники тестов уровня БД: вызов SQL под контекстом запроса и чтение журнала."""

import hashlib
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.context import RequestContext, system_context, use_context
from app.core.db import get_portal_sessionmaker, get_sessionmaker

INSUFFICIENT_PRIVILEGE = "42501"
NO_DATA_FOUND = "P0002"


def context(
    user_id: UUID | None,
    *,
    internal: bool = True,
    projects: set[UUID] | None = None,
    all_users: bool = False,
    counterparty: UUID | None = None,
    request_id: UUID | None = None,
) -> RequestContext:
    return RequestContext(
        user_id=user_id,
        is_internal=internal,
        project_ids=frozenset(projects or ()),
        counterparty_id=counterparty,
        sees_all_users=all_users,
        request_id=request_id,
    )


async def call(
    who: RequestContext | None, sql: str, *, portal: bool = False, **params: Any
) -> list[Any]:
    """Выполняет SQL логином kut_app (или kut_portal) под контекстом who и фиксирует транзакцию."""
    maker = get_portal_sessionmaker() if portal else get_sessionmaker()
    with use_context(who) if who is not None else system_context():
        async with maker() as session:
            result = await session.execute(text(sql), params)
            rows = result.all() if result.returns_rows else []
            await session.commit()
            return rows


async def expect_error(who: RequestContext | None, sql: str, sqlstate: str, **params: Any) -> None:
    with pytest.raises(DBAPIError) as info:
        await call(who, sql, **params)
    assert getattr(info.value.orig, "sqlstate", None) == sqlstate, info.value


async def owner_rows(engine: AsyncEngine, sql: str, **params: Any) -> list[Any]:
    """Чтение произвольным движком (kut_owner или kut_worker): служебные запросы тестов."""
    async with engine.connect() as connection:
        return list((await connection.execute(text(sql), params)).all())


def fresh_hash() -> bytes:
    return hashlib.sha256(uuid4().bytes).digest()


async def issue_session(user_id: UUID, *, ttl: str = "14 days") -> tuple[UUID, bytes]:
    family, token = uuid4(), fresh_hash()
    await call(
        None,
        "SELECT app.refresh_issue(:u, :f, :h, CAST(:ttl AS interval), CAST(:amr AS text[]))",
        u=user_id, f=family, h=token, ttl=ttl, amr=["pwd"],
    )  # fmt: skip
    return family, token


async def events(engine: AsyncEngine, action: str, row_id: UUID | None = None) -> list[Any]:
    sql = "SELECT * FROM audit_log WHERE kind = 'event' AND action = :action"
    params: dict[str, Any] = {"action": action}
    if row_id is not None:
        sql += " AND row_id = :row_id"
        params["row_id"] = row_id
    return await owner_rows(engine, sql + " ORDER BY id", **params)
