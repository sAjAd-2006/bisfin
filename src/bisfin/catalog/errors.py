"""Stable, bounded catalog canonicalization failures."""

from bisfin.db.errors import BisfinError


class CatalogConflictError(BisfinError):
    """A manifest definition conflicts with immutable canonical catalog state."""

    code = "CATALOG_CONFLICT"


class InstrumentIdentityConflictError(CatalogConflictError):
    code = "INSTRUMENT_IDENTITY_CONFLICT"


class IdentifierRenameConflictError(CatalogConflictError):
    code = "IDENTIFIER_RENAME_CONFLICT"


class InstrumentSpecConflictError(CatalogConflictError):
    code = "INSTRUMENT_SPEC_CONFLICT"


__all__ = [
    "CatalogConflictError",
    "IdentifierRenameConflictError",
    "InstrumentIdentityConflictError",
    "InstrumentSpecConflictError",
]
