"""S7b (ревью): откат ревизии с версиями без файла оставляет данные и правила, повторный upgrade
не падает.

Версия ВН без файла создаётся обычным путём API (как приложение, kut_app). Миграции идут под
kut_owner
в отдельном контексте: API в нём не вызывается, иначе приложение получило бы URL владельца. Откат до
ревизии перед e7a2 не удаляет документы, поэтому CHECK «всё или ничего» и NOT NULL-колонки остаются,
а повторный upgrade до head проходит.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.config import get_settings
from tests.conftest import ALEMBIC_INI, require_test_url
from tests.test_materials_stock import document, make_yard

BEFORE_FILES = "d9a4c7e2f6b1"


@contextmanager
def as_owner() -> Iterator[Config]:
    """Миграции против тестовой БД под kut_owner; приложение в этом контексте не используется."""
    url = require_test_url("TEST_DATABASE_OWNER_URL").render_as_string(hide_password=False)
    config = Config(str(ALEMBIC_INI))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        try:
            yield config
        finally:
            get_settings.cache_clear()


async def test_a_downgrade_with_a_waybill_without_file_keeps_the_rules_and_upgrades_again(
    api, auth_world, worker_sync_engine: Engine, owner_sync_engine: Engine
) -> None:
    yard = await make_yard(api, auth_world, worker_sync_engine)
    _, version = await document(api, yard.desk.heads["storekeeper"], yard, "waybill_internal", [])

    with as_owner() as config:
        command.downgrade(config, BEFORE_FILES)
        command.upgrade(config, "head")

    with owner_sync_engine.connect() as connection:
        checks = connection.scalar(
            text(
                "SELECT count(*) FROM pg_constraint"
                " WHERE conname = 'ck_document_version_file_all_or_none'"
            )
        )
        s3_key = connection.scalar(
            text("SELECT s3_key FROM document_version WHERE id = CAST(:id AS uuid)"),
            {"id": version},
        )
    assert checks == 1
    assert s3_key is None
