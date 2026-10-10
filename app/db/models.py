"""Domain models for persisted notification events."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class NotificationStatus(StrEnum):
    """Lifecycle status of a booking notification."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


@dataclass(slots=True)
class NotificationRecord:
    """A single booking notification stored in SQLite."""

    id: int
    event_id: str
    event_type: str
    booking_id: str
    payload_json: str
    status: NotificationStatus
    attempts: int
    last_error: str | None
    next_attempt_at: datetime | None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "booking_id": self.booking_id,
            "status": self.status.value,
            "attempts": self.attempts,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }
