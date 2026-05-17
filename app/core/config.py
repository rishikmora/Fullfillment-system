from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql://fulfillment:fulfillment@localhost:5432/fulfillment_db"
    redis_url: str = "redis://localhost:6379/0"
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_order_topic: str = "order-events"
    kafka_reorder_topic: str = "reorder-events"
    low_stock_threshold: int = 10
    app_env: str = "development"

    model_config = {"env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
