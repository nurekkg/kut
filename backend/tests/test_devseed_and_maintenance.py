from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.modules.auth import devseed
from app.modules.auth.security import hash_password, verify_password
from app.modules.maintenance import tasks
from tests.auth_support import PASSWORD, AuthWorld
from tests.conftest import require_test_url


@pytest.fixture
def worker_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Задачи воркера работают под kut_worker: подменяем DATABASE_URL на его учётку."""
    url = require_test_url("TEST_DATABASE_WORKER_URL").render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    tasks._engine.cache_clear()
    yield monkeypatch
    tasks._engine().dispose()
    monkeypatch.undo()
    get_settings.cache_clear()
    tasks._engine.cache_clear()


# --- Celery beat ---

import app.modules.notifications.tasks  # noqa: E402,F401


def test_beat_schedules_both_maintenance_tasks_with_the_configured_intervals() -> None:
    schedule = celery_app.conf.beat_schedule
    settings = get_settings()

    by_task = {entry["task"]: entry["schedule"] for entry in schedule.values()}
    assert by_task == {
        "maintenance.purge_auth_data": float(settings.maintenance_purge_seconds),
        "maintenance.ensure_audit_partitions": float(settings.maintenance_partitions_seconds),
        "notification.dispatch": 60.0,
    }
    assert {
        "maintenance.purge_auth_data",
        "maintenance.ensure_audit_partitions",
        "notification.dispatch",
    } <= set(celery_app.tasks)
    assert "app.modules.maintenance.tasks" in celery_app.conf.include


def test_the_purge_task_removes_expired_rows_through_the_definer_function(
    worker_env: pytest.MonkeyPatch,
    new_user: object,
    owner_sync_engine: Engine,
) -> None:
    user, _ = new_user("foreman")  # type: ignore[operator]
    expired, live = uuid4().bytes * 2, uuid4().bytes * 2
    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        for token, expires in ((expired, "-3 days"), (live, "3 days")):
            connection.execute(
                text(
                    "INSERT INTO refresh_token (user_id, family_id, token_hash, amr, expires_at)"
                    " VALUES (:u, gen_random_uuid(), :h, '{pwd}', now() + CAST(:e AS interval))"
                ),
                {"u": user, "h": token, "e": expires},
            )

    counts = tasks.purge_auth_data()

    assert counts["refresh_tokens"] >= 1
    assert set(counts) == {"refresh_tokens", "idempotency_keys", "user_invites"}
    with owner_sync_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE kut_auth"))
        left = {
            bytes(item)
            for item in connection.scalars(
                text("SELECT token_hash FROM refresh_token WHERE user_id = :u"), {"u": user}
            )
        }
    assert left == {live}


def test_the_partitions_task_creates_future_months_once(
    worker_env: pytest.MonkeyPatch,
) -> None:
    worker_env.setenv("AUDIT_PARTITIONS_AHEAD", "48")
    get_settings.cache_clear()

    first, second = tasks.ensure_audit_partitions(), tasks.ensure_audit_partitions()

    assert first >= 0 and second == 0


# --- dev-seed ---


def test_a_second_seed_run_changes_nothing_and_never_overwrites_a_password(
    auth_world: AuthWorld, worker_sync_engine: Engine, owner_sync_engine: Engine
) -> None:
    def snapshot() -> tuple[int, int, int, str]:
        with worker_sync_engine.connect() as connection:
            users = connection.execute(
                text(
                    'SELECT count(*) FROM "user"'
                    " WHERE email LIKE '%@kut.test' AND email NOT LIKE 'tmp-%'"
                )
            ).scalar_one()
            projects = connection.execute(
                text("SELECT count(*) FROM project WHERE name LIKE '%(dev)'")
            ).scalar_one()
            links = connection.execute(
                text("SELECT count(*) FROM user_project_access")
            ).scalar_one()
        with owner_sync_engine.begin() as auth:
            auth.execute(text("SET LOCAL ROLE kut_auth"))
            hash_ = auth.execute(
                text("SELECT password_hash FROM \"user\" WHERE email = 'foreman@kut.test'")
            ).scalar_one()
        return users, projects, links, hash_

    before = snapshot()
    with worker_sync_engine.begin() as connection:
        report = devseed.seed(
            connection, password_hash=hash_password("a-different-password-9"), totp_secret=None
        )
    after = snapshot()

    assert report.changed is False and report.created == {}
    assert before == after
    assert verify_password(after[3], PASSWORD)  # прежний пароль, а не новый


def test_the_seed_covers_every_role_two_subcontractors_and_two_projects(
    auth_world: AuthWorld, worker_sync_engine: Engine
) -> None:
    with worker_sync_engine.connect() as connection:
        roles = set(
            connection.scalars(
                text(
                    'SELECT r.code FROM "user" u JOIN role r ON r.id = u.role_id'
                    " WHERE u.email LIKE '%@kut.test' AND u.deleted_at IS NULL"
                    " AND u.email NOT LIKE 'tmp-%'"
                )
            )
        )
        subs = connection.execute(
            text(
                "SELECT count(*) FROM counterparty"
                " WHERE 'subcontractor' = ANY(types) AND name LIKE '%(dev)'"
            )
        ).scalar_one()
        sub_users = connection.execute(
            text(
                'SELECT count(*) FROM "user" u JOIN role r ON r.id = u.role_id'
                " WHERE r.code = 'subcontractor' AND u.email LIKE '%@kut.test'"
                " AND u.email NOT LIKE 'tmp-%'"
            )
        ).scalar_one()

    assert roles == set(auth_world.roles)  # по пользователю на каждую из 13 ролей
    assert subs == 2 and sub_users == 2
    assert len(auth_world.projects) == 2
    assert len(devseed.USERS) == 18 and len({user.login for user in devseed.USERS}) == 18


def test_totp_is_seeded_for_owner_and_finance_director_but_not_the_accountant() -> None:
    assert {"owner", "finance_director"} == devseed.TOTP_ROLES


def test_the_seed_refuses_to_run_outside_development(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for env in ("test", "production"):
        monkeypatch.setenv("APP_ENV", env)
        monkeypatch.setenv("DEV_SEED_PASSWORD", "long-enough-password")
        get_settings.cache_clear()

        assert devseed.main() == 1
        assert "APP_ENV must be 'development'" in capsys.readouterr().err
    monkeypatch.undo()
    get_settings.cache_clear()


def test_the_seed_refuses_a_missing_or_short_password(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    for value in (None, "short"):
        if value is None:
            monkeypatch.delenv("DEV_SEED_PASSWORD", raising=False)
        else:
            monkeypatch.setenv("DEV_SEED_PASSWORD", value)

        assert devseed.main() == 1
        assert "DEV_SEED_PASSWORD" in capsys.readouterr().err
    monkeypatch.undo()
    get_settings.cache_clear()
