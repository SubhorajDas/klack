"""Application construction and process lifecycle wiring."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.responses import HTMLResponse

from klack.api.health import router as health_router
from klack.api.router import create_api_router
from klack.api.swagger import development_swagger_ui_html
from klack.core.config import AppEnvironment, Settings
from klack.core.container import build_container
from klack.core.db.session import DatabaseHealthCheck
from klack.core.errors import unhandled_exception_handler
from klack.core.logging import configure_logging
from klack.core.middleware.request_context import RequestContextMiddleware
from klack.core.problems import request_validation_exception_handler
from klack.modules.channels.api.errors import channel_exception_handler
from klack.modules.channels.domain.errors import ChannelError
from klack.modules.identity.api.errors import identity_exception_handler
from klack.modules.identity.domain.errors import IdentityError
from klack.modules.messaging.api.errors import messaging_exception_handler
from klack.modules.messaging.domain.errors import MessagingError
from klack.modules.workspaces.api.errors import workspace_exception_handler
from klack.modules.workspaces.domain.errors import WorkspaceError


def create_app(
    settings: Settings | None = None,
    *,
    database_health_check: DatabaseHealthCheck | None = None,
) -> FastAPI:
    """Build a fully configured FastAPI application without connecting to dependencies."""
    resolved_settings = settings or Settings()  # type: ignore[call-arg]
    configure_logging(resolved_settings)
    logger = structlog.get_logger(__name__)
    container = build_container(
        resolved_settings,
        database_health_check=database_health_check,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info("application_started")
        try:
            yield
        finally:
            await container.engine.dispose()
            logger.info("application_stopped")

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        debug=resolved_settings.app_debug,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
    )
    app.state.container = container
    app.add_middleware(RequestContextMiddleware)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    app.add_exception_handler(IdentityError, identity_exception_handler)
    app.add_exception_handler(WorkspaceError, workspace_exception_handler)
    app.add_exception_handler(ChannelError, channel_exception_handler)
    app.add_exception_handler(MessagingError, messaging_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(health_router)
    app.include_router(create_api_router(prefix=resolved_settings.api_v1_prefix))
    if resolved_settings.app_env is AppEnvironment.DEVELOPMENT:

        @app.get("/docs", include_in_schema=False)
        async def development_swagger() -> HTMLResponse:
            """Serve interactive documentation only in the local development environment."""
            return development_swagger_ui_html(
                openapi_url=app.openapi_url or "/openapi.json",
                api_prefix=resolved_settings.api_v1_prefix,
                title=f"{resolved_settings.app_name} - Swagger UI",
            )

    return app
