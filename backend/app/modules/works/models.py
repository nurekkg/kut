from sqlalchemy import CheckConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BusinessBase


class Work(BusinessBase):
    """Вид работ - справочник уровня группы (ТЗ §6.1). Узлом дерева не является: связка узла и
    вида работ появится в ГПР (S3)."""

    __tablename__ = "work"

    code: Mapped[str]
    name: Mapped[str]
    unit: Mapped[str]
    category: Mapped[str] = mapped_column(index=True)

    __table_args__ = (
        CheckConstraint("btrim(code) <> '' AND btrim(name) <> ''", name="code_and_name_not_blank"),
        CheckConstraint(
            "btrim(unit) <> '' AND btrim(category) <> ''", name="unit_and_category_not_blank"
        ),
        Index("uq_work_code", "code", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )
