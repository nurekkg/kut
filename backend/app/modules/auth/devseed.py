"""Демонстрационные данные для разработки: `python -m app.modules.auth.devseed`.

Работает только с APP_ENV=development и под kut_worker (BYPASSRLS). Переходы статусов версий
выполняет владелец под kut_auth (DEV_SEED_OWNER_URL): kut_worker функции app.version_move не имеет.
Пароль берётся из DEV_SEED_PASSWORD, секрет TOTP владельца и финансового директора - из
DEV_SEED_TOTP_SECRET (бухгалтер остаётся без 2FA: через него проверяется обязательная настройка
при первом входе). Повторный запуск ничего не меняет: существующие записи не трогаются, пароли не
перезаписываются.
"""

import os
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, create_engine, text

from app.core.config import get_settings
from app.modules.auth.crypto import encrypt_totp_secret
from app.modules.auth.security import MIN_PASSWORD_LENGTH, hash_password
from app.modules.documents.devseed import ensure_documents
from app.modules.equipment.devseed import SECOND_EAST, ensure_equipment, ensure_shifts
from app.modules.equipment.service import bishkek_today
from app.modules.objects.devseed import ensure_project_card, ensure_tree, ensure_works
from app.modules.photos.devseed import ensure_photos
from app.modules.schedule.devseed import ensure_schedule

EAST = "ЖК «Восток» (dev)"
WEST = "ЖК «Запад» (dev)"
LE_EAST = "ООО «Кут Девелопмент» (dev)"
LE_WEST = "ОсОО «Кут Строй» (dev)"
LEGAL_ENTITIES = (LE_EAST, LE_WEST)
# Коды для номеров документов (ADR-015 часть 1 раздел 3.4): <вид>-<код>-<год>-<n>.
LEGAL_ENTITY_CODES = {LE_EAST: "ЮЛ1", LE_WEST: "ЮЛ2"}
PROJECTS = ((EAST, LE_EAST), (WEST, LE_WEST), (SECOND_EAST, LE_EAST))
SUB_A = "Субподрядчик А (dev)"
SUB_B = "Субподрядчик Б (dev)"
CUSTOMER = "Заказчик (dev)"
SUPPLIER = "Поставщик (dev)"
COUNTERPARTIES = (
    ("Арендодатель А (dev)", "supplier"),
    ("Арендодатель Б (dev)", "supplier"),
    ("Арендодатель В (dev)", "supplier"),
    (SUB_A, "subcontractor"),
    (SUB_B, "subcontractor"),
    (CUSTOMER, "customer"),
    (SUPPLIER, "supplier"),
)
TOTP_ROLES = frozenset({"owner", "finance_director"})
EMAIL_DOMAIN = "kut.test"  # зарезервированная зона .test: письма никуда не уйдут


@dataclass(frozen=True, slots=True)
class DevUser:
    login: str
    role: str
    full_name: str
    projects: tuple[str, ...] = ()
    counterparty: str | None = None
    phone: str | None = None
    legal_entities: tuple[str, ...] = ()

    @property
    def email(self) -> str:
        return f"{self.login}@{EMAIL_DOMAIN}"


# Роли scope=group видят все объекты без user_project_access; остальным объекты выдаются явно.
USERS = (
    DevUser("owner", "owner", "Владелец (dev)"),
    DevUser("finance", "finance_director", "Финансовый директор (dev)"),
    DevUser("techdir", "technical_director", "Технический директор (dev)"),
    DevUser(
        "foreman",
        "foreman",
        "Прораб Восток (dev)",
        (EAST,),
        legal_entities=(LE_EAST,),
        phone="+996555100001",
    ),
    DevUser(
        "foreman2",
        "foreman",
        "Прораб Восток-2 (dev)",
        (SECOND_EAST,),
        legal_entities=(LE_EAST,),
    ),
    DevUser("master", "master", "Мастер Восток (dev)", (EAST,), phone="+996555100002"),
    DevUser("estimator", "estimator", "Сметчик (dev)", (EAST,)),
    DevUser("storekeeper", "storekeeper", "Кладовщик Восток (dev)", (EAST,)),
    DevUser("procurement", "procurement", "Снабженец Запад (dev)", (WEST,)),
    DevUser("accountant", "accountant", "Бухгалтер (dev)", (WEST,), legal_entities=LEGAL_ENTITIES),
    DevUser("lawyer", "lawyer", "Юрист (dev)", (EAST, WEST), legal_entities=LEGAL_ENTITIES),
    DevUser(
        "customer", "customer", "Представитель заказчика (dev)", (EAST,), counterparty=CUSTOMER
    ),
    DevUser("sub_a", "subcontractor", "Субподрядчик А: прораб (dev)", (EAST,), counterparty=SUB_A),
    DevUser("sub_b", "subcontractor", "Субподрядчик Б: прораб (dev)", (WEST,), counterparty=SUB_B),
    DevUser("supplier", "supplier", "Поставщик: менеджер (dev)", (WEST,), counterparty=SUPPLIER),
    # Техника (ADR-014, Q8): три роли модуля; машинист - штатный, с объектом и юрлицом.
    DevUser("equipment_head", "equipment_head", "Начальник отдела спецтехники (dev)"),
    DevUser("mechanic", "mechanic", "Механик парка (dev)"),
    DevUser(
        "machinist",
        "machinist",
        "Машинист Восток (dev)",
        (EAST, SECOND_EAST),
        legal_entities=(LE_EAST,),
    ),
)


@dataclass(slots=True)
class SeedReport:
    created: dict[str, int] = field(default_factory=dict)

    def add(self, table: str, count: int = 1) -> None:
        if count:
            self.created[table] = self.created.get(table, 0) + count

    @property
    def changed(self) -> bool:
        return bool(self.created)


def _find(connection: Connection, sql: str, **params: Any) -> Any:
    return connection.execute(text(sql), params).scalar_one_or_none()


def _insert(connection: Connection, report: SeedReport, table: str, sql: str, **params: Any) -> Any:
    created = connection.execute(text(sql), params).scalar_one()
    report.add(table)
    return created


def _ensure_legal_entity(connection: Connection, report: SeedReport, name: str) -> Any:
    code = LEGAL_ENTITY_CODES[name]
    found = _find(
        connection,
        "SELECT id FROM legal_entity WHERE name = :name AND deleted_at IS NULL",
        name=name,
    )
    if found is not None:
        # Код нужен номерам документов (ADR-015): строки, созданные до S5a, получают его один раз.
        connection.execute(
            text(
                "UPDATE legal_entity SET code = :code"
                " WHERE id = :id AND code IS NULL AND deleted_at IS NULL"
            ),
            {"code": code, "id": found},
        )
        return found
    return _insert(
        connection,
        report,
        "legal_entity",
        "INSERT INTO legal_entity (name, code) VALUES (:name, :code) RETURNING id",
        name=name,
        code=code,
    )


def _ensure_counterparty(connection: Connection, report: SeedReport, name: str, kind: str) -> Any:
    found = _find(
        connection,
        "SELECT id FROM counterparty WHERE name = :name AND deleted_at IS NULL",
        name=name,
    )
    if found is not None:
        return found
    return _insert(
        connection,
        report,
        "counterparty",
        "INSERT INTO counterparty (name, types) VALUES (:name, :types) RETURNING id",
        name=name,
        types=[kind],
    )


def _ensure_project(
    connection: Connection, report: SeedReport, name: str, legal_entity_id: Any
) -> Any:
    found = _find(
        connection, "SELECT id FROM project WHERE name = :name AND deleted_at IS NULL", name=name
    )
    if found is not None:
        return found
    return _insert(
        connection,
        report,
        "project",
        "INSERT INTO project (name, legal_entity_id) VALUES (:name, :entity) RETURNING id",
        name=name,
        entity=legal_entity_id,
    )


def _ensure_link(
    connection: Connection,
    report: SeedReport,
    table: str,
    column: str,
    user_id: Any,
    target_id: Any,
) -> None:
    # table и column - константы этого модуля, не пользовательский ввод.
    exists = _find(
        connection,
        f"SELECT id FROM {table} WHERE user_id = :user AND {column} = :target"  # noqa: S608
        " AND deleted_at IS NULL",
        user=user_id,
        target=target_id,
    )
    if exists is None:
        connection.execute(
            text(f"INSERT INTO {table} (user_id, {column}) VALUES (:user, :target)"),  # noqa: S608
            {"user": user_id, "target": target_id},
        )
        report.add(table)


