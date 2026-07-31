"""Unit tests for deterministic structured logging and operation context."""

import io
import json
import logging
from collections.abc import Iterator

import pytest

from bisfin.logging import clear_log_context, configure_logging, get_log_context, log_context

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _restore_root_logging() -> Iterator[None]:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    clear_log_context()
    yield
    root_logger.handlers.clear()
    root_logger.handlers.extend(original_handlers)
    root_logger.setLevel(original_level)
    clear_log_context()


def _json_records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_json_logging_emits_one_valid_object_per_line() -> None:
    stream = io.StringIO()
    configure_logging(
        level="INFO",
        log_format="json",
        environment="test",
        application="bisfin-tests",
        stream=stream,
    )

    logging.getLogger("bisfin.unit").info("first message")
    logging.getLogger("bisfin.unit").warning("second message")

    records = _json_records(stream)
    assert [record["message"] for record in records] == ["first message", "second message"]
    assert records[0]["level"] == "INFO"
    assert records[0]["logger"] == "bisfin.unit"
    assert records[0]["environment"] == "test"
    assert records[0]["application"] == "bisfin-tests"
    assert str(records[0]["timestamp"]).endswith("Z")


def test_context_is_injected_into_json_record() -> None:
    stream = io.StringIO()
    configure_logging(log_format="json", stream=stream)

    with log_context(
        request_id="request-17",
        correlation_id="correlation-4",
        ingestion_batch_id=23,
        provider_code="BRSAPI",
        feed_code="TSETMC_CANDLE_DAILY_RAW",
        symbol="فملی",
        backtest_run_id="run-9",
    ):
        logging.getLogger("bisfin.context").info("contextual")

    record = _json_records(stream)[0]
    assert record["request_id"] == "request-17"
    assert record["correlation_id"] == "correlation-4"
    assert record["ingestion_batch_id"] == 23
    assert record["provider_code"] == "BRSAPI"
    assert record["feed_code"] == "TSETMC_CANDLE_DAILY_RAW"
    assert record["symbol"] == "فملی"
    assert record["backtest_run_id"] == "run-9"


def test_context_manager_restores_context_after_success_and_exception() -> None:
    assert get_log_context() == {}

    with log_context(request_id="outer"):
        assert get_log_context() == {"request_id": "outer"}
        with pytest.raises(RuntimeError, match="operation failed"):
            with log_context(correlation_id="inner"):
                assert get_log_context() == {
                    "request_id": "outer",
                    "correlation_id": "inner",
                }
                raise RuntimeError("operation failed")
        assert get_log_context() == {"request_id": "outer"}

    assert get_log_context() == {}


def test_independent_record_does_not_inherit_completed_context() -> None:
    stream = io.StringIO()
    configure_logging(log_format="json", stream=stream)

    with log_context(request_id="completed-request"):
        logging.getLogger("bisfin.context").info("inside")
    logging.getLogger("bisfin.context").info("outside")

    inside, outside = _json_records(stream)
    assert inside["request_id"] == "completed-request"
    assert "request_id" not in outside


def test_exception_logging_includes_traceback_in_one_json_record() -> None:
    stream = io.StringIO()
    configure_logging(log_format="json", stream=stream)

    try:
        raise LookupError("missing value")
    except LookupError:
        logging.getLogger("bisfin.exception").exception("lookup failed")

    records = _json_records(stream)
    assert len(records) == 1
    assert records[0]["message"] == "lookup failed"
    assert "LookupError: missing value" in str(records[0]["exception"])


def test_console_logging_is_human_readable() -> None:
    stream = io.StringIO()
    configure_logging(log_format="console", environment="test", application="worker", stream=stream)

    logging.getLogger("bisfin.console").info("ready")

    output = stream.getvalue()
    assert "INFO" in output
    assert "bisfin.console: ready" in output
    assert "environment=test" in output
    assert "application=worker" in output


def test_database_url_and_password_assignments_are_redacted() -> None:
    stream = io.StringIO()
    configure_logging(log_format="json", stream=stream)
    url_secret = "url-secret-value"
    password_secret = "password-secret-value"

    logging.getLogger("bisfin.secret").error(
        "connection failed url=%s POSTGRES_PASSWORD=%s",
        f"postgresql+psycopg://worker:{url_secret}@database/bisfin",
        password_secret,
    )

    output = stream.getvalue()
    assert url_secret not in output
    assert password_secret not in output
    assert "[REDACTED_DATABASE_URL]" in output
    assert "password=[REDACTED]" in output


def test_brsapi_keyed_urls_and_authorization_are_redacted() -> None:
    stream = io.StringIO()
    configure_logging(log_format="json", stream=stream)
    secret = "provider-key-never-print"

    logging.getLogger("bisfin.secret").error(
        "request failed url=https://Api.BrsApi.ir/Tsetmc/Candlestick.php?key=%s&type=2 "
        "authorization=Bearer %s",
        secret,
        secret,
    )

    output = stream.getvalue()
    assert secret not in output
    assert "[REDACTED]" in output
