"""Application-safe database errors with PostgreSQL SQLSTATE preservation."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import NoReturn

import psycopg
from sqlalchemy.exc import DBAPIError, OperationalError, SQLAlchemyError

_DATABASE_URL = re.compile(r"(?i)\bpostgres(?:ql)?(?:\+psycopg)?://[^\s'\"<>]+")
_AUTHORIZATION_HEADER = re.compile(r"(?i)\b(authorization)(\s*[:=]\s*)[^;\r\n]+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|key|credential)"
    r"([\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^,;}\r\n]+)"
)
_AUTHENTICATION_TOKEN = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")


def redact_secrets(value: object) -> str:
    """Return a bounded representation with common credential forms removed."""

    rendered = str(value)
    rendered = _DATABASE_URL.sub("[REDACTED_DATABASE_URL]", rendered)
    rendered = _AUTHORIZATION_HEADER.sub(r"\1\2***", rendered)
    rendered = _SECRET_ASSIGNMENT.sub(r"\1\2***", rendered)
    rendered = _AUTHENTICATION_TOKEN.sub(r"\1 ***", rendered)
    return rendered[:1_024]


class BisfinError(Exception):
    """Base class for expected Bisfin application failures."""


class ConfigurationError(BisfinError):
    """Application configuration is invalid."""


class DatabaseError(BisfinError):
    """A database operation failed without exposing driver connection details."""

    def __init__(
        self,
        message: str,
        *,
        sqlstate: str | None = None,
        operation: str | None = None,
        original: BaseException | None = None,
    ) -> None:
        self.sqlstate = sqlstate
        self.operation = redact_secrets(operation) if operation else None
        self.original = original
        suffix = f" Operation: {self.operation}." if self.operation else ""
        state = f" [SQLSTATE {sqlstate}]" if sqlstate else ""
        super().__init__(f"{redact_secrets(message)}{state}{suffix}")


class DatabaseUnavailableError(DatabaseError):
    """PostgreSQL cannot currently be reached or used."""


class RepositoryError(BisfinError):
    """A repository could not fulfil a domain operation."""


class IntegrityViolationError(DatabaseError):
    """PostgreSQL rejected a write because integrity would be violated."""


class TemporalOverlapError(IntegrityViolationError):
    """A half-open catalog validity interval overlaps an existing interval."""


class InvalidPointInTimeQueryError(DatabaseError):
    """The database rejected Point-in-Time query arguments."""


class EntityNotFoundError(DatabaseError):
    """An explicitly requested database entity does not exist."""


class UnsupportedDatabaseOperationError(DatabaseError):
    """The database contract does not support the requested operation."""


class InvalidStateTransitionError(RepositoryError):
    """A requested domain lifecycle transition is not legal."""


class UnitOfWorkLifecycleError(BisfinError):
    """A Unit of Work was entered or completed in an invalid lifecycle state."""


def extract_sqlstate(error: BaseException) -> str | None:
    """Find a driver SQLSTATE through SQLAlchemy and exception wrappers."""

    pending: list[BaseException] = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))

        sqlstate = getattr(current, "sqlstate", None)
        if isinstance(sqlstate, str) and sqlstate:
            return sqlstate

        diag = getattr(current, "diag", None)
        diagnostic_state = getattr(diag, "sqlstate", None)
        if isinstance(diagnostic_state, str) and diagnostic_state:
            return diagnostic_state

        if isinstance(current, DBAPIError) and isinstance(current.orig, BaseException):
            pending.append(current.orig)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return None


def map_database_error(
    error: BaseException,
    *,
    operation: str | None = None,
) -> DatabaseError:
    """Translate a driver error while retaining its original object and SQLSTATE."""

    if isinstance(error, DatabaseError):
        return error

    sqlstate = extract_sqlstate(error)
    error_type: type[DatabaseError]
    message: str

    if sqlstate == "23P01":
        error_type = TemporalOverlapError
        message = "Temporal validity intervals overlap."
    elif sqlstate == "22004":
        error_type = InvalidPointInTimeQueryError
        message = "A required Point-in-Time query argument is null."
    elif sqlstate == "22023":
        error_type = InvalidPointInTimeQueryError
        message = "Point-in-Time query arguments are invalid."
    elif sqlstate == "P0002":
        error_type = EntityNotFoundError
        message = "The requested database entity was not found."
    elif sqlstate == "0A000":
        error_type = UnsupportedDatabaseOperationError
        message = "The requested database operation is not supported by this contract."
    elif sqlstate is not None and sqlstate.startswith("08"):
        error_type = DatabaseUnavailableError
        message = "PostgreSQL is unavailable."
    elif sqlstate is not None and sqlstate.startswith("23"):
        error_type = IntegrityViolationError
        message = "PostgreSQL rejected an integrity constraint violation."
    elif isinstance(error, (OperationalError, psycopg.OperationalError)):
        error_type = DatabaseUnavailableError
        message = "PostgreSQL is unavailable."
    else:
        error_type = DatabaseError
        message = "The database operation failed."

    return error_type(
        message,
        sqlstate=sqlstate,
        operation=operation,
        original=error,
    )


def raise_database_error(
    error: BaseException,
    *,
    operation: str | None = None,
) -> NoReturn:
    """Raise a mapped error and preserve the original exception as its cause."""

    raise map_database_error(error, operation=operation) from error


@contextmanager
def translate_database_errors(*, operation: str | None = None) -> Iterator[None]:
    """Translate SQLAlchemy/psycopg errors at an application boundary."""

    try:
        yield
    except DatabaseError:
        raise
    except (SQLAlchemyError, psycopg.Error) as error:
        raise_database_error(error, operation=operation)


__all__ = [
    "BisfinError",
    "ConfigurationError",
    "DatabaseError",
    "DatabaseUnavailableError",
    "EntityNotFoundError",
    "IntegrityViolationError",
    "InvalidPointInTimeQueryError",
    "InvalidStateTransitionError",
    "RepositoryError",
    "TemporalOverlapError",
    "UnitOfWorkLifecycleError",
    "UnsupportedDatabaseOperationError",
    "extract_sqlstate",
    "map_database_error",
    "raise_database_error",
    "redact_secrets",
    "translate_database_errors",
]
