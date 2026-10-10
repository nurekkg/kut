"""dashboards: ряд план/факт по датам для недельного графика (S4, ТЗ §5.10-§5.11)

Revision ID: c8f1e2a7b934
Revises: a1efb2b3d84b
Create Date: 2026-10-10 12:00:00.000000

Функция app.progress_series(объект, даты) считает тот же процент, что app.project_readiness (S3a):
работа = min(подтверждённый факт / плановый объём, 1), вес - app.schedule_item_weight, план - линейный
по дням. Отличие одно: факт берётся на каждую дату ряда по work_date <= дата (факт за прошлую неделю
попадает в ряд этой недели, а не последующих). Для сегодняшней даты ряд совпадает с S3a (проверяется
тестом), функции S3a не меняются.

Расчёт одним проходом: строки ГПР x даты (план), события факта (объём по первой дате ряда не раньше
work_date) и разности долей по событиям, накопленные по датам оконной функцией. Стоимость растёт с
числом строк ГПР и дат, а не с числом вызовов. Функция работает от имени вызывающего (SECURITY
INVOKER): границу доступа ставит RLS; внешняя роль получает только подтверждённый факт.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c8f1e2a7b934"
down_revision: str | Sequence[str] | None = "a1efb2b3d84b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOGINS = "kut_app, kut_portal"

PROGRESS_SERIES = """
CREATE FUNCTION app.progress_series(p_project uuid, p_points date[])
RETURNS TABLE (point_date date, planned_percent numeric, fact_percent numeric)
LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
    WITH pts AS MATERIALIZED (
        SELECT d AS point_date, row_number() OVER (ORDER BY d) AS k
        FROM (SELECT DISTINCT unnest(p_points) AS d) u
    ),
    items AS MATERIALIZED (
        SELECT item.id AS item_id, item.plan_start AS s, item.plan_end AS e,
               item.plan_volume AS pv,
               app.schedule_item_weight(item.id, item.weight, item.plan_start, item.plan_end) AS w
        FROM public.schedule_item item
        JOIN public.project_node n ON n.id = item.node_id
        WHERE item.project_id = p_project AND item.deleted_at IS NULL
    ),
    total AS (SELECT sum(w) AS w_sum FROM items),
    planned AS (
        SELECT pts.k,
               sum(i.w * (LEAST(GREATEST(pts.point_date - i.s + 1, 0), i.e - i.s + 1)::numeric
                          / (i.e - i.s + 1))) AS plan_w
        FROM pts CROSS JOIN items i
        GROUP BY pts.k
    ),
    events AS MATERIALIZED (
        SELECT a.schedule_item_id AS item_id, first_point.k, sum(a.volume) AS vol
        FROM public.work_actual a
        CROSS JOIN LATERAL (
            SELECT pts.k FROM pts WHERE pts.point_date >= a.work_date ORDER BY pts.k LIMIT 1
        ) first_point
        WHERE a.project_id = p_project AND a.status = 'approved'
          AND a.deleted_at IS NULL AND a.schedule_item_id IS NOT NULL
        GROUP BY a.schedule_item_id, first_point.k
    ),
    steps AS (
        SELECT ev.item_id, ev.k, ev.vol,
               sum(ev.vol) OVER (PARTITION BY ev.item_id ORDER BY ev.k) AS cum
        FROM events ev
    ),
    delta AS (
        SELECT st.k,
               sum(i.w * (LEAST(st.cum / i.pv, 1) - LEAST((st.cum - st.vol) / i.pv, 1))) AS d
        FROM steps st
        JOIN items i ON i.item_id = st.item_id
        GROUP BY st.k
    ),
    running AS (
        SELECT pts.k, sum(COALESCE(delta.d, 0)) OVER (ORDER BY pts.k ROWS UNBOUNDED PRECEDING)
               AS fact_w
        FROM pts LEFT JOIN delta ON delta.k = pts.k
    )
    SELECT pts.point_date,
           round(100 * COALESCE(planned.plan_w, 0) / NULLIF(total.w_sum, 0), 4),
           round(100 * running.fact_w / NULLIF(total.w_sum, 0), 4)
    FROM pts
    CROSS JOIN total
    LEFT JOIN planned ON planned.k = pts.k
    LEFT JOIN running ON running.k = pts.k
    ORDER BY pts.point_date
$$
"""

SIGNATURE = "app.progress_series(uuid, date[])"


def upgrade() -> None:
    op.execute(PROGRESS_SERIES)
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO {LOGINS}")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {SIGNATURE}")
