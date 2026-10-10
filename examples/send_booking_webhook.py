"""
Example: how a website backend can notify this bot after a booking is saved.

Run from the website server (never from the browser):

    export BOOKING_BOT_WEBHOOK_URL=https://bot.example.com/api/webhooks/bookings
    export BOOKING_BOT_WEBHOOK_SECRET=your_shared_secret
    python examples/send_booking_webhook.py
"""

from __future__ import annotations

import os
import sys
import uuid

import httpx

WEBHOOK_URL = os.environ.get(
    "BOOKING_BOT_WEBHOOK_URL",
    "http://127.0.0.1:8000/api/webhooks/bookings",
)
WEBHOOK_SECRET = os.environ.get("BOOKING_BOT_WEBHOOK_SECRET", "")


def notify_booking_created(booking: dict) -> dict:
    if not WEBHOOK_SECRET:
        raise RuntimeError("BOOKING_BOT_WEBHOOK_SECRET is not set")

    payload = {
        "event_id": f"evt_{booking['id']}_{uuid.uuid4().hex[:8]}",
        "event_type": "booking.created",
        "booking": booking,
    }

    # Use a stable event_id derived from booking id if your DB has one unique id:
    # payload["event_id"] = f"booking_created_{booking['id']}"

    with httpx.Client(timeout=5.0) as client:
        for attempt in range(3):
            try:
                response = client.post(
                    WEBHOOK_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {WEBHOOK_SECRET}"},
                )
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == 2:
                    raise
    raise RuntimeError("unreachable")


if __name__ == "__main__":
    result = notify_booking_created(
        {
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
        }
    )
    print(result)
    sys.exit(0 if result.get("ok") else 1)
