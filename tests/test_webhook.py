"""Webhook API tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.db.database import Database
from app.db.models import NotificationStatus


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_valid_webhook_accepted(
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
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "ok": True,
        "event_id": "evt_12345",
        "status": "accepted",
    }

    record = await database.get_by_event_id("evt_12345")
    assert record is not None
    assert record.status == NotificationStatus.PENDING
    assert record.booking_id == "12345"


@pytest.mark.asyncio
async def test_missing_authorization(
    client: AsyncClient,
    sample_booking_payload: dict,
) -> None:
    response = await client.post(
        "/api/webhooks/bookings",
        json=sample_booking_payload,
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_authorization(
    client: AsyncClient,
    sample_booking_payload: dict,
) -> None:
    response = await client.post(
        "/api/webhooks/bookings",
        json=sample_booking_payload,
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert response.status_code == 401
    assert "secret" not in response.text.lower() or "wrong" not in response.text.lower()


@pytest.mark.asyncio
async def test_missing_required_fields(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/webhooks/bookings",
        json={
            "event_id": "evt_missing",
            "event_type": "booking.created",
            "booking": {"id": "1"},
        },
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert response.json()["ok"] is False


@pytest.mark.asyncio
async def test_invalid_json(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/api/webhooks/bookings",
        content=b"{not-json",
        headers={**auth_headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unsupported_event_type(
    client: AsyncClient,
    auth_headers: dict[str, str],
    sample_booking_payload: dict,
) -> None:
    payload = dict(sample_booking_payload)
    payload["event_type"] = "booking.cancelled"
    response = await client.post(
        "/api/webhooks/bookings",
        json=payload,
        headers=auth_headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_event_id(
    client: AsyncClient,
    auth_headers: dict[str, str],
    sample_booking_payload: dict,
    database: Database,
) -> None:
    first = await client.post(
        "/api/webhooks/bookings",
        json=sample_booking_payload,
        headers=auth_headers,
    )
    second = await client.post(
        "/api/webhooks/bookings",
        json=sample_booking_payload,
        headers=auth_headers,
    )
    assert first.json()["status"] == "accepted"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    # Still only one persisted row
    record = await database.get_by_event_id("evt_12345")
    assert record is not None
    cursor = await database.connection.execute("SELECT COUNT(*) AS c FROM notifications")
    row = await cursor.fetchone()
    assert row["c"] == 1


@pytest.mark.asyncio
async def test_optional_fields_accepted(
    client: AsyncClient,
    auth_headers: dict[str, str],
    database: Database,
) -> None:
    response = await client.post(
        "/api/webhooks/bookings",
        json={
            "event_id": "evt_minimal",
            "event_type": "booking.confirmed",
            "booking": {
                "id": "99",
                "customer_name": "Minimal Customer",
            },
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    record = await database.get_by_event_id("evt_minimal")
    assert record is not None
    assert record.status == NotificationStatus.PENDING
