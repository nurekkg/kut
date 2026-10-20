from datetime import UTC, datetime
from functools import lru_cache

from celery import shared_task
from sqlalchemy import Engine, create_engine

from app.core.config import get_settings
from app.modules.notifications.adapters import NotificationAdapter, get_adapter
from app.modules.notifications.dispatch import dispatch_due


@lru_cache
def _engine() -> Engine:
    return create_engine(get_settings().database_url.get_secret_value(), pool_pre_ping=True)


@lru_cache
def _adapter() -> NotificationAdapter:
    return get_adapter(get_settings())


@shared_task(name="notification.dispatch")
def dispatch() -> int:
    settings = get_settings()
    return dispatch_due(
        _engine(), _adapter(), base_url=settings.app_public_url, now=datetime.now(UTC)
    )
