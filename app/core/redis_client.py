import redis.asyncio as redis
from redis.asyncio import Redis
from typing import Optional
from contextlib import asynccontextmanager
from .config import settings
from .logging_config import logger


_redis_client: Optional[Redis] = None


async def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.REDIS_URL,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            encoding="utf-8",
            decode_responses=True,
            socket_keepalive=True,
            socket_connect_timeout=5,
            retry_on_timeout=True,
        )
        try:
            await _redis_client.ping()
            logger.info("Redis connection established successfully")
        except Exception as e:
            logger.error("Failed to connect to Redis", error=str(e))
            raise
    return _redis_client


async def close_redis():
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
        logger.info("Redis connection closed")


@asynccontextmanager
async def get_redis_context():
    r = await get_redis()
    try:
        yield r
    finally:
        pass
