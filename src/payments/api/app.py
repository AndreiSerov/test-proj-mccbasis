from fastapi import APIRouter, FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from payments.api.auth import ApiKeyAuth
from payments.api.payments import router as payments_router
from payments.core.logging import configure_logging
from payments.core.settings import Settings, load_settings


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="payments", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    # create_async_engine connects lazily, so /health works without a database.
    app.state.engine = create_async_engine(settings.database_url)
    app.state.session_factory = async_sessionmaker(
        app.state.engine, expire_on_commit=False
    )
    app.include_router(payments_router)
    app.include_router(_docs_router(app))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def app_factory() -> FastAPI:
    configure_logging()
    return create_app(load_settings())


def _docs_router(app: FastAPI) -> APIRouter:
    """OpenAPI schema and UIs behind X-API-Key, like every route but /health."""
    router = APIRouter(dependencies=[ApiKeyAuth], include_in_schema=False)

    @router.get("/openapi.json")
    async def openapi() -> dict[str, object]:
        return get_openapi(title=app.title, version=app.version, routes=app.routes)

    @router.get("/docs")
    async def swagger() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/openapi.json", title=app.title)

    @router.get("/redoc")
    async def redoc() -> HTMLResponse:
        return get_redoc_html(openapi_url="/openapi.json", title=app.title)

    return router
