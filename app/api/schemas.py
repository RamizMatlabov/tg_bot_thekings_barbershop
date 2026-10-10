"""Pydantic schemas for webhook requests and responses."""

from __future__ import annotations

from datetime import date as Date
from datetime import time as Time
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUPPORTED_EVENT_TYPES = frozenset(
    {
        "booking.created",
        "booking.confirmed",
    }
)

EventType = Literal["booking.created", "booking.confirmed"]


class BookingPayload(BaseModel):
    """Booking details supplied by the website backend."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: Annotated[str, Field(min_length=1, max_length=128)]
    customer_name: Annotated[str, Field(min_length=1, max_length=200)]
    customer_phone: Annotated[str | None, Field(default=None, max_length=64)] = None
    service_name: Annotated[str | None, Field(default=None, max_length=200)] = None
    barber_name: Annotated[str | None, Field(default=None, max_length=200)] = None
    date: Date | None = None
    time: Time | None = None
    price: Decimal | float | int | None = None
    currency: Annotated[str | None, Field(default=None, max_length=16)] = None
    status: Annotated[str | None, Field(default=None, max_length=64)] = None

    @field_validator("customer_phone", "service_name", "barber_name", "currency", "status", mode="before")
    @classmethod
    def empty_str_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("id", "customer_name", mode="before")
    @classmethod
    def require_non_empty(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("must not be empty")
        return value


class BookingWebhookRequest(BaseModel):
    """Inbound booking event from the website."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    event_id: Annotated[str, Field(min_length=1, max_length=128)]
    event_type: EventType
    booking: BookingPayload

    @field_validator("event_id", mode="before")
    @classmethod
    def require_event_id(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("must not be empty")
        return value


class WebhookAcceptedResponse(BaseModel):
    ok: bool = True
    event_id: str
    status: Literal["accepted", "duplicate"]


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    service: str = "barbershop-booking-bot"
