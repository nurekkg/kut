import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, Field, computed_field, model_validator

from app.core.schemas import StrictModel

Volume = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3)]
Weight = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3)]


def _check_dates(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise ValueError("plan_end is before plan_start")


class ScheduleItemCreate(StrictModel):
    node_id: uuid.UUID
    work_id: uuid.UUID
    plan_start: date
    plan_end: date
    plan_volume: Volume
    weight: Weight | None = None
    responsible_user_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _check_dates(self.plan_start, self.plan_end)
        return self


class ScheduleItemPatch(StrictModel):
    """Меняются только присланные поля; null очищает вес и ответственного. Узел и вид работ строки
    не меняются: строку удаляют и создают заново."""

    plan_start: date | None = None
    plan_end: date | None = None
    plan_volume: Volume | None = None
    weight: Weight | None = None
    responsible_user_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        for required in ("plan_start", "plan_end", "plan_volume"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} cannot be null")
        if {"plan_start", "plan_end"} <= self.model_fields_set:
            _check_dates(self.plan_start, self.plan_end)
        return self


class ScheduleItemOut(BaseModel):
    """Ответственный внешним ролям не отдаётся: его колонки закрыты для пула kut_portal."""

    id: uuid.UUID
    project_id: uuid.UUID
    node_id: uuid.UUID
    work_id: uuid.UUID
    work_code: str
    work_name: str
    unit: str
    plan_start: date
    plan_end: date
    plan_volume: Decimal
    weight: Decimal | None
    responsible_user_id: uuid.UUID | None = None
    responsible_name: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_days(self) -> int:
        return (self.plan_end - self.plan_start).days + 1


class DependencyCreate(StrictModel):
    """Зависимость «окончание - начало»: successor начинается после окончания predecessor."""

    predecessor_id: uuid.UUID
    successor_id: uuid.UUID


class DependencyOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    predecessor_id: uuid.UUID
    successor_id: uuid.UUID
