"""Demo Webhook receiver: logs every request as a JSON line, 200 or 500 on `?fail=1`."""

import json
import logging

from fastapi import FastAPI, Request, Response

from payments.core.logging import configure_logging

logger = logging.getLogger("webhook_sink")


def create_app() -> FastAPI:
    app = FastAPI(title="webhook-sink")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def sink(request: Request) -> Response:
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = raw.decode(errors="replace")
        logger.info(
            "received",
            extra={
                "method": request.method,
                "path": request.url.path,
                "query": request.url.query,
                "body": body,
            },
        )
        failing = request.query_params.get("fail") == "1"
        return Response(status_code=500 if failing else 200)

    return app


def app_factory() -> FastAPI:
    configure_logging()
    # The sink's own JSON line is the record; uvicorn's access line would double it.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return create_app()
