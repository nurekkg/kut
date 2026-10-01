import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import BusinessBase
from app.modules.rbac.enums import RoleScope

_SCOPES = ", ".join(f"'{scope.value}'" for scope in RoleScope)


class Role(BusinessBase):
    __tablename__ = "role"

    code: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    scope: Mapped[str]
    requires_2fa: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    rank: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    is_system: Mapped[bool] = mapped_column(default=False, server_default=text("false"))

    __table_args__ = (CheckConstraint(f"scope IN ({_SCOPES})", name="scope_valid"),)


class Permission(BusinessBase):
    __tablename__ = "permission"

    module: Mapped[str]
    action: Mapped[str]
    qualifier: Mapped[str | None] = mapped_column(default=None)

    __table_args__ = (
        UniqueConstraint(
            "module",
            "action",
            "qualifier",
            name="uq_permission_module_action_qualifier",
            postgresql_nulls_not_distinct=True,
        ),
    )


class RolePermission(BusinessBase):
    __tablename__ = "role_permission"

    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("role.id"))
    permission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("permission.id"), index=True)

    __table_args__ = (
        Index(
            "uq_role_permission_role_id_permission_id",
            "role_id",
            "permission_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )
