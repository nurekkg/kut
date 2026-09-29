from celery import Celery

from app.core.config import get_settings

TASK_MODULES = ["app.modules.health.tasks"]


def create_celery() -> Celery:
    settings = get_settings()
    app = Celery(
        "kut",
        broker=settings.celery_broker_url.get_secret_value(),
        backend=settings.celery_result_backend.get_secret_value(),
        include=TASK_MODULES,
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_ignore_result=True,
        result_expires=3600,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = create_celery()
