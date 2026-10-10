import math
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.auth.principal import Principal
from app.modules.dashboards.schemas import (
    Budget,
    Counts,
    FactPoint,
    FactsSummary,
    GroupDashboard,
    GroupRow,
    NodeTile,
    ObjectDashboard,
    PortalDashboard,
    PortalProgress,
    ProblemWork,
    Progress,
    SeriesPoint,
    Thresholds,
)
from app.modules.photos import service as photos
from app.modules.rbac.enums import Action, Module
from app.modules.readiness import service as readiness
from app.modules.readiness.schemas import Aggregate, NodeReadiness, ProjectReadiness

HUNDRED = Decimal(100)
TWO = Decimal("0.01")
ONE = Decimal("0.1")

# Проценты, отставание и проблемные работы берутся из функций S3a (app.project_readiness,
# app.schedule_progress): дашборд не считает готовность заново. Ряд по неделям - app.progress_series
# (миграция c8f1e2a7b934): та же формула на набор дат, факт по work_date. Все функции работают от
# имени вызывающего, границу доступа ставит RLS.
_TODAY = text("SELECT app.local_today()")
_PROJECT = text("SELECT name, status, lifecycle_stage FROM project WHERE id = :project")
_PORTAL_PROJECT = text("SELECT name, status FROM project WHERE id = :project")
_PLAN_RANGE = text(
    "SELECT min(plan_start) AS first, max(plan_end) AS last FROM schedule_item"
    " WHERE project_id = :project AND deleted_at IS NULL"
)
_SERIES = text(
    "SELECT point_date, planned_percent, fact_percent"
    " FROM app.progress_series(:project, CAST(:points AS date[]))"
)
_COUNTS = text(
    "SELECT count(*) AS works,"
    " count(*) FILTER (WHERE pr.state IN ('late_start', 'overdue_no_fact')) AS no_fact_started,"
    " count(*) FILTER (WHERE pr.state IN ('overdue', 'overdue_no_fact')"
    " OR pr.delay_days >= :lag) AS problems"
    " FROM app.schedule_progress(:project, :as_of, NULL) pr"
)
_PROBLEM_COUNT = text(
    "SELECT count(*) FROM app.schedule_progress(:project, :as_of, NULL) pr"
    " WHERE pr.state IN ('overdue', 'overdue_no_fact') OR pr.delay_days >= :lag"
)
_PROBLEMS = text(
    "SELECT pr.item_id, pr.node_id, n.name AS node_name, pr.work_id, w.name AS work_name, w.unit,"
    " pr.plan_start, pr.plan_end, pr.plan_volume, pr.confirmed_volume, pr.fraction, pr.delay_days,"
    " pr.overdue_days, pr.state"
    " FROM app.schedule_progress(:project, :as_of, NULL) pr"
    " JOIN project_node n ON n.id = pr.node_id"
    " LEFT JOIN work w ON w.id = pr.work_id"
    " WHERE pr.state IN ('overdue', 'overdue_no_fact') OR pr.delay_days >= :lag"
    " ORDER BY pr.delay_days DESC, pr.overdue_days DESC, pr.item_id LIMIT :top"
)
_NODE_NAMES = text(
    "SELECT id, name, type FROM project_node WHERE project_id = :project AND deleted_at IS NULL"
)
_FACTS = text(
    "SELECT count(*) FILTER (WHERE a.status = 'submitted') AS awaiting,"
    " count(*) FILTER (WHERE a.status = 'submitted'"
    " AND a.updated_at < now() - make_interval(days => :stale)) AS stale,"
    " count(*) FILTER (WHERE a.status <> 'draft' AND a.work_date > :since) AS recent"
    " FROM work_actual a WHERE a.project_id = :project AND a.deleted_at IS NULL"
)
_BUDGET = text(
    "SELECT planned_budget, budget_currency FROM project_finance WHERE project_id = :project"
)
_GROUP_PROJECTS = text(
    "SELECT id, name, status, lifecycle_stage FROM project WHERE deleted_at IS NULL ORDER BY name"
)
_GROUP_BUDGETS = text("SELECT project_id, planned_budget, budget_currency FROM project_finance")
_GROUP_AWAITING = text(
    "SELECT project_id, count(*) AS n FROM work_actual"
    " WHERE status = 'submitted' AND deleted_at IS NULL GROUP BY project_id"
)


