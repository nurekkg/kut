"""Правила, которые проверяет БД, в виде понятных ответов API.

Триггеры дерева объекта поднимают check_violation с машинным кодом в тексте ('node.cycle'), а
индексы и ограничения называются в журнале ошибок: здесь они превращаются в код ответа и статус.
Сервис обращается сюда при flush/commit, чтобы клиент получил не 500, а причину.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

# Код из RAISE EXCEPTION триггеров -> (статус, код ответа).
TRIGGER_RULES: dict[str, tuple[int, str]] = {
    "node.type_not_allowed": (422, "node_type_not_allowed"),
    "node.cycle": (422, "node_cycle"),
    "node.parent_invalid": (422, "node_parent_invalid"),
    "node.has_dependents": (409, "node_has_dependents"),
    "project.has_dependents": (409, "project_has_nodes"),
    "project.customer_invalid": (422, "customer_invalid"),
}

# Имя нарушенного ограничения или индекса -> (статус, код ответа).
CONSTRAINT_RULES: dict[str, tuple[int, str]] = {
    "uq_project_node_root_code": (409, "node_code_taken"),
    "uq_project_node_sibling_code": (409, "node_code_taken"),
    "uq_work_code": (409, "work_code_taken"),
    "ck_project_node_polygon_valid": (422, "polygon_invalid"),
    "ck_project_node_planned_dates_ordered": (422, "dates_invalid"),
    "ck_project_planned_dates_ordered": (422, "dates_invalid"),
    "ck_project_coordinates_valid": (422, "coordinates_invalid"),
    "ck_project_budget_not_negative": (422, "budget_invalid"),
}


def rule_error(error: DBAPIError) -> HTTPException | None:
    """HTTP-ошибка по нарушенному правилу БД или None, если это не известное правило."""
    diag = getattr(error.orig, "diag", None)
    if diag is None:
        return None
    message = getattr(diag, "message_primary", None) or ""
    if message in TRIGGER_RULES:
        status, code = TRIGGER_RULES[message]
        return HTTPException(status, code)
    constraint = getattr(diag, "constraint_name", None) or ""
    if constraint in CONSTRAINT_RULES:
        status, code = CONSTRAINT_RULES[constraint]
        return HTTPException(status, code)
    return None


@asynccontextmanager
async def db_rules(session: AsyncSession) -> AsyncIterator[None]:
    """Нарушение известного правила БД внутри блока -> откат и HTTPException с кодом причины."""
    try:
        yield
    except DBAPIError as error:
        translated = rule_error(error)
        if translated is None:
            raise
        await session.rollback()
        raise translated from error
