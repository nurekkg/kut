from app.core.celery_app import celery_app
from app.modules.health.tasks import ping


def test_ping_task_is_registered_and_returns_pong() -> None:
    assert ping.name == "health.ping"
    assert "app.modules.health.tasks" in celery_app.conf.include
    assert ping.apply().get() == "pong"
