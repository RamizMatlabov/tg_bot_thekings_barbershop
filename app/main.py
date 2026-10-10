"""Application entrypoint: FastAPI webhook API + Telegram bot + worker."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.api.routes import router
from app.bot import create_dispatcher, start_bot_polling, stop_bot
from app.config import Settings, get_settings
from app.db.database import Database, set_database
from app.services.notifications import NotificationService, create_bot
from app.workers.notification_worker import NotificationWorker

logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # Avoid leaking credentials via third-party HTTP debug logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject oversized request bodies early."""

    def __init__(self, app: FastAPI, max_body_size: int) -> None:
        super().__init__(app)
        self.max_body_size = max_body_size

    async def dispatch(self, request: Request, call_next) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit():
            if int(content_length) > self.max_body_size:
                return JSONResponse(
                    status_code=413,
                    content={"ok": False, "error": "Request body too large"},
                )
        return await call_next(request)


def _build_lifespan(*, run_background_services: bool):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings: Settings = app.state.settings
        configure_logging(settings.log_level)

        database = Database(settings.sqlite_path)
        await database.connect()
        set_database(database)
        app.state.database = database

        bot = create_bot(settings.telegram_bot_token)
        dispatcher = create_dispatcher()
        notification_service = NotificationService(
            bot=bot,
            chat_id=settings.telegram_chat_id,
            timezone_name=settings.timezone,
        )
        worker = NotificationWorker(
            database=database,
            notification_service=notification_service,
            settings=settings,
        )

        app.state.bot = bot
        app.state.dispatcher = dispatcher
        app.state.notification_service = notification_service
        app.state.worker = worker
        app.state.polling_task = None

        if run_background_services:
            await worker.start()
            polling_task = asyncio.create_task(
                start_bot_polling(bot, dispatcher),
                name="telegram-polling",
            )
            app.state.polling_task = polling_task

        logger.info("Application started")

        try:
            yield
        finally:
            logger.info("Application shutting down")
            if run_background_services:
                await worker.stop()
                polling_task = app.state.polling_task
                if polling_task is not None:
                    polling_task.cancel()
                    try:
                        await polling_task
                    except asyncio.CancelledError:
                        pass
                await dispatcher.stop_polling()
            await stop_bot(bot)
            await database.close()
            set_database(None)
            logger.info("Application shutdown complete")

    return lifespan


def create_app(
    settings: Settings | None = None,
    *,
    run_background_services: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Barbershop Booking Notification Bot",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_build_lifespan(run_background_services=run_background_services),
    )
    app.state.settings = settings
    app.add_middleware(MaxBodySizeMiddleware, max_body_size=settings.max_request_body_bytes)
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        # Avoid echoing raw payload contents that may include PII
        logger.warning("Validation error on %s", request.url.path)
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "error": "Validation error",
                "detail": "Invalid request payload",
            },
        )

    return app


app = create_app()
