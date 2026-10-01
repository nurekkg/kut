import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import (
    Boolean,
    Connection,
    Table,
    create_engine,
    func,
    literal_column,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.modules.rbac.enums import Action, Module, RoleScope, expected_catalog
from app.modules.rbac.models import Permission, Role, RolePermission

SEED_FILE = Path(__file__).resolve().parents[3] / "seeds" / "rbac_matrix.yaml"

PermissionKey = tuple[str, str, str | None]

_ABSENT = "—"
_CELL = re.compile(r"^(R/W|R)(?: \(свой\))?$")
_CELL_ACTIONS: dict[str, tuple[Action, ...]] = {
    "R": (Action.READ,),
    "R/W": (Action.READ, Action.WRITE),
}

ROLE = cast(Table, Role.__table__)
PERMISSION = cast(Table, Permission.__table__)
ROLE_PERMISSION = cast(Table, RolePermission.__table__)


class RoleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    name: str
    scope: RoleScope
    rank: int
    requires_2fa: bool = False


class ExtraGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    permission: str


class RbacSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    roles: list[RoleSpec]
    matrix: dict[Module, dict[str, str]]
    extra: list[ExtraGrant] = []

    @model_validator(mode="after")
    def _check(self) -> "RbacSpec":
        codes = [role.code for role in self.roles]
        if len(set(codes)) != len(codes):
            raise ValueError("duplicate role codes")
        known = set(codes)
        for module, row in self.matrix.items():
            if set(row) != known:
                raise ValueError(f"matrix row {module.value!r} must list every role exactly once")
            for cell in row.values():
                _cell_actions(cell)
        catalog = expected_catalog()
        for grant in self.extra:
            if grant.role not in known:
                raise ValueError(f"extra grant for unknown role {grant.role!r}")
            if _parse_permission(grant.permission) not in catalog:
                raise ValueError(f"extra grant of unknown permission {grant.permission!r}")
        return self


def _cell_actions(cell: str) -> tuple[Action, ...]:
    if cell == _ABSENT:
        return ()
    match = _CELL.match(cell)
    if match is None:
        raise ValueError(f"bad matrix cell {cell!r}")
    return _CELL_ACTIONS[match[1]]


def _parse_permission(value: str) -> PermissionKey:
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"permission must be module:action[:qualifier], got {value!r}")
    return parts[0], parts[1], parts[2] if len(parts) == 3 else None


def load_spec(path: Path = SEED_FILE) -> RbacSpec:
    with path.open(encoding="utf-8") as handle:
        return RbacSpec.model_validate(yaml.safe_load(handle))


def desired_grants(spec: RbacSpec) -> dict[str, set[PermissionKey]]:
    grants: dict[str, set[PermissionKey]] = {role.code: set() for role in spec.roles}
    for module, row in spec.matrix.items():
        for role, cell in row.items():
            grants[role].update(
                (module.value, action.value, None) for action in _cell_actions(cell)
            )
    for extra in spec.extra:
        grants[extra.role].add(_parse_permission(extra.permission))
    return grants


@dataclass(frozen=True, slots=True)
class SyncReport:
    roles_created: int
    roles_updated: int
    permissions_created: int
    grants_added: int
    grants_removed: int

    @property
    def changed(self) -> bool:
        return any(
            (
                self.roles_created,
                self.roles_updated,
                self.permissions_created,
                self.grants_added,
                self.grants_removed,
            )
        )


