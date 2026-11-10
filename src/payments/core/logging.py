"""JSON logging on stdlib `logging`: one object per line, `payment_id` when known."""

import contextlib
import contextvars
import json
import logging
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TextIO

_payment_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "payment_id", default=None
)

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@contextlib.contextmanager
def payment_context(payment_id: object | None) -> Iterator[None]:
    """Tag every log record emitted inside the block with this Payment's id.

    `None` (the Payment is not known) tags nothing.
    """
    if payment_id is None:
        yield
        return
    token = _payment_id.set(str(payment_id))
    try:
        yield
    finally:
        _payment_id.reset(token)


class _PaymentIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "payment_id", None) is None:
            current = _payment_id.get()
            if current is not None:
                record.payment_id = current
        return True


_STANDARD_ATTRIBUTES = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}
"""Attributes every LogRecord has; anything else came from `extra` or a filter."""


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRIBUTES and key not in entry:
                entry[key] = str(value) if key == "payment_id" else value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            entry["exception"] = record.exc_text
        return json.dumps(entry, default=str)


def json_handler(stream: TextIO | None = None) -> logging.Handler:
    """A handler writing JSON lines to `stream` (default stderr)."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_PaymentIdFilter())
    return handler


def configure_logging(level: int = logging.INFO) -> None:
    """Send all logging, including uvicorn's and FastStream's, through one JSON handler.

    Called at process start by the app factories, never by `create_app`, so
    tests keep pytest's own log capture.
    """
    handler = json_handler()
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
