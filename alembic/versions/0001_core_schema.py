"""Register the existing core schema raw SQL migration."""

from __future__ import annotations

from collections.abc import Sequence

from migration_registry import apply_registered_migration, unsupported_downgrade

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    apply_registered_migration(revision)


def downgrade() -> None:
    unsupported_downgrade(revision)
