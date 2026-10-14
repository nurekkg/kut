"""equipment: номер регистрации единицы для листов, которые видит пользователь (экраны SE-2)

Revision ID: a9d2e6c1f4b8
Revises: f0c4d8a1e7b3
Create Date: 2026-10-13 19:00:00.000000

- app.equipment_unit_labels(p_units) - номер регистрации по id единиц. Нужен подтверждающему, которому
  реестр не виден (делегат-мастер видит листы объекта, а единицы - нет). Функция выполняется от владельца
  (обходит RLS реестра), поэтому номера отдаёт только по id, которые вызывающий уже получил из видимых ему
  листов: id - UUID, перебрать их нельзя. EXECUTE у kut_app.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a9d2e6c1f4b8"
down_revision: str | Sequence[str] | None = "f0c4d8a1e7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIT_LABELS = """
CREATE FUNCTION app.equipment_unit_labels(p_units uuid[])
RETURNS TABLE(o_id uuid, o_registry text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT u.id, u.registry_number::text
    FROM public.equipment_unit u
    WHERE u.id = ANY(p_units) AND u.deleted_at IS NULL
$$
"""


def upgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute(UNIT_LABELS)
    op.execute("GRANT EXECUTE ON FUNCTION app.equipment_unit_labels(uuid[]) TO kut_app")
    op.execute("RESET ROLE")


def downgrade() -> None:
    op.execute("SET LOCAL ROLE kut_auth")
    op.execute("DROP FUNCTION IF EXISTS app.equipment_unit_labels(uuid[])")
    op.execute("RESET ROLE")
