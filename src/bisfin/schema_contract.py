"""Package-owned constants shared by migration tooling and runtime health checks."""

from typing import Final

ALEMBIC_HEAD_REVISION: Final[str] = "0004"

__all__ = ["ALEMBIC_HEAD_REVISION"]
