"""Notification formatting, delivery, and retry tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from httpx import AsyncClient

from app.api.schemas import BookingPayload
from app.db.database import Database
from app.db.models import NotificationStatus
from app.services.notifications import build_booking_message, escape_html, format_price
from app.workers.notification_worker import NotificationWorker


def test_html_escaping_of_user_provided_text() -> None:
    booking = BookingPayload(
        id="1<script>",
        customer_name='John <b>Smith</b> & "Co"',
        customer_phone="+998901234567",
        service_name="Cut & Style",
        barber_name="<Aziz>",
        status="confirmed",
    )
    message = build_booking_message(booking, timezone_name="Asia/Tashkent")
    assert "<script>" not in message
    assert escape_html('John <b>Smith</b> & "Co"') in message
    assert "&lt;b&gt;" in message or "&amp;" in message
    assert "<b>Smith</b>" not in message
    assert "&lt;Aziz&gt;" in message
    assert "Cut &amp; Style" in message


def test_optional_fields_omitted_from_message() -> None:
    booking = BookingPayload(id="42", customer_name="Only Name")
    message = build_booking_message(booking, timezone_name="Asia/Tashkent")
    assert "Booking ID: #42" in message
    assert "Customer: Only Name" in message
    assert "Phone:" not in message
    assert "Barber:" not in message
    assert "Service:" not in message
    assert "Date:" not in message
    assert "Time:" not in message
    assert "Price:" not in message
    assert "Status:" not in message


def test_full_message_formatting() -> None:
    booking = BookingPayload(
        id="12345",
        customer_name="John Smith",
        customer_phone="+998901234567",
        service_name="Haircut",
        barber_name="Aziz",
        date="2026-10-15",
        time="14:30",
        price=100000,
        currency="UZS",
        status="confirmed",
    )
    message = build_booking_message(booking, timezone_name="Asia/Tashkent")
    assert "NEW BARBERSHOP BOOKING" in message
    assert "Booking ID: #12345" in message
    assert "Customer: John Smith" in message
    assert "Phone: +998901234567" in message
    assert "Barber: Aziz" in message
    assert "Service: Haircut" in message
    assert "Date: 15 October 2026" in message
    assert "Time: 14:30" in message
    assert "Price: 100 000 UZS" in message
    assert "Status: Confirmed" in message


def test_format_price_other_currency() -> None:
    assert format_price(25.5, "USD") == "25.50 USD"


@pytest.mark.asyncio
async def test_notification_sent_and_persisted(
    app,
    client: AsyncClient,
    auth_headers: dict[str, str],
    sample_booking_payload: dict,
    database: Database,
) -> None:
    response = await client.post(
        "/api/webhooks/bookings",
        json=sample_booking_payload,
        headers=auth_headers,
    )
    assert response.json()["status"] == "accepted"

    worker: NotificationWorker = app.state.worker
    with patch.object(
        app.state.notification_service,
        "send_booking_notification",
        new=AsyncMock(),
    ) as send_mock:
        processed = await worker.process_due_batch()
        assert processed == 1
        send_mock.assert_awaited_once()

    record = await database.get_by_event_id("evt_12345")
    assert record is not None
    assert record.status == NotificationStatus.SENT
    assert record.sent_at is not None
    assert record.attempts == 1


@pytest.mark.asyncio
async def test_telegram_failure_retries_then_fails(
    app,
    client: AsyncClient,
    auth_headers: dict[str, str],
    sample_booking_payload: dict,
    database: Database,
) -> None:
    sample_booking_payload = dict(sample_booking_payload)
    sample_booking_payload["event_id"] = "evt_retry"

    await client.post(
        "/api/webhooks/bookings",
        json=sample_booking_payload,
        headers=auth_headers,
    )

    worker: NotificationWorker = app.state.worker
    failing = AsyncMock(side_effect=TelegramNetworkError(method=AsyncMock(), message="down"))

    with patch.object(app.state.notification_service, "send_booking_notification", new=failing):
        # Attempt 1 -> pending retry
        await worker.process_due_batch()
        record = await database.get_by_event_id("evt_retry")
        assert record is not None
        assert record.status == NotificationStatus.PENDING
        assert record.attempts == 1
        assert record.last_error is not None

        # Force next_attempt_at to be due
        await database.connection.execute(
            "UPDATE notifications SET next_attempt_at = NULL WHERE event_id = ?",
            ("evt_retry",),
        )
        await database.connection.commit()

        # Attempt 2 -> pending retry
        await worker.process_due_batch()
        record = await database.get_by_event_id("evt_retry")
        assert record.status == NotificationStatus.PENDING
        assert record.attempts == 2

        await database.connection.execute(
            "UPDATE notifications SET next_attempt_at = NULL WHERE event_id = ?",
            ("evt_retry",),
        )
        await database.connection.commit()

        # Attempt 3 reaches max -> failed
        await worker.process_due_batch()
        record = await database.get_by_event_id("evt_retry")
        assert record.status == NotificationStatus.FAILED
        assert record.attempts == 3
        assert record.sent_at is None

    assert failing.await_count == 3


@pytest.mark.asyncio
async def test_telegram_retry_after_uses_suggested_delay(
    app,
    client: AsyncClient,
    auth_headers: dict[str, str],
    sample_booking_payload: dict,
    database: Database,
) -> None:
    sample_booking_payload = dict(sample_booking_payload)
    sample_booking_payload["event_id"] = "evt_flood"

    await client.post(
        "/api/webhooks/bookings",
        json=sample_booking_payload,
        headers=auth_headers,
    )

    worker: NotificationWorker = app.state.worker
    exc = TelegramRetryAfter(method=AsyncMock(), message="flood", retry_after=42)
    with patch.object(
        app.state.notification_service,
        "send_booking_notification",
        new=AsyncMock(side_effect=exc),
    ):
        await worker.process_due_batch()

    record = await database.get_by_event_id("evt_flood")
    assert record is not None
    assert record.status == NotificationStatus.PENDING
    assert record.next_attempt_at is not None


@pytest.mark.asyncio
async def test_not_marked_sent_until_telegram_succeeds(
    app,
    client: AsyncClient,
    auth_headers: dict[str, str],
    sample_booking_payload: dict,
    database: Database,
) -> None:
    sample_booking_payload = dict(sample_booking_payload)
    sample_booking_payload["event_id"] = "evt_partial"

    await client.post(
        "/api/webhooks/bookings",
        json=sample_booking_payload,
        headers=auth_headers,
    )

    worker: NotificationWorker = app.state.worker
    with patch.object(
        app.state.notification_service,
        "send_booking_notification",
        new=AsyncMock(side_effect=TelegramNetworkError(method=AsyncMock(), message="timeout")),
    ):
        await worker.process_due_batch()

    record = await database.get_by_event_id("evt_partial")
    assert record is not None
    assert record.status != NotificationStatus.SENT
    assert record.sent_at is None
