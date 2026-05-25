"""Add anonymous search analytics events.

Revision ID: 032_add_search_analytics_events
Revises: 031_add_editorial_collections
Create Date: 2026-05-24

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "032_add_search_analytics_events"
down_revision: str | None = "031_add_editorial_collections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create storage for query and selection relevance signals."""
    op.create_table(
        "search_analytics_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("query", sa.String(length=200), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),
        sa.Column("selected_type", sa.String(length=20), nullable=True),
        sa.Column("selected_id", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_search_analytics_events_event_type",
        "search_analytics_events",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        "ix_search_analytics_events_query",
        "search_analytics_events",
        ["query"],
        unique=False,
    )
    op.create_index(
        "ix_search_analytics_events_selected_type",
        "search_analytics_events",
        ["selected_type"],
        unique=False,
    )
    op.create_index(
        "ix_search_analytics_events_created_at",
        "search_analytics_events",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop anonymous search analytics storage."""
    op.drop_index(
        "ix_search_analytics_events_created_at",
        table_name="search_analytics_events",
    )
    op.drop_index(
        "ix_search_analytics_events_selected_type",
        table_name="search_analytics_events",
    )
    op.drop_index(
        "ix_search_analytics_events_query",
        table_name="search_analytics_events",
    )
    op.drop_index(
        "ix_search_analytics_events_event_type",
        table_name="search_analytics_events",
    )
    op.drop_table("search_analytics_events")