def _q(value: Decimal, places: Decimal) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)


def _progress(agg: Aggregate | None) -> Progress | None:
    if agg is None:
        return None
    return Progress(
        percent=agg.percent,
        planned_percent=agg.planned_percent,
        deviation=agg.deviation,
        delay_days=agg.delay_days,
    )


def _budget(row: Any) -> Budget | None:
    if row is None or row.planned_budget is None:
        return None
    return Budget(planned_budget=row.planned_budget, currency=row.budget_currency)


def _thresholds() -> Thresholds:
    settings = get_settings()
    return Thresholds(
        lag_days=settings.dashboard_problem_lag_days,
        approval_stale_days=settings.dashboard_approval_stale_days,
        recent_days=settings.dashboard_recent_days,
        top_n=settings.dashboard_top_n,
    )


def series_points(today: date, first: date, last: date, weeks: int) -> list[date]:
    """Недельная сетка от сегодняшнего дня: даты через семь дней в окне weeks недель в обе стороны и
    в пределах плана. Сегодняшняя дата - всегда точка ряда (последний факт)."""
    points = {today}
    low = max(first, today - timedelta(weeks=weeks))
    high = min(last, today + timedelta(weeks=weeks))
    if low <= high:
        first_step = math.ceil((low - today).days / 7)
        last_step = math.floor((high - today).days / 7)
        points.update(today + timedelta(weeks=step) for step in range(first_step, last_step + 1))
    return sorted(points)


async def _series(
    session: AsyncSession, project_id: uuid.UUID, points: Sequence[date], today: date
) -> list[SeriesPoint]:
    rows = (await session.execute(_SERIES, {"project": project_id, "points": list(points)})).all()
    return [
        SeriesPoint(
            date=row.point_date,
            plan_percent=_q(row.planned_percent, TWO),
            fact_percent=None if row.point_date > today else _q(row.fact_percent, TWO),
        )
        for row in rows
    ]


def _tile(node: NodeReadiness, names: dict[uuid.UUID, Any]) -> NodeTile | None:
    info = names.get(node.node_id)
    if info is None:
        return None
    return NodeTile(
        node_id=node.node_id,
        name=info.name,
        type=info.type,
        progress=_progress(node.subtree),
        items=node.subtree.items,
        overdue=node.subtree.overdue,
    )


async def object_dashboard(
    session: AsyncSession, principal: Principal, project_id: uuid.UUID
) -> ObjectDashboard:
    settings = get_settings()
    lag = settings.dashboard_problem_lag_days
    today: date = await session.scalar(_TODAY)
    project = (await session.execute(_PROJECT, {"project": project_id})).one()
    span = (await session.execute(_PLAN_RANGE, {"project": project_id})).one()
    ready: ProjectReadiness = await readiness.project_readiness(session, project_id, today)
    total = ready.project
    names = {
        row.id: row for row in (await session.execute(_NODE_NAMES, {"project": project_id})).all()
    }

    counts = None
    problems: list[ProblemWork] = []
    if total is not None:
        head = (
            await session.execute(_COUNTS, {"project": project_id, "as_of": today, "lag": lag})
        ).one()
        counts = Counts(
            works=head.works,
            completed=total.completed,
            overdue=total.overdue,
            no_fact_started=head.no_fact_started,
            problems=head.problems,
        )
        rows = (
            await session.execute(
                _PROBLEMS,
                {
                    "project": project_id,
                    "as_of": today,
                    "lag": lag,
                    "top": settings.dashboard_top_n,
                },
            )
        ).all()
        problems = [
            ProblemWork(
                item_id=row.item_id,
                node_id=row.node_id,
                node_name=row.node_name,
                work_id=row.work_id,
                work_name=row.work_name,
                unit=row.unit,
                plan_start=row.plan_start,
                plan_end=row.plan_end,
                plan_volume=row.plan_volume,
                confirmed_volume=row.confirmed_volume,
                percent=_q(row.fraction * HUNDRED, TWO),
                delay_days=_q(row.delay_days, ONE),
                overdue_days=row.overdue_days,
                state=row.state,
            )
            for row in rows
        ]

    nodes = [
        tile
        for tile in (_tile(node, names) for node in ready.nodes if node.parent_id is None)
        if tile is not None
    ]
    lagging = sorted(
        (node for node in ready.nodes if node.subtree.delay_days >= lag),
        key=lambda node: node.subtree.delay_days,
        reverse=True,
    )[: settings.dashboard_top_n]
    zones = [tile for tile in (_tile(node, names) for node in lagging) if tile is not None]

    facts = None
    photo_items = None
    if principal.has(Module.WORK_ACTUAL, Action.READ):
        fact_row = (
            await session.execute(
                _FACTS,
                {
                    "project": project_id,
                    "stale": settings.dashboard_approval_stale_days,
                    "since": today - timedelta(days=settings.dashboard_recent_days),
                },
            )
        ).one()
        facts = FactsSummary(
            awaiting_approval=fact_row.awaiting,
            awaiting_approval_stale=fact_row.stale,
            recent=fact_row.recent,
        )
        photo_items = await photos.list_photos(
            session,
            project_id,
            actual_ids=None,
            newest_first=True,
            limit=settings.dashboard_photos_limit,
            offset=0,
            with_urls=False,
        )

    budget = None
    if principal.has(Module.FINANCE, Action.READ):
        budget = _budget((await session.execute(_BUDGET, {"project": project_id})).one_or_none())

    series: list[SeriesPoint] = []
    if span.first is not None:
        points = series_points(today, span.first, span.last, settings.dashboard_series_window_weeks)
        series = await _series(session, project_id, points, today)

    return ObjectDashboard(
        as_of=today,
        project_id=project_id,
        name=project.name,
        status=project.status,
        lifecycle_stage=project.lifecycle_stage,
        planned_start=span.first,
        planned_end=span.last,
        thresholds=_thresholds(),
        progress=_progress(total),
        counts=counts,
        facts=facts,
        series=series,
        nodes=nodes,
        zones=zones,
        problems=problems,
        photos=photo_items,
        budget=budget,
    )


