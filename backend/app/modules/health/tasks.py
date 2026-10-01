import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="health.ping", ignore_result=False)
def ping() -> str:
    logger.info("ping received")
    return "pong"
