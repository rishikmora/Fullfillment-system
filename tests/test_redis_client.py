"""
Tests for app/db/redis_client.py
Covers: cache reads, writes, atomic decrement, invalidation, metrics cache.
"""

import redis
from unittest.mock import patch, MagicMock

from app.db import redis_client as rc


WH_ID = "wh-abc"
PROD_ID = "prod-xyz"


class TestInventoryCache:
    def test_cache_inventory_sets_key_with_ttl(self):
        with patch("app.db.redis_client.get_redis") as mock_get:
            r = MagicMock()
            mock_get.return_value = r
            rc.cache_inventory(WH_ID, PROD_ID, 50)
            key = f"inv:{WH_ID}:{PROD_ID}"
            r.setex.assert_called_once_with(key, rc.INVENTORY_TTL, 50)

    def test_cache_inventory_swallows_redis_error(self):
        with patch("app.db.redis_client.get_redis") as mock_get:
            r = MagicMock()
            r.setex.side_effect = redis.RedisError("connection refused")
            mock_get.return_value = r
            # Should not raise
            rc.cache_inventory(WH_ID, PROD_ID, 50)

    def test_get_cached_inventory_returns_int(self):
        with patch("app.db.redis_client.get_redis") as mock_get:
            r = MagicMock()
            r.get.return_value = "75"
            mock_get.return_value = r
            result = rc.get_cached_inventory(WH_ID, PROD_ID)
            assert result == 75

    def test_get_cached_inventory_returns_none_on_miss(self):
        with patch("app.db.redis_client.get_redis") as mock_get:
            r = MagicMock()
            r.get.return_value = None
            mock_get.return_value = r
            result = rc.get_cached_inventory(WH_ID, PROD_ID)
            assert result is None

    def test_get_cached_inventory_returns_none_on_error(self):
        with patch("app.db.redis_client.get_redis") as mock_get:
            r = MagicMock()
            r.get.side_effect = redis.RedisError("timeout")
            mock_get.return_value = r
            result = rc.get_cached_inventory(WH_ID, PROD_ID)
            assert result is None

    def test_invalidate_deletes_key(self):
        with patch("app.db.redis_client.get_redis") as mock_get:
            r = MagicMock()
            mock_get.return_value = r
            rc.invalidate_inventory(WH_ID, PROD_ID)
            r.delete.assert_called_once_with(f"inv:{WH_ID}:{PROD_ID}")

    def test_invalidate_swallows_redis_error(self):
        with patch("app.db.redis_client.get_redis") as mock_get:
            r = MagicMock()
            r.delete.side_effect = redis.RedisError("connection lost")
            mock_get.return_value = r
            rc.invalidate_inventory(WH_ID, PROD_ID)  # should not raise


class TestAtomicDecrement:
    def test_decrement_returns_new_value_when_key_exists(self):
        with patch("app.db.redis_client.get_redis") as mock_get:
            r = MagicMock()
            r.exists.return_value = 1
            r.decrby.return_value = 45
            mock_get.return_value = r
            result = rc.decrement_cached_inventory(WH_ID, PROD_ID, 5)
            assert result == 45
            r.decrby.assert_called_once_with(f"inv:{WH_ID}:{PROD_ID}", 5)

    def test_decrement_returns_none_on_cache_miss(self):
        with patch("app.db.redis_client.get_redis") as mock_get:
            r = MagicMock()
            r.exists.return_value = 0
            mock_get.return_value = r
            result = rc.decrement_cached_inventory(WH_ID, PROD_ID, 5)
            assert result is None
            r.decrby.assert_not_called()

    def test_decrement_returns_none_on_redis_error(self):
        with patch("app.db.redis_client.get_redis") as mock_get:
            r = MagicMock()
            r.exists.side_effect = redis.RedisError("timeout")
            mock_get.return_value = r
            result = rc.decrement_cached_inventory(WH_ID, PROD_ID, 5)
            assert result is None

    def test_decrement_detects_negative_race_condition(self):
        """If DECRBY returns negative, a race condition occurred."""
        with patch("app.db.redis_client.get_redis") as mock_get:
            r = MagicMock()
            r.exists.return_value = 1
            r.decrby.return_value = -3  # another request took the last units
            mock_get.return_value = r
            result = rc.decrement_cached_inventory(WH_ID, PROD_ID, 5)
            assert result == -3  # caller must handle negative


class TestMetricsCache:
    def test_cache_and_retrieve_metrics(self):
        with patch("app.db.redis_client.get_redis") as mock_get:
            import json
            r = MagicMock()
            metrics = {"total_orders": 100, "dispatched": 95}
            r.get.return_value = json.dumps(metrics)
            mock_get.return_value = r
            result = rc.get_cached_metrics()
            assert result == metrics

    def test_get_metrics_returns_none_on_miss(self):
        with patch("app.db.redis_client.get_redis") as mock_get:
            r = MagicMock()
            r.get.return_value = None
            mock_get.return_value = r
            result = rc.get_cached_metrics()
            assert result is None

    def test_cache_metrics_writes_with_ttl(self):
        with patch("app.db.redis_client.get_redis") as mock_get:
            r = MagicMock()
            mock_get.return_value = r
            rc.cache_metrics({"total_orders": 10})
            r.setex.assert_called_once()
            args = r.setex.call_args[0]
            assert args[0] == "metrics:dashboard"
            assert args[1] == 10  # 10s TTL
