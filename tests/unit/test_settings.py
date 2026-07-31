"""Unit tests for typed, secret-aware application settings."""

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from bisfin.config import Settings

pytestmark = pytest.mark.unit

_DATABASE_ENVIRONMENT_KEYS = (
    "DATABASE_URL",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "DATABASE_POOL_SIZE",
    "DATABASE_MAX_OVERFLOW",
    "DATABASE_POOL_TIMEOUT_SECONDS",
    "DATABASE_STATEMENT_TIMEOUT_MS",
    "DATABASE_APPLICATION_NAME",
    "BRSAPI_BASE_URL",
    "BRSAPI_API_KEY",
    "BRSAPI_CONNECT_TIMEOUT_SECONDS",
    "BRSAPI_READ_TIMEOUT_SECONDS",
    "BRSAPI_USER_AGENT",
    "BRSAPI_PROVIDER_CODE",
    "BRSAPI_DAILY_RAW_FEED_CODE",
    "BRSAPI_IDENTIFIER_TYPE",
    "BRSAPI_DEFAULT_TIMEZONE",
)


def _settings(**values: object) -> Settings:
    """Validate explicit values without reading environment or dotenv sources."""

    # pydantic-settings accepts this documented runtime-only source override,
    # but its generated type-checking signature intentionally omits it.
    return Settings(_env_file=None, **values)  # type: ignore[call-arg,arg-type]


@pytest.fixture(autouse=True)
def _isolated_database_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in _DATABASE_ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def test_database_url_takes_precedence_over_individual_fields() -> None:
    settings = _settings(
        database_url="postgresql://direct:encoded%40secret@db.example/direct_db?sslmode=require",
        postgres_host="ignored.example",
        postgres_db="ignored_db",
        postgres_user="ignored_user",
        postgres_password="ignored_password",
    )

    assert settings.sqlalchemy_database_url == (
        "postgresql+psycopg://direct:encoded%40secret@db.example/direct_db?sslmode=require"
    )


def test_environment_database_url_overrides_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://env-user:env-pass@env-db/env-name")
    monkeypatch.setenv("POSTGRES_HOST", "fallback-db")

    settings = Settings()

    assert settings.sqlalchemy_database_url == (
        "postgresql+psycopg://env-user:env-pass@env-db/env-name"
    )
    assert settings.safe_summary()["database_source"] == "DATABASE_URL"


def test_fallback_url_is_constructed_with_psycopg_dialect() -> None:
    settings = _settings(
        postgres_host="database.internal",
        postgres_port=6543,
        postgres_db="market-data",
        postgres_user="worker",
        postgres_password="safe-local-value",
    )

    assert settings.sqlalchemy_database_url == (
        "postgresql+psycopg://worker:safe-local-value@database.internal:6543/market-data"
    )


def test_fallback_url_percent_encodes_user_password_and_database() -> None:
    settings = _settings(
        postgres_user="worker@tenant",
        postgres_password="p@ss:/?#[] value",
        postgres_db="market/data",
    )

    assert settings.sqlalchemy_database_url == (
        "postgresql+psycopg://worker%40tenant:p%40ss%3A%2F%3F%23%5B%5D%20value"
        "@127.0.0.1:5432/market%2Fdata"
    )


def test_psycopg_url_conversion_preserves_the_remainder() -> None:
    settings = _settings(
        database_url="postgresql+psycopg://worker:encoded%40secret@db/bisfin?sslmode=require",
    )

    assert settings.psycopg_database_url == (
        "postgresql://worker:encoded%40secret@db/bisfin?sslmode=require"
    )


def test_explicit_postgresql_scheme_is_case_insensitive() -> None:
    settings = _settings(database_url="POSTGRESQL://worker:secret@database/bisfin")

    assert settings.sqlalchemy_database_url == (
        "postgresql+psycopg://worker:secret@database/bisfin"
    )


