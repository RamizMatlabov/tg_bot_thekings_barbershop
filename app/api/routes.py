"""HTTP routes for health checks and booking webhooks."""

from __future__ import annotations

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.api.schemas import (
    BookingWebhookRequest,
    ErrorResponse,
    HealthResponse,
    WebhookAcceptedResponse,
)
from app.config import Settings, get_settings
from app.db.database import Database, get_database

logger = logging.getLogger(__name__)

router = APIRouter()


def verify_webhook_secret(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Authenticate webhook callers with a Bearer shared secret."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    provided = authorization.removeprefix("Bearer ").strip()
    expected = settings.webhook_secret
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={200: {"model": HealthResponse}},
)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.post(
    "/api/webhooks/bookings",
    response_model=WebhookAcceptedResponse,
    responses={
        200: {"model": WebhookAcceptedResponse},
        401: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    dependencies=[Depends(verify_webhook_secret)],
)
async def receive_booking_webhook(
    payload: BookingWebhookRequest,
    database: Annotated[Database, Depends(get_database)],
) -> WebhookAcceptedResponse:
    """
    Accept a booking event from the website backend.

    The event is persisted immediately. Telegram delivery happens in the
    background worker so transient Telegram outages do not lose events.
    """
    record, created = await database.create_notification(
        event_id=payload.event_id,
        event_type=payload.event_type,
        booking_id=payload.booking.id,
        payload=payload.model_dump(mode="json"),
    )

    if not created:
        logger.info(
            "Duplicate booking event ignored event_id=%s booking_id=%s",
            payload.event_id,
            payload.booking.id,
        )
        return WebhookAcceptedResponse(
            ok=True,
            event_id=payload.event_id,
            status="duplicate",
        )

    logger.info(
        "Booking event accepted event_id=%s event_type=%s booking_id=%s notification_id=%s",
        payload.event_id,
        payload.event_type,
        payload.booking.id,
        record.id if record else None,
    )
    return WebhookAcceptedResponse(
        ok=True,
        event_id=payload.event_id,
        status="accepted",
    )
