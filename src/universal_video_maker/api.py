from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from structlog.contextvars import bound_contextvars

from universal_video_maker import __version__
from universal_video_maker.config import Settings, get_settings
from universal_video_maker.errors import ApplicationError
from universal_video_maker.health import router as health_router
from universal_video_maker.identity import (
    KeycloakTokenVerifier,
    TokenVerifier,
)
from universal_video_maker.identity import router as auth_router
from universal_video_maker.logging_config import configure_logging


def create_app(
    settings: Settings | None = None,
    *,
    token_verifier: TokenVerifier | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    logger = structlog.get_logger(__name__)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ready = True
        logger.info("application_started", environment=settings.environment)
        try:
            yield
        finally:
            app.state.ready = False
            logger.info("application_stopped")

    app = FastAPI(
        title="Universal Video Maker Control Plane",
        version=__version__,
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        openapi_url="/openapi.json" if settings.is_development else None,
        lifespan=lifespan,
    )
    app.state.ready = False
    app.state.token_verifier = token_verifier or KeycloakTokenVerifier.from_settings(settings)

    @app.middleware("http")
    async def add_request_context(request: Request, call_next):
        request_id = str(uuid4())
        request.state.request_id = request_id
        with bound_contextvars(request_id=request_id):
            response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(ApplicationError)
    async def handle_application_error(
        request: Request,
        error: ApplicationError,
    ) -> JSONResponse:
        logger.warning(
            "application_error",
            code=error.code,
            status_code=error.status_code,
            request_id=getattr(request.state, "request_id", None),
        )
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    app.include_router(health_router)
    app.include_router(auth_router, prefix=settings.api_prefix)
    return app


app = create_app()
