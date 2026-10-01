import logging
from functools import lru_cache

from celery import shared_task
from sqlalchemy import Engine, create_engine, text

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _engine() -> Engine:
    # Воркер подключается как kut_worker (BYPASSRLS), контекст запроса ему не нужен.
    return create_engine(get_settings().database_url.get_secret_value(), pool_pre_ping=True)


@shared_task(name="maintenance.purge_auth_data")
def purge_auth_data() -> dict[str, int]:
    """Удаляет просроченные refresh-токены и ключи идемпотентности (app.purge_expired_auth_data)."""
    with _engine().begin() as connection:
        counts: dict[str, int] = connection.execute(
            text("SELECT app.purge_expired_auth_data()")
        ).scalar_one()
    logger.info("purged expired auth data: %s", counts)
    return counts


@shared_task(name="maintenance.ensure_audit_partitions")
def ensure_audit_partitions() -> int:
    """Создаёт месячные партиции audit_log на audit_partitions_ahead месяцев вперёд."""
    ahead = get_settings().audit_partitions_ahead
    with _engine().begin() as connection:
        created: int = connection.execute(
            text("SELECT app.ensure_audit_partitions(:ahead)"), {"ahead": ahead}
        ).scalar_one()
    logger.info("audit partitions ensured: created=%s ahead=%s", created, ahead)
    return created
