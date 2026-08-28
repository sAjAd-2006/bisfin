"""Concurrency-safe, transaction-neutral catalog bootstrap persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import insert, or_, select, text, update
from sqlalchemy.engine import Connection, RowMapping

from bisfin.catalog.errors import (
    CatalogConflictError,
    IdentifierRenameConflictError,
    InstrumentIdentityConflictError,
    InstrumentSpecConflictError,
)
from bisfin.catalog.manifest import (
    AssetTypeDefinition,
    CurrencyDefinition,
    FeedDefinition,
    InstrumentDefinition,
    ProviderDefinition,
    TimeframeDefinition,
    VenueDefinition,
)
from bisfin.db.errors import translate_database_errors
from bisfin.db.tables import (
    asset_type,
    currency,
    data_feed,
    data_provider,
    instrument,
    instrument_identifier,
    instrument_spec_version,
    timeframe,
    venue,
)
from bisfin.domain.catalog import (
    AssetType,
    Currency,
    DataFeed,
    Instrument,
    InstrumentIdentifier,
    Provider,
    Timeframe,
    Venue,
)
from bisfin.domain.common import require_aware_datetime


@dataclass(frozen=True, slots=True)
class CatalogWriteResult:
    created: bool
    unchanged: bool


@dataclass(frozen=True, slots=True)
class InstrumentWriteResult:
    instrument: Instrument
    instrument_created: bool
    identifier_created: int
    identifier_closed: int
    specification_created: int
    unchanged: bool


class SqlAlchemyCatalogWriterRepository:
    """Write only explicit manifest state through a caller-owned transaction.

    All reference keys and temporal logical keys are serialized with transaction
    advisory locks. Database unique/exclusion constraints and migration-0003
    triggers remain final authority; this layer never commits or rolls back.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def ensure_currency(
        self, definition: CurrencyDefinition
    ) -> tuple[Currency, CatalogWriteResult]:
        self._lock("currency", definition.currency_code)
        row = self._one_or_none(
            select(currency).where(currency.c.currency_code == definition.currency_code)
        )
        expected = {
            "currency_code": definition.currency_code,
            "display_name": definition.display_name,
            "minor_unit": definition.minor_unit,
            "is_fiat": definition.is_fiat,
            "metadata": definition.metadata,
        }
        if row is None:
            row = self._one(insert(currency).values(**expected).returning(*currency.c))
            return Currency.model_validate(dict(row)), CatalogWriteResult(True, False)
        self._assert_equal(row, expected, "currency", definition.currency_code)
        return Currency.model_validate(dict(row)), CatalogWriteResult(False, True)

    def ensure_asset_type(
        self, definition: AssetTypeDefinition
    ) -> tuple[AssetType, CatalogWriteResult]:
        self._lock("asset_type", definition.asset_type_code)
        row = self._one_or_none(
            select(asset_type).where(asset_type.c.asset_type_code == definition.asset_type_code)
        )
        expected = {
            "asset_type_code": definition.asset_type_code,
            "display_name": definition.display_name,
            "description": definition.description,
        }
        if row is None:
            row = self._one(insert(asset_type).values(**expected).returning(*asset_type.c))
            return AssetType.model_validate(dict(row)), CatalogWriteResult(True, False)
        self._assert_equal(row, expected, "asset type", definition.asset_type_code)
        return AssetType.model_validate(dict(row)), CatalogWriteResult(False, True)

    def ensure_provider(
        self, definition: ProviderDefinition
    ) -> tuple[Provider, CatalogWriteResult]:
        self._lock("provider", definition.provider_code)
        row = self._one_or_none(
            select(data_provider).where(data_provider.c.provider_code == definition.provider_code)
        )
        expected = {
            "provider_code": definition.provider_code,
            "display_name": definition.display_name,
            "provider_kind": definition.provider_kind,
            "base_url": definition.base_url,
            "default_timezone": definition.default_timezone,
            "metadata": definition.metadata,
        }
        if row is None:
            row = self._one(insert(data_provider).values(**expected).returning(*data_provider.c))
            return Provider.model_validate(dict(row)), CatalogWriteResult(True, False)
        self._assert_equal(row, expected, "provider", definition.provider_code)
        return Provider.model_validate(dict(row)), CatalogWriteResult(False, True)

    def ensure_feed(
        self, provider_id: int, definition: FeedDefinition
    ) -> tuple[DataFeed, CatalogWriteResult]:
        self._lock("feed", provider_id, definition.feed_code)
        row = self._one_or_none(
            select(data_feed).where(
                data_feed.c.provider_id == provider_id,
                data_feed.c.feed_code == definition.feed_code,
            )
        )
        expected = {
            "provider_id": provider_id,
            "feed_code": definition.feed_code,
            "display_name": definition.display_name,
            "data_kind": definition.data_kind,
            "native_timezone": definition.native_timezone,
            "parser_version": definition.parser_version,
            "active_from": definition.active_from,
            "active_to": definition.active_to,
            "metadata": definition.metadata,
        }
        if row is None:
            row = self._one(insert(data_feed).values(**expected).returning(*data_feed.c))
            return DataFeed.model_validate(dict(row)), CatalogWriteResult(True, False)
        self._assert_equal(row, expected, "feed", definition.feed_code)
        return DataFeed.model_validate(dict(row)), CatalogWriteResult(False, True)

    def ensure_venue(self, definition: VenueDefinition) -> tuple[Venue, CatalogWriteResult]:
        self._lock("venue", definition.venue_code)
        row = self._one_or_none(select(venue).where(venue.c.venue_code == definition.venue_code))
        expected = {
            "venue_code": definition.venue_code,
            "display_name": definition.display_name,
            "mic_code": definition.mic_code,
            "country_code": definition.country_code,
            "timezone_name": definition.timezone_name,
            "base_currency_code": definition.base_currency_code,
            "metadata": definition.metadata,
        }
        if row is None:
            row = self._one(insert(venue).values(**expected).returning(*venue.c))
            return Venue.model_validate(dict(row)), CatalogWriteResult(True, False)
        self._assert_equal(row, expected, "venue", definition.venue_code)
        return Venue.model_validate(dict(row)), CatalogWriteResult(False, True)

    def get_venue_by_code(self, venue_code: str) -> Venue:
        row = self._one_or_none(select(venue).where(venue.c.venue_code == venue_code))
        if row is None:
            raise CatalogConflictError(f"Required venue {venue_code!r} is not bootstrapped.")
        return Venue.model_validate(dict(row))

    def get_feed_by_codes(self, provider_code: str, feed_code: str) -> DataFeed:
        row = self._one_or_none(
            select(data_feed)
            .join(data_provider, data_feed.c.provider_id == data_provider.c.provider_id)
            .where(
                data_provider.c.provider_code == provider_code,
                data_feed.c.feed_code == feed_code,
            )
        )
        if row is None:
            raise CatalogConflictError(
                f"Required feed {provider_code}/{feed_code} is not bootstrapped."
            )
        return DataFeed.model_validate(dict(row))

    def ensure_timeframe(
        self, definition: TimeframeDefinition
    ) -> tuple[Timeframe, CatalogWriteResult]:
        self._lock("timeframe", definition.timeframe_code)
        row = self._one_or_none(
            select(timeframe).where(timeframe.c.timeframe_code == definition.timeframe_code)
        )
        expected = {
            "timeframe_code": definition.timeframe_code,
            "display_name": definition.display_name,
            "duration_seconds": definition.duration_seconds,
            "calendar_unit": definition.calendar_unit,
            "session_aligned": definition.session_aligned,
        }
        if row is None:
            row = self._one(insert(timeframe).values(**expected).returning(*timeframe.c))
            return Timeframe.model_validate(dict(row)), CatalogWriteResult(True, False)
        self._assert_equal(row, expected, "timeframe", definition.timeframe_code)
        return Timeframe.model_validate(dict(row)), CatalogWriteResult(False, True)

    def apply_instrument(
        self,
        definition: InstrumentDefinition,
        *,
        provider_id: int,
        venue_id: int,
    ) -> InstrumentWriteResult:
        """Resolve by ISIN before symbol and apply only explicit, adjacent history."""

        self._lock_identifier_values(
            provider_id,
            (("BRSAPI_L18", definition.provider_symbol), ("ISIN", definition.isin)),
        )
        isin_owner = self._identifier_owner(
            provider_id, "ISIN", definition.isin, definition.identifier_valid_from
        )
        symbol_owner = self._identifier_owner(
            provider_id, "BRSAPI_L18", definition.provider_symbol, definition.identifier_valid_from
        )
        created = False
        identifiers_created = 0
        identifiers_closed = 0
        if isin_owner is None and symbol_owner is None:
            row = self._one(
                insert(instrument)
                .values(
                    asset_type_code=definition.asset_type_code,
                    venue_id=venue_id,
                    quote_currency_code=definition.currency_code,
                    canonical_symbol=definition.provider_symbol,
                    display_name=definition.name_fa,
                    status=definition.status,
                    active_from=definition.active_from,
                    active_to=definition.active_to,
                    metadata={
                        **definition.metadata,
                        "stable_key": definition.stable_key,
                        "name_en": definition.name_en,
                    },
                )
                .returning(*instrument.c)
            )
            current = Instrument.model_validate(dict(row))
            created = True
            self._insert_identifier(
                provider_id=provider_id,
                identifier_type="ISIN",
                identifier_value=definition.isin,
                valid_from=definition.identifier_valid_from,
                instrument_id=current.instrument_id,
                is_primary=False,
            )
            self._insert_identifier(
                provider_id=provider_id,
                identifier_type="BRSAPI_L18",
                identifier_value=definition.provider_symbol,
                valid_from=definition.identifier_valid_from,
                instrument_id=current.instrument_id,
                is_primary=True,
            )
            identifiers_created = 2
        elif isin_owner is None:
            raise InstrumentIdentityConflictError(
                "A provider symbol resolves without the manifest ISIN; "
                "automatic merge is forbidden."
            )
        elif symbol_owner is not None and symbol_owner != isin_owner:
            raise InstrumentIdentityConflictError(
                "The manifest ISIN and provider symbol resolve to different instruments."
            )
        else:
            self._lock("instrument_identifier_owner", provider_id, isin_owner, "BRSAPI_L18")
            current = self._get_instrument(isin_owner)
            self._assert_instrument_identity(current, definition, venue_id)
            if symbol_owner is None:
                if definition.previous_symbol is None or definition.rename_effective_from is None:
                    raise IdentifierRenameConflictError(
                        "An existing ISIN may receive a new symbol only through an explicit rename."
                    )
                identifiers_created, identifiers_closed = self._apply_rename(
                    definition,
                    provider_id=provider_id,
                    instrument_id=current.instrument_id,
                )
                current = self._get_instrument(current.instrument_id)

        specification_created = self._apply_specification(current.instrument_id, definition)
        unchanged = not created and identifiers_created == 0 and specification_created == 0
        return InstrumentWriteResult(
            instrument=current,
            instrument_created=created,
            identifier_created=identifiers_created,
            identifier_closed=identifiers_closed,
            specification_created=specification_created,
            unchanged=unchanged,
        )

    def _apply_rename(
        self,
        definition: InstrumentDefinition,
        *,
        provider_id: int,
        instrument_id: int,
    ) -> tuple[int, int]:
        assert definition.previous_symbol is not None
        assert definition.rename_effective_from is not None
        self._lock_identifier_values(
            provider_id,
            (
                ("BRSAPI_L18", definition.previous_symbol),
                ("BRSAPI_L18", definition.provider_symbol),
            ),
        )
        old = self._active_identifier(
            provider_id,
            "BRSAPI_L18",
            definition.previous_symbol,
            definition.rename_effective_from,
        )
        new = self._active_identifier(
            provider_id,
            "BRSAPI_L18",
            definition.provider_symbol,
            definition.rename_effective_from,
        )
        if new is not None:
            if new.instrument_id == instrument_id:
                return 0, 0
            raise IdentifierRenameConflictError(
                "The target rename symbol belongs to another instrument."
            )
        if old is None or old.instrument_id != instrument_id:
            raise IdentifierRenameConflictError(
                "The declared previous symbol is not active for the ISIN owner."
            )
        if old.valid_from is None or old.valid_from >= definition.rename_effective_from:
            raise IdentifierRenameConflictError(
                "Rename effective time must follow the old identifier start."
            )
        closed = self._one(
            update(instrument_identifier)
            .where(
                instrument_identifier.c.provider_id == provider_id,
                instrument_identifier.c.identifier_type == "BRSAPI_L18",
                instrument_identifier.c.identifier_value == definition.previous_symbol,
                instrument_identifier.c.valid_from == old.valid_from,
                instrument_identifier.c.valid_to.is_(None),
            )
            .values(valid_to=definition.rename_effective_from)
            .returning(*instrument_identifier.c)
        )
        del closed
        self._insert_identifier(
            provider_id=provider_id,
            identifier_type="BRSAPI_L18",
            identifier_value=definition.provider_symbol,
            valid_from=definition.rename_effective_from,
            instrument_id=instrument_id,
            is_primary=True,
        )
        self._one(
            update(instrument)
            .where(instrument.c.instrument_id == instrument_id)
            .values(canonical_symbol=definition.provider_symbol)
            .returning(*instrument.c)
        )
        return 1, 1

    def _apply_specification(self, instrument_id: int, definition: InstrumentDefinition) -> int:
        self._lock("instrument_specification", instrument_id)
        rows = self._all(
            select(instrument_spec_version)
            .where(instrument_spec_version.c.instrument_id == instrument_id)
            .order_by(instrument_spec_version.c.effective_from)
        )
        expected = self._specification_values(instrument_id, definition)
        for row in rows:
            if row["effective_from"] == definition.spec_effective_from:
                self._assert_equal(row, expected, "instrument specification", str(instrument_id))
                return 0
        if not rows:
            self._one(
                insert(instrument_spec_version)
                .values(**expected)
                .returning(*instrument_spec_version.c)
            )
            return 1
        latest = rows[-1]
        if self._same_specification(latest, expected):
            return 0
        if definition.spec_effective_from <= latest["effective_from"]:
            raise InstrumentSpecConflictError(
                "Specification changes cannot rewrite historical versions."
            )
        if latest["effective_to"] is not None:
            raise InstrumentSpecConflictError(
                "A closed specification history cannot be extended implicitly."
            )
        self._one(
            update(instrument_spec_version)
            .where(
                instrument_spec_version.c.instrument_id == instrument_id,
                instrument_spec_version.c.effective_from == latest["effective_from"],
                instrument_spec_version.c.effective_to.is_(None),
            )
            .values(effective_to=definition.spec_effective_from)
            .returning(*instrument_spec_version.c)
        )
        self._one(
            insert(instrument_spec_version).values(**expected).returning(*instrument_spec_version.c)
        )
        return 1

    def _specification_values(
        self, instrument_id: int, definition: InstrumentDefinition
    ) -> dict[str, object]:
        return {
            "instrument_id": instrument_id,
            "effective_from": definition.spec_effective_from,
            "effective_to": None,
            "price_tick": definition.price_tick,
            "quantity_step": definition.quantity_step,
            "lot_size": definition.lot_size,
            "contract_multiplier": definition.contract_multiplier,
            "price_scale": definition.price_scale,
            "quantity_scale": definition.quantity_scale,
            "lower_price_limit": definition.lower_price_limit,
            "upper_price_limit": definition.upper_price_limit,
            "shares_outstanding": definition.shares_outstanding,
            "metadata": definition.metadata,
        }

    def _same_specification(self, row: RowMapping, expected: dict[str, object]) -> bool:
        return all(
            row[name] == value
            for name, value in expected.items()
            if name not in {"effective_from", "effective_to"}
        )

    def _assert_instrument_identity(
        self, current: Instrument, definition: InstrumentDefinition, venue_id: int
    ) -> None:
        expected = {
            "asset_type_code": definition.asset_type_code,
            "venue_id": venue_id,
            "quote_currency_code": definition.currency_code,
            "status": definition.status,
            "active_from": definition.active_from,
            "active_to": definition.active_to,
        }
        self._assert_equal(current.model_dump(), expected, "instrument", definition.stable_key)

    def _get_instrument(self, instrument_id: int) -> Instrument:
        row = self._one(select(instrument).where(instrument.c.instrument_id == instrument_id))
        return Instrument.model_validate(dict(row))

    def _identifier_owner(
        self, provider_id: int, identifier_type: str, identifier_value: str, as_of: datetime
    ) -> int | None:
        require_aware_datetime(as_of)
        rows = self._all(
            select(instrument_identifier.c.instrument_id)
            .where(
                instrument_identifier.c.provider_id == provider_id,
                instrument_identifier.c.identifier_type == identifier_type,
                instrument_identifier.c.identifier_value == identifier_value,
                instrument_identifier.c.valid_from <= as_of,
                or_(
                    instrument_identifier.c.valid_to.is_(None),
                    instrument_identifier.c.valid_to > as_of,
                ),
            )
            .order_by(instrument_identifier.c.valid_from.desc())
            .limit(2)
        )
        if len(rows) > 1:
            raise InstrumentIdentityConflictError("Multiple active identifier intervals exist.")
        return None if not rows else int(rows[0]["instrument_id"])

    def _active_identifier(
        self, provider_id: int, identifier_type: str, identifier_value: str, as_of: datetime
    ) -> InstrumentIdentifier | None:
        row = self._one_or_none(
            select(instrument_identifier)
            .where(
                instrument_identifier.c.provider_id == provider_id,
                instrument_identifier.c.identifier_type == identifier_type,
                instrument_identifier.c.identifier_value == identifier_value,
                instrument_identifier.c.valid_from <= as_of,
                or_(
                    instrument_identifier.c.valid_to.is_(None),
                    instrument_identifier.c.valid_to > as_of,
                ),
            )
            .order_by(instrument_identifier.c.valid_from.desc())
            .limit(2)
        )
        return None if row is None else InstrumentIdentifier.model_validate(dict(row))

    def _insert_identifier(
        self,
        *,
        provider_id: int,
        identifier_type: str,
        identifier_value: str,
        valid_from: datetime,
        instrument_id: int,
        is_primary: bool,
    ) -> None:
        require_aware_datetime(valid_from)
        self._one(
            insert(instrument_identifier)
            .values(
                provider_id=provider_id,
                identifier_type=identifier_type,
                identifier_value=identifier_value,
                valid_from=valid_from,
                instrument_id=instrument_id,
                is_primary=is_primary,
                metadata={},
            )
            .returning(*instrument_identifier.c)
        )

    def _lock_identifier_values(
        self, provider_id: int, values: tuple[tuple[str, str], ...]
    ) -> None:
        for identifier_type, identifier_value in sorted(values):
            statement = text(
                "SELECT pg_catalog.pg_advisory_xact_lock("
                "pg_catalog.hashtextextended("
                "pg_catalog.jsonb_build_array("
                "'catalog.instrument_identifier', CAST(:provider_id AS BIGINT), "
                "CAST(:identifier_type AS TEXT), CAST(:identifier_value AS TEXT)"
                ")::text, 0))"
            )
            self._execute(
                statement,
                {
                    "provider_id": provider_id,
                    "identifier_type": identifier_type,
                    "identifier_value": identifier_value,
                },
            )

    def _lock(self, *parts: object) -> None:
        self._execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": "|".join(str(part) for part in parts)},
        )

    def _assert_equal(
        self,
        row: RowMapping | Mapping[str, object],
        expected: Mapping[str, object],
        kind: str,
        key: str,
    ) -> None:
        values = dict(row)
        differences = [name for name, value in expected.items() if values.get(name) != value]
        if differences:
            raise CatalogConflictError(
                f"Conflicting {kind} definition for {key}: {', '.join(differences)}"
            )

    def _execute(self, statement: Any, params: dict[str, object] | None = None) -> Any:
        with translate_database_errors(operation="catalog bootstrap write"):
            return self._connection.execute(statement, params or {})

    def _one(self, statement: Any) -> RowMapping:
        with translate_database_errors(operation="catalog bootstrap write"):
            return self._connection.execute(statement).mappings().one()

    def _one_or_none(self, statement: Any) -> RowMapping | None:
        with translate_database_errors(operation="catalog bootstrap read"):
            return self._connection.execute(statement).mappings().one_or_none()

    def _all(self, statement: Any) -> list[RowMapping]:
        with translate_database_errors(operation="catalog bootstrap read"):
            return list(self._connection.execute(statement).mappings().all())


__all__ = ["CatalogWriteResult", "InstrumentWriteResult", "SqlAlchemyCatalogWriterRepository"]
