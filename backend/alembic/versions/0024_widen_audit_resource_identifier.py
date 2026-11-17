"""Widen the audit log resource identifier to the podcast slug length.

Ops podcast routes record the slug as the audit entry's resource identifier.
Slugs hold up to 255 characters, so a 120-character identifier column made
PostgreSQL reject the audit insert (and the whole request) for longer slugs.

Downgrading cuts identifiers longer than 120 characters to their first 120
characters so the narrower column can hold them; the full slug stays in the
entry's summary text.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op  # type: ignore[attr-defined]

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_LENGTH = 120
_NEW_LENGTH = 255


def upgrade() -> None:
    """Widen ``audit_logs.resource_identifier`` to 255 characters."""
    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column(
            "resource_identifier",
            existing_type=sa.String(length=_OLD_LENGTH),
            type_=sa.String(length=_NEW_LENGTH),
            existing_nullable=True,
        )


def downgrade() -> None:
    """Narrow the column back to 120 characters, cutting longer identifiers."""
    op.execute(
        sa.text(
            "UPDATE audit_logs"
            " SET resource_identifier = substr(resource_identifier, 1, :length)"
            " WHERE length(resource_identifier) > :length",
        ).bindparams(length=_OLD_LENGTH),
    )
    with op.batch_alter_table("audit_logs") as batch:
        batch.alter_column(
            "resource_identifier",
            existing_type=sa.String(length=_NEW_LENGTH),
            type_=sa.String(length=_OLD_LENGTH),
            existing_nullable=True,
        )
