import json
import logging
from typing import Callable, Awaitable
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from aiokafka.errors import KafkaConnectionError

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_producer: AIOKafkaProducer | None = None


async def get_producer() -> AIOKafkaProducer:
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            acks="all",           # wait for all replicas — durability guarantee
            enable_idempotence=True,  # exactly-once semantics
            compression_type="gzip",
        )
        await _producer.start()
        logger.info("Kafka producer started")
    return _producer


async def stop_producer() -> None:
    global _producer
    if _producer:
        await _producer.stop()
        _producer = None
        logger.info("Kafka producer stopped")


async def publish_order_event(event: dict) -> None:
    """
    Publishes an order event to Kafka.
    Key = order_id ensures all events for one order go to the same partition,
    preserving per-order event ordering.
    """
    try:
        producer = await get_producer()
        key = event.get("order_id", "unknown").encode("utf-8")
        await producer.send_and_wait(
            settings.kafka_order_topic,
            value=event,
            key=key,
        )
        logger.debug("Published order event: %s", event.get("order_id"))
    except KafkaConnectionError as e:
        logger.error("Kafka unavailable, order event dropped: %s", e)
        raise


async def publish_reorder_event(event: dict) -> None:
    try:
        producer = await get_producer()
        await producer.send_and_wait(settings.kafka_reorder_topic, value=event)
        logger.info("Reorder event published for product %s", event.get("product_id"))
    except KafkaConnectionError as e:
        logger.error("Kafka reorder publish failed: %s", e)


async def consume_order_events(
    handler: Callable[[dict], Awaitable[None]],
    group_id: str = "fulfillment-workers",
) -> None:
    """
    Long-running Kafka consumer. Calls handler for every event.
    Designed to run as a background asyncio task.
    """
    consumer = AIOKafkaConsumer(
        settings.kafka_order_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,   # manual commit after processing
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
    )
    await consumer.start()
    logger.info("Kafka consumer started on topic: %s", settings.kafka_order_topic)
    try:
        async for msg in consumer:
            try:
                await handler(msg.value)
                await consumer.commit()
            except Exception as e:
                logger.error("Error processing Kafka message: %s", e)
    finally:
        await consumer.stop()
