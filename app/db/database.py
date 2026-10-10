"""Async SQLite access for notification persistence."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from app.db.models import NotificationRecord, NotificationStatus

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    booking_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_attempt_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_notifications_status_next
    ON notifications (status, next_attempt_at);
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _from_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _row_to_record(row: aiosqlite.Row) -> NotificationRecord:
    return NotificationRecord(
        id=row["id"],
        event_id=row["event_id"],
        event_type=row["event_type"],
        booking_id=row["booking_id"],
        payload_json=row["payload_json"],
        status=NotificationStatus(row["status"]),
        attempts=row["attempts"],
        last_error=row["last_error"],
        next_attempt_at=_from_iso(row["next_attempt_at"]),
        created_at=_from_iso(row["created_at"]) or _utcnow(),
        updated_at=_from_iso(row["updated_at"]) or _utcnow(),
        sent_at=_from_iso(row["sent_at"]),
    )


class Database:
    """Thin async wrapper around SQLite for notification records."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.executescript(_CREATE_TABLE_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def create_notification(
        self,
        *,
        event_id: str,
        event_type: str,
        booking_id: str,
        payload: dict[str, Any],
    ) -> tuple[NotificationRecord | None, bool]:
        """
        Insert a pending notification.

        Returns (record, created). If event_id already exists, returns
        (existing_record, False) without sending a duplicate.
        """
        now = _utcnow()
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        async with self._lock:
            try:
                cursor = await self.connection.execute(
                    """
                    INSERT INTO notifications (
                        event_id, event_type, booking_id, payload_json,
                        status, attempts, last_error, next_attempt_at,
                        created_at, updated_at, sent_at
                    ) VALUES (?, ?, ?, ?, ?, 0, NULL, ?, ?, ?, NULL)
                    """,
                    (
                        event_id,
                        event_type,
                        booking_id,
                        payload_json,
                        NotificationStatus.PENDING.value,
                        _to_iso(now),
                        _to_iso(now),
                        _to_iso(now),
                    ),
                )
                await self.connection.commit()
                record = await self.get_by_id(cursor.lastrowid)
                return record, True
            except aiosqlite.IntegrityError:
                await self.connection.rollback()
                existing = await self.get_by_event_id(event_id)
                return existing, False

    async def get_by_id(self, notification_id: int | None) -> NotificationRecord | None:
        if notification_id is None:
            return None
        cursor = await self.connection.execute(
            "SELECT * FROM notifications WHERE id = ?",
            (notification_id,),
        )
        row = await cursor.fetchone()
        return _row_to_record(row) if row else None

    async def get_by_event_id(self, event_id: str) -> NotificationRecord | None:
        cursor = await self.connection.execute(
            "SELECT * FROM notifications WHERE event_id = ?",
            (event_id,),
        )
        row = await cursor.fetchone()
        return _row_to_record(row) if row else None

    async def claim_due_notifications(self, *, limit: int) -> list[NotificationRecord]:
        """
        Atomically claim due pending/retryable rows for processing.

        Uses a lock plus status transition to keep concurrent workers safe.
        """
        now = _utcnow()
        now_iso = _to_iso(now)
        claimed: list[NotificationRecord] = []

        async with self._lock:
            cursor = await self.connection.execute(
                """
                SELECT * FROM notifications
                WHERE status = ?
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (NotificationStatus.PENDING.value, now_iso, limit),
            )
            rows = await cursor.fetchall()
            for row in rows:
                record = _row_to_record(row)
                await self.connection.execute(
                    """
                    UPDATE notifications
                    SET status = ?, updated_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        NotificationStatus.SENDING.value,
                        now_iso,
                        record.id,
                        NotificationStatus.PENDING.value,
                    ),
                )
                claimed.append(
                    NotificationRecord(
                        id=record.id,
                        event_id=record.event_id,
                        event_type=record.event_type,
                        booking_id=record.booking_id,
                        payload_json=record.payload_json,
                        status=NotificationStatus.SENDING,
                        attempts=record.attempts,
                        last_error=record.last_error,
                        next_attempt_at=record.next_attempt_at,
                        created_at=record.created_at,
                        updated_at=now,
                        sent_at=record.sent_at,
                    )
                )
            await self.connection.commit()
        return claimed

    async def mark_sent(self, notification_id: int) -> None:
        now = _utcnow()
        await self.connection.execute(
            """
            UPDATE notifications
            SET status = ?, attempts = attempts + 1, last_error = NULL,
                next_attempt_at = NULL, updated_at = ?, sent_at = ?
            WHERE id = ?
            """,
            (
                NotificationStatus.SENT.value,
                _to_iso(now),
                _to_iso(now),
                notification_id,
            ),
        )
        await self.connection.commit()

    async def mark_retry(
        self,
        notification_id: int,
        *,
        error: str,
        delay_seconds: float,
    ) -> None:
        now = _utcnow()
        next_attempt = now + timedelta(seconds=delay_seconds)
        await self.connection.execute(
            """
            UPDATE notifications
            SET status = ?, attempts = attempts + 1, last_error = ?,
                next_attempt_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                NotificationStatus.PENDING.value,
                error[:1000],
                _to_iso(next_attempt),
                _to_iso(now),
                notification_id,
            ),
        )
        await self.connection.commit()

    async def mark_failed(self, notification_id: int, *, error: str) -> None:
        now = _utcnow()
        await self.connection.execute(
            """
            UPDATE notifications
            SET status = ?, attempts = attempts + 1, last_error = ?,
                next_attempt_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (
                NotificationStatus.FAILED.value,
                error[:1000],
                _to_iso(now),
                notification_id,
            ),
        )
        await self.connection.commit()

    async def requeue_stale_sending(self, *, older_than_seconds: int = 120) -> int:
        """Recover rows stuck in sending after a crash/restart."""
        cutoff = _utcnow() - timedelta(seconds=older_than_seconds)
        cursor = await self.connection.execute(
            """
            UPDATE notifications
            SET status = ?, updated_at = ?
            WHERE status = ? AND updated_at <= ?
            """,
            (
                NotificationStatus.PENDING.value,
                _to_iso(_utcnow()),
                NotificationStatus.SENDING.value,
                _to_iso(cutoff),
            ),
        )
        await self.connection.commit()
        return cursor.rowcount


_database: Database | None = None


def get_database() -> Database:
    if _database is None:
        raise RuntimeError("Database has not been initialized")
    return _database


def set_database(database: Database | None) -> None:
    global _database
    _database = database
