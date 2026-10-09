"""project_finance: write only for group roles with objects:write (PO decision, S3a OPEN no. 4)

Revision ID: a3f7c1d9e582
Revises: f8c2a5e61d47
Create Date: 2026-10-09 11:00:00.000000

Решение PO по OPEN S3a №4: коммерческую часть карточки объекта (бюджет, заказчик, адрес,
координаты, гарантия) пишет только роль с областью «вся группа» (scope=group) и правом
objects:write, то есть владелец и технический директор. До этой ревизии граница стояла только в API,
а политики INSERT и UPDATE таблицы project_finance требовали пару finance:read + objects:write:
прораб имеет и то и другое на свой объект и мог бы записать бюджет прямым SQL в обход API.

Чтение не меняется. Миграции до f8c2a5e61d47 включительно заморожены: правка - новой ревизией.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a3f7c1d9e582"
down_revision: str | Sequence[str] | None = "f8c2a5e61d47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WRITERS = "kut_app, kut_worker"
INTERNAL = "(SELECT app.is_internal())"
PROJECTS = "((SELECT app.project_ids())::uuid[])"
# InitPlan: функции вычисляются один раз на запрос, а не на каждую строку.
CAN_SEE_FINANCE = "(SELECT app.has_permission('finance', 'read'))"
CAN_EDIT_CARD = "(SELECT app.has_permission('objects', 'write'))"
IS_GROUP_ROLE = "(SELECT app.is_group_role())"

VISIBLE = f"{INTERNAL} AND project_id = ANY {PROJECTS} AND {CAN_SEE_FINANCE}"
EDITABLE_BEFORE = f"{VISIBLE} AND {CAN_EDIT_CARD}"
EDITABLE_AFTER = f"{EDITABLE_BEFORE} AND {IS_GROUP_ROLE}"

# Область роли пользователя запроса (как app.has_permission): пользователь берётся из контекста
# запроса, а не из параметра. Без пользователя (системный контекст) ответ «нет»: системные записи
# идут под kut_worker (BYPASSRLS) и политики не проверяют.
IS_GROUP_ROLE_FUNCTION = """
CREATE FUNCTION app.is_group_role() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public."user" usr
        JOIN public.role r ON r.id = usr.role_id AND r.deleted_at IS NULL
        WHERE usr.id = app.user_id() AND usr.is_active AND usr.deleted_at IS NULL
          AND r.scope = 'group')
$$
"""


def _set_write_policies(editable: str) -> None:
    op.execute(
        f"ALTER POLICY project_finance_insert_app ON project_finance WITH CHECK ({editable})"
    )
    op.execute(
        f"ALTER POLICY project_finance_update_app ON project_finance"
        f" USING ({editable}) WITH CHECK ({editable})"
    )


def upgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(IS_GROUP_ROLE_FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION app.is_group_role() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app.is_group_role() TO {WRITERS}")
    op.execute("RESET ROLE")
    _set_write_policies(EDITABLE_AFTER)


def downgrade() -> None:
    _set_write_policies(EDITABLE_BEFORE)
    op.execute("DROP FUNCTION app.is_group_role()")