def _ensure_user(
    connection: Connection,
    report: SeedReport,
    user: DevUser,
    *,
    role_id: Any,
    counterparty_id: Any,
    password_hash: str,
    totp_secret: str | None,
    totp_roles: frozenset[str],
) -> Any:
    found = _find(
        connection,
        'SELECT id FROM "user" WHERE email = :email AND deleted_at IS NULL',
        email=user.email,
    )
    if found is not None:
        return found
    user_id = _insert(
        connection,
        report,
        "user",
        'INSERT INTO "user" (email, phone, full_name, role_id, counterparty_id, password_hash,'
        " password_changed_at) VALUES (:email, :phone, :full_name, :role_id, :counterparty_id,"
        " :hash, now()) RETURNING id",
        email=user.email,
        phone=user.phone,
        full_name=user.full_name,
        role_id=role_id,
        counterparty_id=counterparty_id,
        hash=password_hash,
    )
    if totp_secret and user.role in totp_roles:
        blob, key_id = encrypt_totp_secret(totp_secret, user_id=user_id)
        connection.execute(
            text(
                'UPDATE "user" SET totp_secret_enc = :blob, totp_key_id = :key_id,'
                " totp_confirmed_at = now() WHERE id = :id"
            ),
            {"blob": blob, "key_id": key_id, "id": user_id},
        )
    return user_id


def seed(
    connection: Connection,
    *,
    password_hash: str,
    totp_secret: str | None,
    totp_roles: frozenset[str] = TOTP_ROLES,
    tree: bool = True,
    schedule: bool = True,
    equipment: bool = True,
) -> SeedReport:
    report = SeedReport()
    roles = {
        row.code: row.id
        for row in connection.execute(text("SELECT code, id FROM role WHERE deleted_at IS NULL"))
    }
    missing = {user.role for user in USERS} - roles.keys()
    if missing:
        raise RuntimeError(f"roles are not seeded (run the RBAC seed first): {sorted(missing)}")

    legal_entities = {
        name: _ensure_legal_entity(connection, report, name) for name in LEGAL_ENTITIES
    }
    counterparties = {
        name: _ensure_counterparty(connection, report, name, kind) for name, kind in COUNTERPARTIES
    }
    projects = {
        name: _ensure_project(connection, report, name, legal_entities[entity])
        for name, entity in PROJECTS
    }
    for key, name in (("east", EAST), ("west", WEST)):
        if ensure_project_card(connection, projects[name], key, counterparties):
            report.add("project_card")
    report.add("work", ensure_works(connection))
    user_ids: dict[str, Any] = {}
    for user in USERS:
        user_id = user_ids[user.login] = _ensure_user(
            connection,
            report,
            user,
            role_id=roles[user.role],
            counterparty_id=counterparties[user.counterparty] if user.counterparty else None,
            password_hash=password_hash,
            totp_secret=totp_secret,
            totp_roles=totp_roles,
        )
        for project in user.projects:
            _ensure_link(
                connection, report, "user_project_access", "project_id", user_id, projects[project]
            )
        for entity in user.legal_entities:
            _ensure_link(
                connection,
                report,
                "user_legal_entity_access",
                "legal_entity_id",
                user_id,
                legal_entities[entity],
            )
    if equipment:
        for table, count in ensure_equipment(
            connection,
            legal_entities=legal_entities,
            counterparties=counterparties,
            projects={"east": projects[EAST], "west": projects[WEST]},
            users=user_ids,
            keys={"east": LE_EAST, "west": LE_WEST},
            today=bishkek_today(),
        ).items():
            report.add(table, count)
    if tree:
        report.add(
            "project_node",
            ensure_tree(
                connection,
                projects[EAST],
                legal_entities[LE_EAST],
                foreman_id=user_ids["foreman"],
                master_id=user_ids["master"],
            ),
        )
    if tree and schedule:
        plan = ensure_schedule(
            connection,
            projects[EAST],
            legal_entities[LE_EAST],
            foreman_id=user_ids["foreman"],
            master_id=user_ids["master"],
        )
        report.add("schedule_item", plan.items)
        report.add("schedule_dependency", plan.dependencies)
        report.add("work_actual", plan.actuals)
    return report


def seed_media(connection: Connection, report: SeedReport) -> None:
    """Фото факта: файлы идут в S3, поэтому отдельно от основного сида (тестам S3 не нужен)."""
    project = _find(
        connection, "SELECT id FROM project WHERE name = :name AND deleted_at IS NULL", name=EAST
    )
    entity = _find(
        connection,
        "SELECT legal_entity_id FROM project WHERE id = :id",
        id=project,
    )
    if project is not None:
        report.add("work_photo", ensure_photos(connection, project, entity))


