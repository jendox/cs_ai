from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.config import AppSettings
from src.db.sa import Database
from src.web_admin.bootstrap import bootstrap_superadmin
from src.web_admin.routes import router
from src.web_admin.templates import WEB_ADMIN_DIR


def create_app(settings: AppSettings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
        async with Database.lifespan(url=settings.postgres.url):
            await bootstrap_superadmin(settings.web)
            yield

    app = FastAPI(
        title="CS Web Admin",
        lifespan=lifespan,
    )
    app.include_router(router, prefix="/admin")

    app.mount(
        "/admin/static",
        StaticFiles(directory=str(WEB_ADMIN_DIR / "static")),
        name="web_admin_static",
    )

    return app
