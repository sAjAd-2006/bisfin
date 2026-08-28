"""Stable calendar canonicalization errors."""

from bisfin.db.errors import BisfinError


class CalendarConflictError(BisfinError):
    code = "CALENDAR_CONFLICT"


__all__ = ["CalendarConflictError"]
