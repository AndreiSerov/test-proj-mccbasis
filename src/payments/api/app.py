from fastapi import FastAPI

from payments.core.settings import Settings, load_settings


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="payments")
    app.state.settings = settings

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def app_factory() -> FastAPI:
    return create_app(load_settings())
