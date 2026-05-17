"""
Tests for app/core/kafka_client.py
Covers: producer lifecycle, event publishing, error handling.
"""

import pytest
from unittest.mock import patch, AsyncMock
from aiokafka.errors import KafkaConnectionError

from app.core import kafka_client as kc


class TestKafkaProducer:
    @pytest.mark.asyncio
    async def test_publish_order_event_sends_to_correct_topic(self):
        mock_producer = AsyncMock()
        mock_producer.send_and_wait = AsyncMock(return_value=None)

        with patch("app.core.kafka_client.get_producer", return_value=mock_producer):
            event = {
                "event_type": "order_dispatched",
                "order_id": "ord-123",
                "warehouse_id": "wh-456",
            }
            await kc.publish_order_event(event)

        mock_producer.send_and_wait.assert_called_once()
        call_kwargs = mock_producer.send_and_wait.call_args
        assert call_kwargs[0][0] == kc.settings.kafka_order_topic

    @pytest.mark.asyncio
    async def test_publish_order_event_uses_order_id_as_key(self):
        mock_producer = AsyncMock()
        mock_producer.send_and_wait = AsyncMock(return_value=None)

        with patch("app.core.kafka_client.get_producer", return_value=mock_producer):
            await kc.publish_order_event({"order_id": "ord-999", "event_type": "test"})

        call_kwargs = mock_producer.send_and_wait.call_args[1]
        assert call_kwargs["key"] == b"ord-999"

    @pytest.mark.asyncio
    async def test_publish_order_event_raises_on_kafka_down(self):
        mock_producer = AsyncMock()
        mock_producer.send_and_wait = AsyncMock(side_effect=KafkaConnectionError("broker down"))

        with patch("app.core.kafka_client.get_producer", return_value=mock_producer):
            with pytest.raises(KafkaConnectionError):
                await kc.publish_order_event({"order_id": "ord-1", "event_type": "test"})

    @pytest.mark.asyncio
    async def test_publish_reorder_event_logs_and_sends(self):
        mock_producer = AsyncMock()
        mock_producer.send_and_wait = AsyncMock(return_value=None)

        with patch("app.core.kafka_client.get_producer", return_value=mock_producer):
            await kc.publish_reorder_event({
                "event_type": "reorder_triggered",
                "product_id": "prod-1",
                "warehouse_id": "wh-1",
            })

        mock_producer.send_and_wait.assert_called_once()
        topic = mock_producer.send_and_wait.call_args[0][0]
        assert topic == kc.settings.kafka_reorder_topic

    @pytest.mark.asyncio
    async def test_publish_reorder_swallows_kafka_error(self):
        mock_producer = AsyncMock()
        mock_producer.send_and_wait = AsyncMock(
            side_effect=KafkaConnectionError("broker unavailable")
        )
        with patch("app.core.kafka_client.get_producer", return_value=mock_producer):
            # Should not raise — reorder is best-effort
            await kc.publish_reorder_event({"event_type": "reorder_triggered", "product_id": "p1"})

    @pytest.mark.asyncio
    async def test_stop_producer_clears_global(self):
        mock_producer = AsyncMock()
        mock_producer.stop = AsyncMock()

        with patch.object(kc, "_producer", mock_producer):
            await kc.stop_producer()
            mock_producer.stop.assert_called_once()
            assert kc._producer is None
