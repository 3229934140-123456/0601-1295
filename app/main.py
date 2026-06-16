from __future__ import annotations
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

try:
    from prometheus_client import make_asgi_app, Counter, Histogram, Gauge
    PROMETHEUS_AVAILABLE = True
except ImportError:
    make_asgi_app = None
    Counter = None
    Histogram = None
    Gauge = None
    PROMETHEUS_AVAILABLE = False

from app.core import (
    settings,
    logger,
    init_db,
    close_db,
    get_redis,
    close_redis,
)
from app.core.constants import SeverityLevel
from app.api.routes import router as api_router
from app.schemas import APIResponse

if PROMETHEUS_AVAILABLE:
    HTTP_REQUEST_COUNT = Counter(
        "http_requests_total",
        "Total HTTP requests",
        ["method", "endpoint", "status_code"]
    )
    HTTP_REQUEST_DURATION = Histogram(
        "http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "endpoint"]
    )
    ACTIVE_TICKETS = Gauge(
        "active_investigation_tickets",
        "Number of active investigation tickets"
    )
else:
    HTTP_REQUEST_COUNT = None
    HTTP_REQUEST_DURATION = None
    ACTIVE_TICKETS = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    logger.info(
        "Starting Compliance Management System",
        version=settings.APP_VERSION,
        env=settings.ENV
    )

    try:
        await init_db()
        db_available = True
    except Exception as e:
        logger.warning("Database initialization failed, running in degraded mode", error=str(e))
        db_available = False

    try:
        redis = await get_redis()
        await redis.set("system_startup_time", datetime.utcnow().isoformat())
    except Exception as e:
        logger.warning("Redis initialization skipped", error=str(e))

    if db_available:
        try:
            from app.core.database import get_db_context
            from app.models.investigation import InvestigationTicket, ComplianceEvent
            from sqlalchemy import select, func

            async with get_db_context() as db:
                active_result = await db.execute(
                    select(func.count()).select_from(InvestigationTicket).where(
                        InvestigationTicket.status != "closed"
                    )
                )
                if PROMETHEUS_AVAILABLE and ACTIVE_TICKETS:
                    ACTIVE_TICKETS.set(active_result.scalar() or 0)

                from app.detection_engine.rules import build_default_rules
                rules_count = len(build_default_rules())
                logger.info(
                    "Startup checks complete",
                    database="initialized",
                    default_rules=rules_count,
                )
        except Exception as e:
            logger.warning("Startup data check failed", error=str(e))

    logger.info("Application started successfully")
    yield

    logger.info("Shutting down application...")
    try:
        await close_db()
    except Exception:
        pass
    try:
        await close_redis()
    except Exception:
        pass
    logger.info("Application shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="企业员工合规管理系统 - 多数据源采集、违规智能检测、工单自动流转、全生命周期追踪",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

if PROMETHEUS_AVAILABLE and make_asgi_app:
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = datetime.utcnow()
    endpoint = request.url.path

    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception as e:
        status_code = 500
        raise e
    finally:
        if PROMETHEUS_AVAILABLE and HTTP_REQUEST_COUNT and HTTP_REQUEST_DURATION:
            duration = (datetime.utcnow() - start_time).total_seconds()
            HTTP_REQUEST_COUNT.labels(
                method=request.method,
                endpoint=endpoint,
                status_code=status_code
            ).inc()
            HTTP_REQUEST_DURATION.labels(
                method=request.method,
                endpoint=endpoint
            ).observe(duration)

    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled exception",
        method=request.method,
        url=str(request.url),
        error=str(exc),
        exc_info=True
    )
    return JSONResponse(
        status_code=500,
        content=APIResponse(
            success=False,
            message="服务器内部错误",
            data={"error": str(exc) if settings.DEBUG else None},
        ).model_dump(mode="json"),
    )


@app.get("/", include_in_schema=False)
async def root():
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "timestamp": datetime.utcnow().isoformat(),
        "docs": "/docs" if settings.DEBUG else "disabled in production",
        "endpoints": {
            "health": "/api/v1/health",
            "dashboard": "/api/v1/dashboard",
            "tickets": "/api/v1/tickets",
            "events": "/api/v1/events",
            "reports": "/api/v1/reports/submit",
            "profiles": "/api/v1/profiles",
            "logs": "/api/v1/logs",
            "options": "/api/v1/options/constants",
        },
    }


app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        workers=4 if not settings.DEBUG else 1,
        log_level=settings.LOG_LEVEL.lower(),
    )
