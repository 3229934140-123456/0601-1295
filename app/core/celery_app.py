from celery import Celery
from kombu import Exchange, Queue
from .config import settings


celery_app = Celery(
    "compliance_tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

default_exchange = Exchange("default", type="direct")
high_priority_exchange = Exchange("high_priority", type="direct")
report_exchange = Exchange("reports", type="direct")

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_queues=(
        Queue("default", default_exchange, routing_key="default"),
        Queue("high_priority", high_priority_exchange, routing_key="high_priority"),
        Queue("data_collection", default_exchange, routing_key="data_collection"),
        Queue("violation_detection", default_exchange, routing_key="violation_detection"),
        Queue("workflow_processing", default_exchange, routing_key="workflow_processing"),
        Queue("reports", report_exchange, routing_key="reports"),
        Queue("notifications", default_exchange, routing_key="notifications"),
    ),
    task_routes={
        "app.tasks.data_collection.*": {"queue": "data_collection"},
        "app.tasks.violation_detection.*": {"queue": "violation_detection"},
        "app.tasks.workflow.*": {"queue": "workflow_processing"},
        "app.tasks.notifications.*": {"queue": "notifications"},
        "app.tasks.reports.*": {"queue": "reports"},
    },
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
    worker_max_tasks_per_child=1000,
    worker_memory_limit="2048000",
)
