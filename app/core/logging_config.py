from __future__ import annotations
import os
import sys
import logging
import structlog
from logging.handlers import RotatingFileHandler
from datetime import datetime
from .config import settings


def setup_logging():
    log_dir = settings.LOG_DIR
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, f"compliance_{datetime.now().strftime('%Y%m%d')}.log")

    timestamper = structlog.processors.TimeStamper(fmt="iso")
    shared_processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer() if settings.DEBUG else structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=100 * 1024 * 1024,
        backupCount=30,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    return structlog.get_logger()


logger = setup_logging()
