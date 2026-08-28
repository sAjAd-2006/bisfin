"""Standard-library console and JSON logging with operation-local context."""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Literal, TextIO

type LogFormat = Literal["console", "json"]
type ContextValue = str | int
type LogContext = dict[str, ContextValue]

_CONTEXT_FIELDS = (
    "request_id",
    "correlation_id",
    "ingestion_batch_id",
    "provider_code",
    "feed_code",
    "symbol",
    "manifest_id",
    "calendar_id",
    "venue_code",
    "instrument_stable_key",
    "backtest_run_id",
)
_log_context: ContextVar[LogContext] = ContextVar("bisfin_log_context", default={})

_DATABASE_URL_PATTERN = re.compile(r"(?i)\b(?:postgres|postgresql)(?:\+psycopg)?://[^\s\"'<>]+")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(postgres_password|database_url|password|api[_-]?key|key|authorization|token)\b"
    r"\s*(?:=|:|'\s*:\s*|\"\s*:\s*)\s*"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s,;}]+)"
)
_KEYED_URL_PATTERN = re.compile(r"(?i)([?&]key=)[^&#\s]+")
_AUTH_TOKEN_PATTERN = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")


def redact_sensitive_text(value: str) -> str:
    """Redact PostgreSQL URLs and common password assignments from text."""

    redacted = _DATABASE_URL_PATTERN.sub("[REDACTED_DATABASE_URL]", value)
    redacted = _KEYED_URL_PATTERN.sub(r"\1[REDACTED]", redacted)
    redacted = _AUTH_TOKEN_PATTERN.sub(r"\1 [REDACTED]", redacted)
    return _SECRET_ASSIGNMENT_PATTERN.sub(_redacted_assignment, redacted)


def _redacted_assignment(match: re.Match[str]) -> str:
    key = match.group(1).lower()
    label = "password" if "password" in key else key
    return f"{label}=[REDACTED]"


def bind_log_context(
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
    ingestion_batch_id: int | str | None = None,
    provider_code: str | None = None,
    feed_code: str | None = None,
    symbol: str | None = None,
    manifest_id: str | None = None,
    calendar_id: str | None = None,
    venue_code: str | None = None,
    instrument_stable_key: str | None = None,
    backtest_run_id: int | str | None = None,
) -> Token[LogContext]:
    """Merge supplied correlation fields into this context and return a reset token."""

    additions: LogContext = {
        key: value
        for key, value in {
            "request_id": request_id,
            "correlation_id": correlation_id,
            "ingestion_batch_id": ingestion_batch_id,
            "provider_code": provider_code,
            "feed_code": feed_code,
            "symbol": symbol,
            "manifest_id": manifest_id,
            "calendar_id": calendar_id,
            "venue_code": venue_code,
            "instrument_stable_key": instrument_stable_key,
            "backtest_run_id": backtest_run_id,
        }.items()
        if value is not None
    }
    return _log_context.set({**_log_context.get(), **additions})


def reset_log_context(token: Token[LogContext]) -> None:
    """Restore the precise context that existed before ``bind_log_context``."""

    _log_context.reset(token)


def clear_log_context() -> None:
    """Remove all correlation data before starting an independent operation."""

    _log_context.set({})


def get_log_context() -> LogContext:
    """Return an isolated snapshot of the active logging context."""

    return dict(_log_context.get())


@contextmanager
def log_context(
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
    ingestion_batch_id: int | str | None = None,
    provider_code: str | None = None,
    feed_code: str | None = None,
    symbol: str | None = None,
    manifest_id: str | None = None,
    calendar_id: str | None = None,
    venue_code: str | None = None,
    instrument_stable_key: str | None = None,
    backtest_run_id: int | str | None = None,
) -> Iterator[None]:
    """Bind correlation fields and always restore the previous context."""

    token = bind_log_context(
        request_id=request_id,
        correlation_id=correlation_id,
        ingestion_batch_id=ingestion_batch_id,
        provider_code=provider_code,
        feed_code=feed_code,
        symbol=symbol,
        manifest_id=manifest_id,
        calendar_id=calendar_id,
        venue_code=venue_code,
        instrument_stable_key=instrument_stable_key,
        backtest_run_id=backtest_run_id,
    )
    try:
        yield
    finally:
        reset_log_context(token)


class _ContextFilter(logging.Filter):
    def __init__(self, *, environment: str, application: str) -> None:
        super().__init__()
        self._environment = environment
        self._application = application

    def filter(self, record: logging.LogRecord) -> bool:
        record.environment = self._environment
        record.application = self._application
        context = _log_context.get()
        for field in _CONTEXT_FIELDS:
            if field in context:
                setattr(record, field, context[field])
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        environment = str(getattr(record, "environment", "unknown"))
        application = str(getattr(record, "application", "bisfin"))
        payload: dict[str, object] = {
            "timestamp": _timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_sensitive_text(record.getMessage()),
            "environment": environment,
            "application": application,
        }
        for field in _CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = redact_sensitive_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        environment = str(getattr(record, "environment", "unknown"))
        application = str(getattr(record, "application", "bisfin"))
        context = " ".join(
            f"{field}={getattr(record, field)}"
            for field in _CONTEXT_FIELDS
            if getattr(record, field, None) is not None
        )
        suffix = f" {context}" if context else ""
        output = (
            f"{_timestamp(record.created)} {record.levelname:<8} {record.name}: "
            f"{redact_sensitive_text(record.getMessage())}"
            f" environment={environment} application={application}{suffix}"
        )
        if record.exc_info:
            exception = redact_sensitive_text(self.formatException(record.exc_info))
            output = f"{output}\n{exception}"
        return output


def configure_logging(
    *,
    level: str = "INFO",
    log_format: LogFormat = "console",
    environment: str = "local",
    application: str = "bisfin",
    stream: TextIO | None = None,
) -> logging.Handler:
    """Replace root handlers with one deterministic console or JSON handler."""

    normalized_level = level.strip().upper()
    numeric_level = logging.getLevelNamesMapping().get(normalized_level)
    if numeric_level is None:
        raise ValueError(f"Unsupported log level: {level!r}")
    if log_format not in ("console", "json"):
        raise ValueError(f"Unsupported log format: {log_format!r}")

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.addFilter(_ContextFilter(environment=environment, application=application))
    handler.setFormatter(_JsonFormatter() if log_format == "json" else _ConsoleFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)
    return handler


def _timestamp(created: float) -> str:
    return (
        datetime.fromtimestamp(created, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
