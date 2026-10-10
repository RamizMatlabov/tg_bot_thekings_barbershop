"""Persistent notification delivery worker with exponential backoff."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from app.db.models import NotificationStatus
from app.services.notifications import TRANSIENT_TELEGRAM_ERRORS, NotificationService

if TYPE_CHECKING:
    from app.config import Settings
    from app.db.database import Database

logger = logging.getLogger(__name__)


class NotificationWorker:
    """
    Polls SQLite for pending notifications and delivers them via Telegram.

    Jobs survive process restarts because state is persisted in the database.
    """

    def __init__(
        self,
        *,
        database: Database,
        notification_service: NotificationService,
        settings: Settings,
    ) -> None:
        self._db = database
        self._notifications = notification_service
        self._settings = settings
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        recovered = await self._db.requeue_stale_sending(older_than_seconds=0)
        if recovered:
            logger.info("Requeued %s stale sending notification(s) on startup", recovered)
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="notification-worker")
        logger.info("Notification worker started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            self._task = None
        logger.info("Notification worker stopped")

    async def _run(self) -> None:
        poll = self._settings.notification_worker_poll_interval_seconds
        while not self._stop_event.is_set():
            try:
                processed = await self.process_due_batch()
                if processed == 0:
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=poll)
                    except asyncio.TimeoutError:
                        pass
            except Exception:
                logger.exception("Notification worker loop error")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=poll)
                except asyncio.TimeoutError:
                    pass

    async def process_due_batch(self) -> int:
        """Process one batch of due notifications. Returns number claimed."""
        due = await self._db.claim_due_notifications(
            limit=self._settings.notification_batch_size,
        )
        for record in due:
            await self._deliver(record.id, record.event_id, record.payload_json, record.attempts)
        return len(due)

    async def _deliver(
        self,
        notification_id: int,
        event_id: str,
        payload_json: str,
        attempts_so_far: int,
    ) -> None:
        try:
            payload = json.loads(payload_json)
            booking = payload.get("booking", payload)
            await self._notifications.send_booking_notification(
                booking,
                event_id=event_id,
            )
        except TRANSIENT_TELEGRAM_ERRORS as exc:
            next_attempt = attempts_so_far + 1
            max_attempts = self._settings.notification_max_attempts
            if next_attempt >= max_attempts:
                logger.error(
                    "Permanently failed notification event_id=%s after %s attempts: %s",
                    event_id,
                    next_attempt,
                    type(exc).__name__,
                )
                await self._db.mark_failed(
                    notification_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return

            delay = self._compute_backoff(next_attempt, exc)
            logger.warning(
                "Transient Telegram failure event_id=%s attempt=%s/%s retry_in=%.1fs error=%s",
                event_id,
                next_attempt,
                max_attempts,
                delay,
                type(exc).__name__,
            )
            await self._db.mark_retry(
                notification_id,
                error=f"{type(exc).__name__}: {exc}",
                delay_seconds=delay,
            )
            return
        except (TelegramAPIError, ValueError, KeyError, json.JSONDecodeError) as exc:
            logger.error(
                "Non-retryable notification failure event_id=%s error=%s",
                event_id,
                type(exc).__name__,
            )
            await self._db.mark_failed(
                notification_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            return
        except Exception as exc:
            logger.exception("Unexpected notification failure event_id=%s", event_id)
            next_attempt = attempts_so_far + 1
            if next_attempt >= self._settings.notification_max_attempts:
                await self._db.mark_failed(
                    notification_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
            else:
                delay = self._compute_backoff(next_attempt, exc)
                await self._db.mark_retry(
                    notification_id,
                    error=f"{type(exc).__name__}: {exc}",
                    delay_seconds=delay,
                )
            return

        await self._db.mark_sent(notification_id)
        logger.info(
            "Notification delivered event_id=%s status=%s",
            event_id,
            NotificationStatus.SENT.value,
        )

    def _compute_backoff(self, attempt: int, exc: BaseException) -> float:
        if isinstance(exc, TelegramRetryAfter):
            return float(getattr(exc, "retry_after", self._settings.notification_base_backoff_seconds))
        base = self._settings.notification_base_backoff_seconds
        # attempt is 1-based after the failed try
        return min(base * (2 ** max(attempt - 1, 0)), 300.0)
