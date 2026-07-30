"""Environment-backed, secret-aware application settings."""

from functools import lru_cache
from typing import Literal
from urllib.parse import quote, urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

Environment = Literal["local", "development", "test", "ci", "staging", "production"]
LogFormat = Literal["console", "json"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_SQLALCHEMY_SCHEME = "postgresql+psycopg://"
_PSYCOPG_SCHEME = "postgresql://"


class Settings(BaseSettings):
    """Validated runtime configuration loaded from the environment and ``.env``.

    Operating-system environment variables have the normal pydantic-settings
    precedence over values in ``.env``. Both database URL and password are
    ``SecretStr`` values so model representations and serialization redact them.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=True,
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    environment: Environment = Field(default="local", validation_alias="BISFIN_ENV")
    log_level: LogLevel = Field(default="INFO", validation_alias="BISFIN_LOG_LEVEL")
    log_format: LogFormat = Field(default="console", validation_alias="BISFIN_LOG_FORMAT")

    database_url: SecretStr | None = Field(default=None, validation_alias="DATABASE_URL")
    postgres_host: str = Field(default="127.0.0.1", min_length=1, validation_alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, ge=1, le=65535, validation_alias="POSTGRES_PORT")
    postgres_db: str = Field(default="bisfin", min_length=1, validation_alias="POSTGRES_DB")
    postgres_user: str = Field(default="bisfin", min_length=1, validation_alias="POSTGRES_USER")
    postgres_password: SecretStr = Field(
        default=SecretStr("bisfin_dev_only_password"),
        validation_alias="POSTGRES_PASSWORD",
    )

    database_pool_size: int = Field(default=5, gt=0, validation_alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=10, ge=0, validation_alias="DATABASE_MAX_OVERFLOW")
    database_pool_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        validation_alias="DATABASE_POOL_TIMEOUT_SECONDS",
    )
    database_statement_timeout_ms: int = Field(
        default=30_000,
        gt=0,
        validation_alias="DATABASE_STATEMENT_TIMEOUT_MS",
    )
    database_application_name: str = Field(
        default="bisfin",
        min_length=1,
        max_length=64,
        validation_alias="DATABASE_APPLICATION_NAME",
    )

    @field_validator("environment", "log_format", mode="before")
    @classmethod
    def _normalize_lowercase(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @property
    def sqlalchemy_database_url(self) -> str:
        """Return a SQLAlchemy 2 URL using the psycopg 3 dialect.

        An explicitly supplied ``DATABASE_URL`` wins. Its PostgreSQL scheme is
        normalized without parsing or exposing credentials. Otherwise every URL
        component that can contain reserved characters is percent-encoded.
        """

        explicit_url = self._explicit_database_url()
        if explicit_url is not None:
            return _to_sqlalchemy_scheme(explicit_url)

        username = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password.get_secret_value(), safe="")
        database = quote(self.postgres_db, safe="")
        host = _format_host(self.postgres_host)
        return f"{_SQLALCHEMY_SCHEME}{username}:{password}@{host}:{self.postgres_port}/{database}"

    @property
    def psycopg_database_url(self) -> str:
        """Return the equivalent psycopg-native PostgreSQL URL."""

        sqlalchemy_url = self.sqlalchemy_database_url
        return _PSYCOPG_SCHEME + sqlalchemy_url.removeprefix(_SQLALCHEMY_SCHEME)

    @property
    def application(self) -> str:
        """Return the application identifier used by logs and PostgreSQL."""

        return self.database_application_name

    def safe_summary(self) -> dict[str, str | int | float]:
        """Return configuration suitable for CLI output and structured logs."""

        return {
            "environment": self.environment,
            "log_level": self.log_level,
            "log_format": self.log_format,
            "database_source": "DATABASE_URL" if self._explicit_database_url() else "POSTGRES_*",
            "postgres_host": self.postgres_host,
            "postgres_port": self.postgres_port,
            "postgres_db": self.postgres_db,
            "postgres_user": self.postgres_user,
            "database_pool_size": self.database_pool_size,
            "database_max_overflow": self.database_max_overflow,
            "database_pool_timeout_seconds": self.database_pool_timeout_seconds,
            "database_statement_timeout_ms": self.database_statement_timeout_ms,
            "database_application_name": self.database_application_name,
        }

    def _explicit_database_url(self) -> str | None:
        if self.database_url is None:
            return None
        value = self.database_url.get_secret_value().strip()
        return value or None


def _to_sqlalchemy_scheme(url: str) -> str:
    scheme, separator, remainder = url.partition("://")
    if separator and scheme.lower() in {
        "postgresql+psycopg",
        "postgresql",
        "postgres",
    }:
        normalized = _SQLALCHEMY_SCHEME + remainder
        try:
            urlsplit(normalized)
            make_url(normalized)
        except (ArgumentError, ValueError):
            raise ValueError("DATABASE_URL is not a valid PostgreSQL URL") from None
        return normalized
    raise ValueError("DATABASE_URL must use a PostgreSQL scheme") from None


def _format_host(host: str) -> str:
    """Bracket a bare IPv6 address while leaving DNS names unchanged."""

    normalized = host.strip()
    if ":" in normalized and not normalized.startswith("["):
        return f"[{normalized}]"
    return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache process-wide settings without creating external resources."""

    return Settings()


def reset_settings_cache() -> None:
    """Clear the settings cache, primarily for process-isolated tests."""

    get_settings.cache_clear()
