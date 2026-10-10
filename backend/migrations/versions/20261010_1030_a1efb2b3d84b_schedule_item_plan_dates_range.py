"""schedule_item: plan dates in 2000-2100 and at most 10 years long (PO decision, S3b OPEN)

Revision ID: a1efb2b3d84b
Revises: a3f7c1d9e582
Create Date: 2026-10-10 10:30:17.281583

Решение PO по OPEN S3b: сервер принимал любые даты ГПР (до 9999-12-31), и одна такая строка подвешивала
диаграмму Ганта у всех, кто её откроет. Теперь границы держит БД: plan_start и plan_end лежат в
[2000-01-01, 2100-12-31], а plan_end не позже plan_start + 10 лет (порядок дат проверяет прежнее
ограничение dates_ordered). Нарушение приходит в API кодом schedule_dates_out_of_range (422).

Строки с нарушением миграция не правит и не удаляет (данные не теряем и не подменяем молча): перед
добавлением ограничений она считает такие строки, включая помеченные удалёнными, и останавливается с
перечнем. Миграции до f8c2a5e61d47 включительно заморожены: правка - новой ревизией.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1efb2b3d84b"
down_revision: str | Sequence[str] | None = "a3f7c1d9e582"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IN_RANGE = "plan_start >= DATE '2000-01-01' AND plan_end <= DATE '2100-12-31'"
# date + interval неизменяем (immutable), поэтому годится для CHECK; календарные 10 лет, а не 3650 суток.
SHORT_ENOUGH = "plan_end <= (plan_start + INTERVAL '10 years')::date"


def _refuse_if_rows_violate() -> None:
    connection = op.get_bind()
    # Под kut_owner FORCE RLS показал бы 0 строк и проверка пропустила бы всё; kut_auth обходит RLS.
    op.execute("SET LOCAL ROLE kut_auth")
    try:
        total = connection.execute(
            sa.text(
                "SELECT count(*) FROM public.schedule_item "
                f"WHERE NOT ({IN_RANGE}) OR NOT ({SHORT_ENOUGH})"
            )
        ).scalar_one()
        sample = connection.execute(
            sa.text(
                "SELECT id, plan_start, plan_end, deleted_at IS NOT NULL AS deleted "
                "FROM public.schedule_item "
                f"WHERE NOT ({IN_RANGE}) OR NOT ({SHORT_ENOUGH}) ORDER BY created_at LIMIT 10"
            )
        ).all()
    finally:
        op.execute("RESET ROLE")
    if total:
        listing = "; ".join(
            f"{row.id} {row.plan_start}..{row.plan_end}{' (удалена)' if row.deleted else ''}"
            for row in sample
        )
        raise RuntimeError(
            f"schedule_item: {total} строк(и) вне границ дат ГПР (2000-01-01..2100-12-31, не длиннее "
            f"10 лет); миграция их не меняет. Первые {len(sample)}: {listing}. Исправьте даты или "
            "уберите строки и повторите."
        )


def upgrade() -> None:
    _refuse_if_rows_violate()
    op.create_check_constraint(op.f("ck_schedule_item_dates_in_range"), "schedule_item", IN_RANGE)
    op.create_check_constraint(op.f("ck_schedule_item_duration_max"), "schedule_item", SHORT_ENOUGH)


def downgrade() -> None:
    op.drop_constraint(op.f("ck_schedule_item_duration_max"), "schedule_item", type_="check")
    op.drop_constraint(op.f("ck_schedule_item_dates_in_range"), "schedule_item", type_="check")
