"""Booking notification formatting and Telegram delivery."""

from __future__ import annotations

import html
import logging
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.client.default import DefaultBotProperties

from app.api.schemas import BookingPayload

logger = logging.getLogger(__name__)

MONTH_NAMES = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}

TRANSIENT_TELEGRAM_ERRORS = (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TimeoutError,
    OSError,
)


def escape_html(value: object) -> str:
    """Escape text for Telegram HTML parse mode."""
    return html.escape(str(value), quote=False)


def format_price(price: Decimal | float | int | None, currency: str | None) -> str | None:
    if price is None:
        return None
    try:
        amount = Decimal(str(price))
    except (InvalidOperation, ValueError):
        return None

    if amount == amount.to_integral_value():
        formatted = f"{int(amount):,}".replace(",", " ")
    else:
        formatted = f"{amount:,.2f}".replace(",", " ")

    code = (currency or "UZS").upper()
    return f"{formatted} {code}"


def format_booking_date(value: date | datetime | None, tz_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        try:
            tz = ZoneInfo(tz_name)
            local = value if value.tzinfo else value.replace(tzinfo=tz)
            local = local.astimezone(tz)
            value = local.date()
        except Exception:
            value = value.date()
    month = MONTH_NAMES.get(value.month, str(value.month))
    return f"{value.day} {month} {value.year}"


def format_booking_time(value: time | datetime | None, tz_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        try:
            tz = ZoneInfo(tz_name)
            local = value if value.tzinfo else value.replace(tzinfo=tz)
            local = local.astimezone(tz)
            value = local.time().replace(microsecond=0)
        except Exception:
            value = value.time().replace(microsecond=0)
    return value.strftime("%H:%M")


def format_status(status: str | None) -> str | None:
    if not status:
        return None
    cleaned = status.replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else None


def build_booking_message(booking: BookingPayload | dict[str, Any], *, timezone_name: str) -> str:
    """Build a concise HTML Telegram notification for a booking."""
    if isinstance(booking, dict):
        booking = BookingPayload.model_validate(booking)

    lines: list[str] = [
        "✂️ <b>NEW BARBERSHOP BOOKING</b>",
        "",
        f"📋 Booking ID: #{escape_html(booking.id)}",
        "",
        f"👤 Customer: {escape_html(booking.customer_name)}",
    ]

    if booking.customer_phone:
        lines.append(f"📞 Phone: {escape_html(booking.customer_phone)}")
    if booking.barber_name:
        lines.append(f"💈 Barber: {escape_html(booking.barber_name)}")
    if booking.service_name:
        lines.append(f"✂️ Service: {escape_html(booking.service_name)}")

    formatted_date = format_booking_date(booking.date, timezone_name)
    if formatted_date:
        lines.append(f"📅 Date: {escape_html(formatted_date)}")

    formatted_time = format_booking_time(booking.time, timezone_name)
    if formatted_time:
        lines.append(f"🕒 Time: {escape_html(formatted_time)}")

    price_text = format_price(booking.price, booking.currency)
    if price_text:
        lines.append(f"💰 Price: {escape_html(price_text)}")

    status_text = format_status(booking.status)
    if status_text:
        lines.extend(["", f"📌 Status: {escape_html(status_text)}"])

    return "\n".join(lines)


class NotificationService:
    """Sends booking notifications to the configured Telegram chat only."""

    def __init__(
        self,
        *,
        bot: Bot,
        chat_id: str,
        timezone_name: str = "Asia/Tashkent",
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._timezone_name = timezone_name

    async def send_booking_notification(
        self,
        booking: BookingPayload | dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None:
        text = build_booking_message(booking, timezone_name=self._timezone_name)
        logger.info(
            "Sending booking notification event_id=%s booking_id=%s",
            event_id,
            booking.id if isinstance(booking, BookingPayload) else booking.get("id"),
        )
        await self._bot.send_message(
            chat_id=self._chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


def create_bot(token: str) -> Bot:
    return Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
