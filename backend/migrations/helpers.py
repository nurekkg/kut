"""Помощники миграций: RLS, права и аудит-триггеры (ADR-001 §1, §4).

Каждая новая бизнес-таблица в своей миграции обязана вызвать force_rls(), grant() и
create_audit_triggers(); тесты покрытия (tests/test_rls_coverage.py, tests/test_audit.py) красные,
если что-то из этого забыто.
"""

import re
from collections.abc import Sequence

from alembic import op

APP_LOGINS = ("kut_app", "kut_portal", "kut_worker")

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _ident(name: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return f'"{name}"'


def force_rls(table: str) -> None:
    """ENABLE + FORCE: политики действуют и на владельца таблицы (kut_owner)."""
    op.execute(f"ALTER TABLE {_ident(table)} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_ident(table)} FORCE ROW LEVEL SECURITY")


def grant(
    table: str,
    *,
    app: str = "",
    portal: str = "",
    worker: str = "",
    portal_columns: Sequence[str] = (),
) -> None:
    """Явные права по логинам; DELETE бизнес-таблицам не выдаётся (только мягкое удаление)."""
    target = _ident(table)
    for login, privileges in (("kut_app", app), ("kut_worker", worker)):
        if privileges:
            op.execute(f"GRANT {privileges} ON {target} TO {login}")
    if portal_columns:
        columns = ", ".join(_ident(column) for column in portal_columns)
        op.execute(f"GRANT SELECT ({columns}) ON {target} TO kut_portal")
    elif portal:
        op.execute(f"GRANT {portal} ON {target} TO kut_portal")


def create_audit_triggers(
    table: str,
    *,
    project_column: str = "",
    legal_entity_column: str = "",
    hidden_columns: Sequence[str] = (),
) -> None:
    """Три statement-level триггера с transition tables: одна вставка в журнал на оператор.

    project_column / legal_entity_column — колонки строки, из которых берутся project_id и
    legal_entity_id записи журнала (у project это id и legal_entity_id). hidden_columns не попадают
    в журнал (секреты).
    """
    target = _ident(table)
    for column in (project_column, legal_entity_column, *hidden_columns):
        if column:
            _ident(column)
    args = ", ".join(
        f"'{value}'" for value in (project_column, legal_entity_column, ",".join(hidden_columns))
    )
    op.execute(
        f"CREATE TRIGGER audit_insert AFTER INSERT ON {target} "
        f"REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT "
        f"EXECUTE FUNCTION app.audit_insert({args})"
    )
    op.execute(
        f"CREATE TRIGGER audit_update AFTER UPDATE ON {target} "
        f"REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows FOR EACH STATEMENT "
        f"EXECUTE FUNCTION app.audit_update({args})"
    )
    op.execute(
        f"CREATE TRIGGER audit_delete AFTER DELETE ON {target} "
        f"REFERENCING OLD TABLE AS old_rows FOR EACH STATEMENT "
        f"EXECUTE FUNCTION app.audit_delete({args})"
    )


def drop_audit_triggers(table: str) -> None:
    target = _ident(table)
    for name in ("audit_insert", "audit_update", "audit_delete"):
        op.execute(f"DROP TRIGGER IF EXISTS {name} ON {target}")