async def group_dashboard(session: AsyncSession) -> GroupDashboard:
    lag = get_settings().dashboard_problem_lag_days
    today: date = await session.scalar(_TODAY)
    budgets = {row.project_id: row for row in (await session.execute(_GROUP_BUDGETS)).all()}
    awaiting = {row.project_id: row.n for row in (await session.execute(_GROUP_AWAITING)).all()}
    rows: list[GroupRow] = []
    for project in (await session.execute(_GROUP_PROJECTS)).all():
        ready = await readiness.project_readiness(session, project.id, today)
        problems = await session.scalar(
            _PROBLEM_COUNT, {"project": project.id, "as_of": today, "lag": lag}
        )
        rows.append(
            GroupRow(
                project_id=project.id,
                name=project.name,
                status=project.status,
                lifecycle_stage=project.lifecycle_stage,
                progress=_progress(ready.project),
                items=ready.project.items if ready.project is not None else 0,
                problems=problems,
                awaiting_approval=awaiting.get(project.id, 0),
                budget=_budget(budgets.get(project.id)),
            )
        )
    return GroupDashboard(as_of=today, thresholds=_thresholds(), rows=rows)


def portal_points(today: date, weeks: int) -> list[date]:
    """Точки ряда факта для заказчика: сегодня и недельная сетка назад на weeks недель. Сетка не
    зависит от плана: сроки и окно плана в ответе не видны даже косвенно."""
    return sorted({today - timedelta(weeks=step) for step in range(weeks + 1)})


async def _fact_series(
    session: AsyncSession, project_id: uuid.UUID, points: Sequence[date]
) -> list[FactPoint]:
    rows = (await session.execute(_SERIES, {"project": project_id, "points": list(points)})).all()
    return [FactPoint(date=row.point_date, fact_percent=_q(row.fact_percent, TWO)) for row in rows]


async def portal_dashboard(session: AsyncSession, project_id: uuid.UUID) -> PortalDashboard:
    settings = get_settings()
    today: date = await session.scalar(_TODAY)
    project = (await session.execute(_PORTAL_PROJECT, {"project": project_id})).one()
    total = (await readiness.project_readiness(session, project_id, today)).project
    progress = None if total is None else PortalProgress(percent=total.percent)
    series = await _fact_series(
        session, project_id, portal_points(today, settings.dashboard_series_window_weeks)
    )
    return PortalDashboard(
        as_of=today,
        project_id=project_id,
        name=project.name,
        status=project.status,
        progress=progress,
        series=series,
    )
