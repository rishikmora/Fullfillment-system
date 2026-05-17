import redis
import json
import logging
from typing import Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_redis_client: Optional[redis.Redis] = None

INVENTORY_TTL = 300  # 5 minutes


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            retry_on_timeout=True,
        )
    return _redis_client


def _inventory_key(warehouse_id: str, product_id: str) -> str:
    return f"inv:{warehouse_id}:{product_id}"


def cache_inventory(warehouse_id: str, product_id: str, available: int) -> None:
    try:
        r = get_redis()
        r.setex(_inventory_key(warehouse_id, product_id), INVENTORY_TTL, available)
    except redis.RedisError as e:
        logger.warning("Redis cache write failed: %s", e)


def get_cached_inventory(warehouse_id: str, product_id: str) -> Optional[int]:
    try:
        r = get_redis()
        val = r.get(_inventory_key(warehouse_id, product_id))
        return int(val) if val is not None else None
    except redis.RedisError as e:
        logger.warning("Redis cache read failed: %s", e)
        return None


def decrement_cached_inventory(warehouse_id: str, product_id: str, qty: int) -> Optional[int]:
    """
    Atomically decrements cached inventory. Returns new value or None on miss/error.
    Uses DECRBY which is atomic — no race conditions under concurrent orders.
    """
    try:
        r = get_redis()
        key = _inventory_key(warehouse_id, product_id)
        if r.exists(key):
            new_val = r.decrby(key, qty)
            return new_val
        return None
    except redis.RedisError as e:
        logger.warning("Redis atomic decrement failed: %s", e)
        return None


def invalidate_inventory(warehouse_id: str, product_id: str) -> None:
    try:
        r = get_redis()
        r.delete(_inventory_key(warehouse_id, product_id))
    except redis.RedisError as e:
        logger.warning("Redis invalidation failed: %s", e)


def cache_metrics(metrics: dict) -> None:
    try:
        r = get_redis()
        r.setex("metrics:dashboard", 10, json.dumps(metrics))
    except redis.RedisError as e:
        logger.warning("Redis metrics cache failed: %s", e)


def get_cached_metrics() -> Optional[dict]:
    try:
        r = get_redis()
        val = r.get("metrics:dashboard")
        return json.loads(val) if val else None
    except redis.RedisError as e:
        logger.warning("Redis metrics read failed: %s", e)
        return None
