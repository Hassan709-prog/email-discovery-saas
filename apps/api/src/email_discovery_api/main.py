"""FastAPI application initialization and lifespan management."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from email_discovery_api.api.errors import register_error_handlers
from email_discovery_api.api.router import api_router
from email_discovery_api.config import Settings, get_settings
from email_discovery_api.database import DatabaseManager
from email_discovery_api.logging import RequestIdMiddleware, setup_logging
from email_discovery_api.redis import APIRedisClient


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory initializing FastAPI instance with lifecycle and middleware."""
    app_settings = settings or get_settings()

    if (
        app_settings.allow_dev_identity_headers
        and app_settings.environment.lower() != "development"
    ):
        raise ValueError(
            "ALLOW_DEV_IDENTITY_HEADERS cannot be enabled outside development environment."
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Application startup
        setup_logging(app_settings.log_level)
        db_manager = getattr(app.state, "db_manager", None) or DatabaseManager(app_settings)
        redis_manager = getattr(app.state, "redis_manager", None) or APIRedisClient(app_settings)
        app.state.db_manager = db_manager
        app.state.redis_manager = redis_manager

        await redis_manager.start()

        yield

        # Application shutdown
        if app.state.redis_manager is not None:
            await app.state.redis_manager.close()
        if app.state.db_manager is not None:
            await app.state.db_manager.close()

    app = FastAPI(
        title=app_settings.app_name,
        debug=app_settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.db_manager = None
    app.state.redis_manager = None

    # Attach request ID and structured logging middleware
    app.add_middleware(RequestIdMiddleware)

    # Register error response envelope handlers
    register_error_handlers(app)

    # Include API routes
    app.include_router(api_router)

    return app


# Default application instance for Uvicorn and deployment entry point
app = create_app()
