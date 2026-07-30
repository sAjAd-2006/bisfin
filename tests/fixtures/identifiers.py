"""Collision-resistant identifiers constrained to database column widths."""

from uuid import uuid4


def unique_code(prefix: str, *, max_length: int = 64) -> str:
    """Return an uppercase test code that is safe for catalog identifiers."""

    suffix = uuid4().hex.upper()
    available = max_length - len(suffix) - 1
    if available < 1:
        raise ValueError("max_length is too small for a unique test code")
    return f"{prefix[:available]}_{suffix}"
