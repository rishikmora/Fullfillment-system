import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import time

from app.api.orders import router as orders_router
from app.api.warehouses import router as warehouses_router
from app.core.kafka_client import get_producer, stop_producer
from app.db.base import engine, Base

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)

    logger.info("Warming Kafka producer...")
    try:
        await get_producer()
    except Exception as e:
        logger.warning("Kafka not available at startup (ok for dev): %s", e)

    logger.info("Application startup complete")
    yield

    # Shutdown
    await stop_producer()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="Smart Order Fulfillment System",
    description=(
        "Distributed warehouse routing system with real-time inventory sync. "
        "Routes orders to the nearest warehouse with available stock using "
        "Haversine distance + Redis-cached inventory."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_timing_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
    logger.info(
        "%s %s → %d (%.1fms)",
        request.method, request.url.path, response.status_code, duration_ms,
    )
    return response


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(orders_router)
app.include_router(warehouses_router)


@app.get("/health", tags=["system"])
def health_check():
    return {"status": "ok", "service": "order-fulfillment"}
