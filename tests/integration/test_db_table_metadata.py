"""Compare the query-only Core mappings with live PostgreSQL catalogs."""

from __future__ import annotations

from typing import cast

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Date,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, VARCHAR
from sqlalchemy.engine import Engine
from sqlalchemy.sql.type_api import TypeEngine

from bisfin.db.tables import MAPPED_TABLES, bar_series


def _type_signature(column_type: TypeEngine[object]) -> tuple[object, ...]:
    if isinstance(column_type, TIMESTAMP):
        return ("timestamp", column_type.timezone, column_type.precision)
    if isinstance(column_type, Numeric):
        return ("numeric", column_type.precision, column_type.scale)
    if isinstance(column_type, JSONB):
        return ("jsonb",)
    if isinstance(column_type, CHAR):
        return ("char", column_type.length)
    if isinstance(column_type, VARCHAR):
        return ("varchar", column_type.length)
    if isinstance(column_type, Text):
        return ("text",)
    if isinstance(column_type, BigInteger):
        return ("bigint",)
    if isinstance(column_type, SmallInteger):
        return ("smallint",)
    if isinstance(column_type, Integer):
        return ("integer",)
    if isinstance(column_type, Boolean):
        return ("boolean",)
    if isinstance(column_type, Date):
        return ("date",)
    if isinstance(column_type, String):
        return ("string", column_type.length)
    return (type(column_type).__name__, str(column_type))


def test_core_mappings_match_live_columns_nullability_types_and_primary_keys(
    db_engine: Engine,
) -> None:
    inspector = inspect(db_engine)
    expected_physical_tables = {table.fullname for table in MAPPED_TABLES}
    assert expected_physical_tables == {
        "catalog.data_provider",
        "catalog.instrument",
        "catalog.instrument_identifier",
        "catalog.instrument_spec_version",
        "ingest.ingestion_batch",
        "ingest.raw_event",
        "market.bar_series",
        "market.bar_revision",
    }

    for table in MAPPED_TABLES:
        reflected = {
            str(column["name"]): column
            for column in inspector.get_columns(table.name, schema=table.schema)
        }
        assert set(reflected) == set(table.c.keys()), table.fullname

        for mapped_column in table.c:
            actual_column = reflected[mapped_column.name]
            assert bool(actual_column["nullable"]) is mapped_column.nullable, (
                table.fullname,
                mapped_column.name,
            )
            actual_type = cast("TypeEngine[object]", actual_column["type"])
            mapped_type = cast("TypeEngine[object]", mapped_column.type)
            assert _type_signature(actual_type) == _type_signature(mapped_type), (
                table.fullname,
                mapped_column.name,
                actual_type,
                mapped_type,
            )

        reflected_pk = inspector.get_pk_constraint(table.name, schema=table.schema)
        assert tuple(reflected_pk["constrained_columns"]) == tuple(
            column.name for column in table.primary_key.columns
        ), table.fullname


def test_bar_series_maps_the_composite_adjustment_set_foreign_key(db_engine: Engine) -> None:
    mapped_foreign_keys = {
        (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in bar_series.foreign_key_constraints
    }
    expected_mapping = (
        ("adjustment_set_id", "instrument_id"),
        (
            "catalog.adjustment_set.adjustment_set_id",
            "catalog.adjustment_set.instrument_id",
        ),
    )
    assert expected_mapping in mapped_foreign_keys

    reflected_foreign_keys = inspect(db_engine).get_foreign_keys(
        bar_series.name,
        schema=bar_series.schema,
    )
    assert any(
        tuple(foreign_key["constrained_columns"]) == ("adjustment_set_id", "instrument_id")
        and foreign_key["referred_schema"] == "catalog"
        and foreign_key["referred_table"] == "adjustment_set"
        and tuple(foreign_key["referred_columns"]) == ("adjustment_set_id", "instrument_id")
        for foreign_key in reflected_foreign_keys
    )