def seed_documents(
    connection: Connection, report: SeedReport, transitions: list[tuple[uuid.UUID, str]]
) -> None:
    """Демо-документы: файлы идут в S3, поэтому отдельно от основного сида (как фото).
    Переходы статусов копятся в transitions и выполняются в main (владелец под kut_auth)."""
    east = _find(
        connection, "SELECT id FROM project WHERE name = :name AND deleted_at IS NULL", name=EAST
    )
    west = _find(
        connection, "SELECT id FROM project WHERE name = :name AND deleted_at IS NULL", name=WEST
    )
    if east is None or west is None:
        return
    legal = {
        key: _find(
            connection,
            "SELECT legal_entity_id FROM project WHERE id = :id",
            id=project,
        )
        for key, project in (("east", east), ("west", west))
    }
    counterparties = {
        name: _find(connection, "SELECT id FROM counterparty WHERE name = :name", name=name)
        for name, _kind in COUNTERPARTIES
    }
    counts = ensure_documents(
        connection,
        legal_entities={"east": legal["east"], "west": legal["west"]},
        projects={"east": east, "west": west},
        counterparties=counterparties,
        transitions=transitions,
    )
    for table, count in counts.items():
        if count:
            report.add(table, count)


def seed_shifts(connection: Connection, report: SeedReport) -> None:
    """Листы, ГСМ и ремонт SE-2a: только dev-seed, как фото; тестовая база их не получает."""
    east = _find(
        connection, "SELECT id FROM project WHERE name = :name AND deleted_at IS NULL", name=EAST
    )
    west = _find(
        connection, "SELECT id FROM project WHERE name = :name AND deleted_at IS NULL", name=WEST
    )
    east_entity = _find(connection, "SELECT id FROM legal_entity WHERE name = :name", name=LE_EAST)
    west_entity = _find(connection, "SELECT id FROM legal_entity WHERE name = :name", name=LE_WEST)
    users = {
        login: _find(
            connection,
            'SELECT id FROM "user" WHERE email = :email',
            email=f"{login}@{EMAIL_DOMAIN}",
        )
        for login in ("foreman", "techdir", "machinist")
    }
    if None in (east, west, east_entity, west_entity, *users.values()):
        return
    for table, count in ensure_shifts(
        connection,
        today=bishkek_today(),
        east=east,
        west=west,
        east_entity=east_entity,
        west_entity=west_entity,
        users=users,
    ).items():
        report.add(table, count)


def main() -> int:
    settings = get_settings()
    if settings.app_env != "development":
        print("dev seed refused: APP_ENV must be 'development'", file=sys.stderr)
        return 1
    password = os.environ.get("DEV_SEED_PASSWORD", "")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(
            f"dev seed refused: set DEV_SEED_PASSWORD ({MIN_PASSWORD_LENGTH}+ characters)",
            file=sys.stderr,
        )
        return 1
    totp_secret = os.environ.get("DEV_SEED_TOTP_SECRET") or None
    owner_url = os.environ.get("DEV_SEED_OWNER_URL", "")
    if not owner_url:
        print(
            "dev seed refused: set DEV_SEED_OWNER_URL (переходы версий идут от владельца)",
            file=sys.stderr,
        )
        return 1
    engine = create_engine(settings.database_url.get_secret_value())
    transitions: list[tuple[uuid.UUID, str]] = []
    with engine.begin() as connection:
        report = seed(connection, password_hash=hash_password(password), totp_secret=totp_secret)
        seed_media(connection, report)
        seed_shifts(connection, report)
        seed_documents(connection, report, transitions)
    engine.dispose()
    owner_engine = create_engine(owner_url)
    with owner_engine.begin() as owner:
        owner.execute(text("SET LOCAL ROLE kut_auth"))
        for version_id, step in transitions:
            owner.execute(
                text("SELECT app.version_move(:v, :to, NULL, NULL)"), {"v": version_id, "to": step}
            )
    owner_engine.dispose()
    print(f"dev seed: created {report.created}" if report.changed else "dev seed: nothing to do")
    return 0


if __name__ == "__main__":
    sys.exit(main())
