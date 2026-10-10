import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.modules.photos.schemas import PhotoOut


class Thresholds(BaseModel):
    """Пороги дашбордов: настройки сервера, а не литералы в расчёте."""

    lag_days: int
    approval_stale_days: int
    recent_days: int
    top_n: int


class Progress(BaseModel):
    """Готовность по весам (как в ГПР). deviation = percent - planned_percent, минус - отставание;
    delay_days - средневзвешенное отставание в днях, плюс - отстаём."""

    percent: Decimal
    planned_percent: Decimal
    deviation: Decimal
    delay_days: Decimal


class SeriesPoint(BaseModel):
    """Точка ряда по дате: план - линейный по срокам, факт - подтверждённый на эту дату.
    После сегодняшней даты факта нет (null)."""

    date: date
    plan_percent: Decimal
    fact_percent: Decimal | None


class NodeTile(BaseModel):
    node_id: uuid.UUID
    name: str
    type: str
    progress: Progress
    items: int
    overdue: int


class ProblemWork(BaseModel):
    item_id: uuid.UUID
    node_id: uuid.UUID
    node_name: str
    work_id: uuid.UUID
    work_name: str | None
    unit: str | None
    plan_start: date
    plan_end: date
    plan_volume: Decimal
    confirmed_volume: Decimal
    percent: Decimal
    delay_days: Decimal
    overdue_days: int
    state: str


class Counts(BaseModel):
    works: int
    completed: int
    overdue: int
    no_fact_started: int
    problems: int


class FactsSummary(BaseModel):
    awaiting_approval: int
    awaiting_approval_stale: int
    recent: int


class Budget(BaseModel):
    planned_budget: Decimal
    currency: str


class ObjectDashboard(BaseModel):
    as_of: date
    project_id: uuid.UUID
    name: str
    status: str
    lifecycle_stage: str
    planned_start: date | None
    planned_end: date | None
    thresholds: Thresholds
    progress: Progress | None
    counts: Counts | None
    facts: FactsSummary | None
    series: list[SeriesPoint]
    nodes: list[NodeTile]
    zones: list[NodeTile]
    problems: list[ProblemWork]
    photos: list[PhotoOut] | None
    budget: Budget | None


class GroupRow(BaseModel):
    project_id: uuid.UUID
    name: str
    status: str
    lifecycle_stage: str
    progress: Progress | None
    items: int
    problems: int
    awaiting_approval: int
    budget: Budget | None


class GroupDashboard(BaseModel):
    as_of: date
    thresholds: Thresholds
    rows: list[GroupRow]


class PortalProgress(BaseModel):
    percent: Decimal
    planned_percent: Decimal
    deviation: Decimal


class PortalDashboard(BaseModel):
    """Вид заказчика и субподрядчика: % и ряд по неделям своего объекта. Без денег, весов, фото и
    имён сотрудников: этих полей в схеме нет."""

    as_of: date
    project_id: uuid.UUID
    name: str
    status: str
    planned_start: date | None
    planned_end: date | None
    progress: PortalProgress | None
    series: list[SeriesPoint]