def _sync_roles(connection: Connection, spec: RbacSpec) -> tuple[int, int]:
    insert = pg_insert(ROLE).values(
        [
            {
                "code": role.code,
                "name": role.name,
                "scope": role.scope.value,
                "requires_2fa": role.requires_2fa,
                "rank": role.rank,
                "is_system": True,
            }
            for role in spec.roles
        ]
    )
    excluded = insert.excluded
    statement = insert.on_conflict_do_update(
        index_elements=[ROLE.c.code],
        set_={
            "name": excluded.name,
            "scope": excluded.scope,
            "requires_2fa": excluded.requires_2fa,
            "rank": excluded.rank,
            "is_system": True,
            "deleted_at": None,
            "updated_at": func.now(),
        },
        where=or_(
            ROLE.c.name.is_distinct_from(excluded.name),
            ROLE.c.scope.is_distinct_from(excluded.scope),
            ROLE.c.requires_2fa.is_distinct_from(excluded.requires_2fa),
            ROLE.c.rank.is_distinct_from(excluded.rank),
            ROLE.c.is_system.is_(False),
            ROLE.c.deleted_at.is_not(None),
        ),
    ).returning(literal_column("(xmax = 0)", Boolean).label("inserted"))
    flags = [row.inserted for row in connection.execute(statement)]
    return sum(flags), len(flags) - sum(flags)


def _sync_catalog(connection: Connection) -> tuple[int, dict[PermissionKey, object]]:
    statement = (
        pg_insert(PERMISSION)
        .values(
            [
                {"module": module, "action": action, "qualifier": qualifier}
                for module, action, qualifier in sorted(expected_catalog(), key=str)
            ]
        )
        .on_conflict_do_nothing(constraint="uq_permission_module_action_qualifier")
        .returning(PERMISSION.c.id)
    )
    created = len(connection.execute(statement).all())
    rows = connection.execute(
        select(
            PERMISSION.c.module, PERMISSION.c.action, PERMISSION.c.qualifier, PERMISSION.c.id
        ).where(PERMISSION.c.deleted_at.is_(None))
    )
    return created, {(row.module, row.action, row.qualifier): row.id for row in rows}


def _sync_grants(
    connection: Connection,
    spec: RbacSpec,
    permission_ids: dict[PermissionKey, object],
) -> tuple[int, int]:
    role_ids = {
        row.code: row.id
        for row in connection.execute(
            select(ROLE.c.code, ROLE.c.id).where(
                ROLE.c.code.in_([role.code for role in spec.roles])
            )
        )
    }
    added = removed = 0
    for code, keys in desired_grants(spec).items():
        role_id = role_ids[code]
        desired = {permission_ids[key] for key in keys}
        existing = set(
            connection.scalars(
                select(ROLE_PERMISSION.c.permission_id).where(
                    ROLE_PERMISSION.c.role_id == role_id, ROLE_PERMISSION.c.deleted_at.is_(None)
                )
            )
        )
        if to_add := desired - existing:
            insert = (
                pg_insert(ROLE_PERMISSION)
                .values([{"role_id": role_id, "permission_id": pid} for pid in to_add])
                .on_conflict_do_nothing(
                    index_elements=[ROLE_PERMISSION.c.role_id, ROLE_PERMISSION.c.permission_id],
                    index_where=ROLE_PERMISSION.c.deleted_at.is_(None),
                )
                .returning(ROLE_PERMISSION.c.id)
            )
            added += len(connection.execute(insert).all())
        if to_remove := existing - desired:
            removal = (
                update(ROLE_PERMISSION)
                .where(
                    ROLE_PERMISSION.c.role_id == role_id,
                    ROLE_PERMISSION.c.permission_id.in_(to_remove),
                    ROLE_PERMISSION.c.deleted_at.is_(None),
                )
                .values(deleted_at=func.now(), updated_at=func.now())
                .returning(ROLE_PERMISSION.c.id)
            )
            removed += len(connection.execute(removal).all())
    return added, removed


def sync_rbac(connection: Connection, spec: RbacSpec) -> SyncReport:
    """Идемпотентная загрузка: повторный запуск без изменений файла ничего не пишет."""
    roles_created, roles_updated = _sync_roles(connection, spec)
    permissions_created, permission_ids = _sync_catalog(connection)
    grants_added, grants_removed = _sync_grants(connection, spec, permission_ids)
    return SyncReport(
        roles_created, roles_updated, permissions_created, grants_added, grants_removed
    )


def main() -> int:
    spec = load_spec()
    engine = create_engine(get_settings().database_url.get_secret_value())
    with engine.begin() as connection:
        report = sync_rbac(connection, spec)
    print(f"rbac seed: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
