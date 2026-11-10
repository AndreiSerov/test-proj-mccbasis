"""JSON log lines: one object per line, `payment_id` where a Payment applies."""

import io
import json
import logging

from payments.core.logging import json_handler, payment_context


def _log_lines(emit) -> list[dict]:
    stream = io.StringIO()
    handler = json_handler(stream)
    logger = logging.getLogger("test.json_logging")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        emit(logger)
    finally:
        logger.removeHandler(handler)
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_record_is_one_json_object_with_the_basic_fields() -> None:
    [record] = _log_lines(lambda log: log.warning("disk %s", "full"))

    assert record["level"] == "WARNING"
    assert record["logger"] == "test.json_logging"
    assert record["message"] == "disk full"
    assert record["timestamp"].endswith("+00:00")
    assert "payment_id" not in record


def test_payment_id_from_extra_is_a_field() -> None:
    [record] = _log_lines(lambda log: log.info("hi", extra={"payment_id": "p-1"}))

    assert record["payment_id"] == "p-1"


def test_payment_context_tags_every_record_inside_it_and_only_those() -> None:
    def emit(log: logging.Logger) -> None:
        with payment_context("p-2"):
            log.info("inside")
        log.info("outside")

    inside, outside = _log_lines(emit)

    assert inside["payment_id"] == "p-2"
    assert "payment_id" not in outside


def test_exception_info_is_a_field_and_the_line_stays_single() -> None:
    def emit(log: logging.Logger) -> None:
        try:
            raise ValueError("boom\nsecond line")
        except ValueError:
            log.exception("failed")

    [record] = _log_lines(emit)

    assert "ValueError: boom" in record["exception"]
    assert record["message"] == "failed"


def test_other_extra_attributes_become_fields() -> None:
    [record] = _log_lines(
        lambda log: log.info(
            "got", extra={"message_id": "abc", "queue": "payments.new"}
        )
    )

    assert record["message_id"] == "abc"
    assert record["queue"] == "payments.new"
    assert "args" not in record
    assert "levelno" not in record


def test_payment_context_with_none_is_a_no_op() -> None:
    def emit(log: logging.Logger) -> None:
        with payment_context(None):
            log.info("unknown payment")

    [record] = _log_lines(emit)

    assert "payment_id" not in record
