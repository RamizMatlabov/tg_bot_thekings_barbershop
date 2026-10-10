"""Telegram bot setup for internal admin notifications."""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

logger = logging.getLogger(__name__)

router = Router(name="admin")


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    """Explain that this bot is for internal booking notifications only."""
    await message.answer(
        "<b>The Kings Barbershop — Booking Notifications</b>\n\n"
        "This bot delivers internal appointment alerts from the website "
        "booking system.\n\n"
        "Customers cannot book appointments here. "
        "Notifications are sent only to the configured staff chat.",
        disable_web_page_preview=True,
    )


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


async def start_bot_polling(bot: Bot, dispatcher: Dispatcher) -> None:
    logger.info("Starting Telegram bot polling")
    await dispatcher.start_polling(bot, handle_signals=False)


async def stop_bot(bot: Bot) -> None:
    await bot.session.close()
    logger.info("Telegram bot session closed")
