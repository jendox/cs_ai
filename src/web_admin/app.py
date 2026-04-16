import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from src.ai.amazon_mcp_client import AmazonMCPHttpClient
from src.ai.config import LLMRuntimeSettingsStorage
from src.ai.config.prompt import LLMPromptStorage
from src.ai.context import LLMContext
from src.ai.llm_clients.pool import LLMClientPool
from src.config import AppSettings
from src.db.sa import Database
from src.web_admin.bootstrap import bootstrap_superadmin
from src.web_admin.routes import router
from src.web_admin.templates import WEB_ADMIN_DIR, templates

logger = logging.getLogger("web_admin.app")


def _error_message(exc: HTTPException) -> str:
    if exc.status_code == status.HTTP_403_FORBIDDEN:
        return "You do not have permission to perform this action."
    if isinstance(exc.detail, str):
        return exc.detail
    return "The request could not be completed."


@asynccontextmanager
async def _setup_optional_mcp_client(url: str) -> AsyncIterator[AmazonMCPHttpClient]:
    try:
        async with AmazonMCPHttpClient.setup(url) as client:
            yield client
    except Exception as error:
        logger.warning("mcp.startup.failed", extra={"error": str(error)})
        async with AmazonMCPHttpClient.setup(url, connect=False) as client:
            yield client


def create_app(settings: AppSettings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
        async with (
            Database.lifespan(url=settings.postgres.url),
            _setup_optional_mcp_client(settings.mcp.url),
        ):
            amazon_mcp_client = AmazonMCPHttpClient.get_initialized_instance()
            app_.state.llm_context = LLMContext(
                client_pool=LLMClientPool(settings.llm),
                runtime_storage=LLMRuntimeSettingsStorage(),
                prompt_storage=LLMPromptStorage(),
                amazon_mcp_client=amazon_mcp_client,
            )
            await bootstrap_superadmin(settings.web)
            yield

    app = FastAPI(
        title="CS Web Admin",
        lifespan=lifespan,
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> Response:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return RedirectResponse(
                url="/admin/login",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "active_page": None,
                "current_user": None,
                "csrf_token": None,
                "flash": None,
                "status_code": exc.status_code,
                "message": _error_message(exc),
            },
            status_code=exc.status_code,
        )

    app.include_router(router, prefix="/admin")

    app.mount(
        "/admin/static",
        StaticFiles(directory=str(WEB_ADMIN_DIR / "static")),
        name="web_admin_static",
    )

    return app
