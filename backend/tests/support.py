from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from app.core.context import RequestContext


@dataclass(frozen=True)
class World:
    """Общий набор данных (conftest.py::world): два юрлица, три объекта, пять пользователей."""

    roles: dict[str, UUID]
    le1: UUID
    le2: UUID
    p1: UUID
    p2: UUID
    p_deleted: UUID
    sub_a: UUID
    sub_b: UUID
    customer: UUID
    supplier: UUID
    owner_user: UUID
    foreman_user: UUID
    sub_a_user: UUID
    sub_b_user: UUID
    customer_user: UUID

    def owner(self, request_id: UUID | None = None) -> RequestContext:
        return internal(
            self.owner_user,
            projects=(self.p1, self.p2),
            legal_entities=(self.le1, self.le2),
            request_id=request_id,
        )

    def foreman(self) -> RequestContext:
        """Внутренний пользователь только с объектом p1 (и p_deleted для проверки фильтра)."""
        return internal(self.foreman_user, projects=(self.p1, self.p_deleted))

    def sub(self, which: str) -> RequestContext:
        if which == "a":
            return external(self.sub_a_user, self.sub_a, projects=(self.p1, self.p_deleted))
        return external(self.sub_b_user, self.sub_b, projects=(self.p1, self.p2))

    def customer_ctx(self) -> RequestContext:
        return external(self.customer_user, self.customer, projects=(self.p1,))


def internal(
    user_id: UUID | None,
    *,
    projects: Iterable[UUID] = (),
    legal_entities: Iterable[UUID] = (),
    request_id: UUID | None = None,
) -> RequestContext:
    return RequestContext(
        user_id=user_id,
        is_internal=True,
        project_ids=frozenset(projects),
        legal_entity_ids=frozenset(legal_entities),
        request_id=request_id,
    )


def external(
    user_id: UUID,
    counterparty_id: UUID,
    *,
    projects: Iterable[UUID] = (),
    request_id: UUID | None = None,
) -> RequestContext:
    return RequestContext(
        user_id=user_id,
        is_internal=False,
        project_ids=frozenset(projects),
        counterparty_id=counterparty_id,
        request_id=request_id,
    )