@pytest.mark.parametrize("port", [0, 65_536])
def test_invalid_postgres_port_is_rejected(port: int) -> None:
    with pytest.raises(ValidationError):
        _settings(postgres_port=port)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_pool_size", 0),
        ("database_max_overflow", -1),
        ("database_pool_timeout_seconds", 0),
        ("database_statement_timeout_ms", 0),
    ],
)
def test_invalid_pool_settings_are_rejected(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({field: value})


def test_supported_values_are_normalized() -> None:
    settings = _settings(
        environment="PRODUCTION",
        log_level="warning",
        log_format="JSON",
    )

    assert settings.environment == "production"
    assert settings.log_level == "WARNING"
    assert settings.log_format == "json"


def test_ci_is_a_supported_environment() -> None:
    settings = _settings(environment="ci")

    assert settings.environment == "ci"


@pytest.mark.parametrize(
    ("field", "value"),
    [("environment", "sandbox"), ("log_format", "xml"), ("log_level", "TRACE")],
)
def test_unsupported_setting_values_are_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({field: value})


def test_secrets_are_redacted_from_repr_serialization_and_validation_errors() -> None:
    password = "never-print-this-password"
    url_secret = "never-print-this-url-secret"
    settings = _settings(
        database_url=f"postgresql://worker:{url_secret}@database/bisfin",
        postgres_password=password,
    )

    assert password not in repr(settings)
    assert url_secret not in repr(settings)
    assert password not in settings.model_dump_json()
    assert url_secret not in settings.model_dump_json()
    assert password not in repr(settings.safe_summary())
    assert url_secret not in repr(settings.safe_summary())

    with pytest.raises(ValidationError) as error:
        _settings(postgres_password=password, postgres_port=0)
    rendered_error = f"{error.value} {error.value.errors()} {error.value.json()}"
    assert password not in rendered_error


def test_invalid_database_url_error_does_not_echo_secret() -> None:
    secret = "not-for-errors"
    settings = _settings(database_url=f"mysql://worker:{secret}@database/bisfin")

    with pytest.raises(ValueError, match="must use a PostgreSQL scheme") as error:
        _ = settings.sqlalchemy_database_url

    assert secret not in f"{error.value!r} {error.value}"


def test_malformed_database_url_is_rejected_without_echoing_secret() -> None:
    secret = "not-for-parser-errors"
    settings = _settings(database_url=f"postgresql://worker:{secret}@[bad/db")

    with pytest.raises(ValueError, match="not a valid PostgreSQL URL") as error:
        _ = settings.sqlalchemy_database_url

    assert secret not in f"{error.value!r} {error.value}"


def test_brsapi_defaults_are_safe_and_fixture_compatible() -> None:
    settings = _settings()

    assert settings.brsapi_base_url == "https://Api.BrsApi.ir/"
    assert settings.brsapi_api_key is None
    assert settings.brsapi_provider_code == "BRSAPI"
    assert settings.brsapi_daily_raw_feed_code == "TSETMC_CANDLE_DAILY_RAW"
    assert settings.brsapi_identifier_type == "BRSAPI_L18"
    assert settings.brsapi_default_timezone == "Asia/Tehran"
    assert settings.safe_summary()["brsapi_api_key_configured"] == "no"


def test_brsapi_api_key_is_secret_and_only_configuration_state_is_summarized() -> None:
    secret = "brsapi-never-print-this"
    settings = _settings(brsapi_api_key=secret)

    rendered = f"{settings!r} {settings.model_dump_json()} {settings.safe_summary()!r}"
    assert secret not in rendered
    assert settings.safe_summary()["brsapi_api_key_configured"] == "yes"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://Api.BrsApi.ir/",
        "ftp://Api.BrsApi.ir/",
        "https:///missing-host",
        "https://user:password@Api.BrsApi.ir/",
        "https://Api.BrsApi.ir/?key=secret",
    ],
)
def test_brsapi_base_url_rejects_insecure_or_credentialed_values(base_url: str) -> None:
    with pytest.raises(ValidationError):
        _settings(brsapi_base_url=base_url)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_brsapi_timeouts_must_be_positive_and_finite(timeout: float) -> None:
    with pytest.raises(ValidationError):
        _settings(brsapi_connect_timeout_seconds=timeout)


def test_brsapi_timezone_must_be_an_iana_zone() -> None:
    with pytest.raises(ValidationError):
        _settings(brsapi_default_timezone="Mars/Olympus")
