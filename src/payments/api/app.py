from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from payments.api.payments import router as payments_router
from payments.core.settings import Settings, load_settings


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="payments")
    app.state.settings = settings
    # create_async_engine connects lazily, so /health works without a database.
    app.state.engine = create_async_engine(settings.database_url)
    app.state.session_factory = async_sessionmaker(
        app.state.engine, expire_on_commit=False
    )
    app.include_router(payments_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def app_factory() -> FastAPI:
    return create_app(load_settings())
