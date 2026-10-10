"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure settings can load before app imports during collection.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST_TOKEN_FOR_UNIT_TESTS")
os.environ.setdefault("TELEGRAM_CHAT_ID", "10001")
os.environ.setdefault("WEBHOOK_SECRET", "test-webhook-secret-value")
os.environ.setdefault("TIMEZONE", "Asia/Tashkent")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from app.config import Settings, get_settings
from app.db.database import Database, get_database, set_database
from app.main import create_app
from app.services.notifications import NotificationService, create_bot
from app.workers.notification_worker import NotificationWorker


@pytest.fixture
def webhook_secret() -> str:
    return "test-webhook-secret-value"


@pytest.fixture
def settings(tmp_path: Path, webhook_secret: str) -> Settings:
    get_settings.cache_clear()
    db_path = tmp_path / "test.db"
    return Settings(
        telegram_bot_token="123456:TEST_TOKEN_FOR_UNIT_TESTS",
        telegram_chat_id="10001",
        webhook_secret=webhook_secret,
        database_url=f"sqlite:///{db_path.as_posix()}",
        timezone="Asia/Tashkent",
        log_level="WARNING",
        notification_max_attempts=3,
        notification_base_backoff_seconds=0.01,
        notification_worker_poll_interval_seconds=0.05,
    )


@pytest.fixture
async def database(settings: Settings) -> AsyncIterator[Database]:
    db = Database(settings.sqlite_path)
    await db.connect()
    set_database(db)
    try:
        yield db
    finally:
        await db.close()
        set_database(None)


@pytest.fixture
def sample_booking_payload() -> dict:
    return {
        "event_id": "evt_12345",
        "event_type": "booking.created",
        "booking": {
            "id": "12345",
            "customer_name": "John Smith",
            "customer_phone": "+998901234567",
            "service_name": "Haircut",
            "barber_name": "Aziz",
            "date": "2026-10-15",
            "time": "14:30",
            "price": 100000,
            "currency": "UZS",
            "status": "confirmed",
        },
    }


@pytest.fixture
async def app(settings: Settings, database: Database):
    application = create_app(settings, run_background_services=False)
    application.state.settings = settings
    application.state.database = database
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_database] = lambda: database

    bot = create_bot(settings.telegram_bot_token)
    notification_service = NotificationService(
        bot=bot,
        chat_id=settings.telegram_chat_id,
        timezone_name=settings.timezone,
    )
    worker = NotificationWorker(
        database=database,
        notification_service=notification_service,
        settings=settings,
    )
    application.state.bot = bot
    application.state.notification_service = notification_service
    application.state.worker = worker

    yield application

    application.dependency_overrides.clear()
    await bot.session.close()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.fixture
def auth_headers(webhook_secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {webhook_secret}"}
