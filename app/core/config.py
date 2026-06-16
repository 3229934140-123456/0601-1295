from pydantic_settings import BaseSettings
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    APP_NAME: str = "企业合规管理系统"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENV: str = "production"

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/compliance_db"
    DATABASE_POOL_SIZE: int = 50
    DATABASE_MAX_OVERFLOW: int = 100

    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_MAX_CONNECTIONS: int = 100

    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    SECRET_KEY: str = "change-this-in-production-very-long-secret-key-xxxxxxxxxxxxxxxxxxxx"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    MAIL_SERVER: str = "imap.example.com"
    MAIL_PORT: int = 993
    MAIL_USERNAME: str = "compliance@example.com"
    MAIL_PASSWORD: str = "password"
    MAIL_USE_SSL: bool = True

    IM_WEBHOOK_URL: str = "https://im.example.com/webhook"
    IM_WEBHOOK_TOKEN: str = "im-token"
    MANAGEMENT_GROUP_WEBHOOK: str = "https://im.example.com/management-group"

    DOOR_ACCESS_API_URL: str = "https://door.example.com/api"
    DOOR_ACCESS_API_KEY: str = "door-api-key"

    FINANCE_API_URL: str = "https://finance.example.com/api"
    FINANCE_API_KEY: str = "finance-api-key"

    DATA_COLLECTION_BATCH_SIZE: int = 1000
    DATA_PROCESSING_WORKERS: int = 8

    REPORT_OUTPUT_DIR: str = "./reports"
    EVIDENCE_PACKAGE_DIR: str = "./evidence_packages"

    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "./logs"

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
