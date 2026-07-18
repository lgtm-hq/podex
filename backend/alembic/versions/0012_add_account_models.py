"""Add account auth and personalization models.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-19 00:34:32.831077
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # type: ignore[attr-defined]

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "account_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_signed_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_account_users_email"), "account_users", ["email"], unique=True
    )
    op.create_table(
        "account_followed_podcasts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("podcast_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["podcast_id"],
            ["podcasts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["account_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "podcast_id", name="uq_account_followed_podcasts_user_podcast"
        ),
    )
    op.create_index(
        op.f("ix_account_followed_podcasts_podcast_id"),
        "account_followed_podcasts",
        ["podcast_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_followed_podcasts_user_id"),
        "account_followed_podcasts",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "account_preferences",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("digest_enabled", sa.Boolean(), nullable=False),
        sa.Column("digest_frequency", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["account_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_account_preferences_user_id"),
        "account_preferences",
        ["user_id"],
        unique=True,
    )
    op.create_table(
        "account_saved_media",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["media.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["account_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "media_id", name="uq_account_saved_media_user_media"
        ),
    )
    op.create_index(
        op.f("ix_account_saved_media_media_id"),
        "account_saved_media",
        ["media_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_saved_media_user_id"),
        "account_saved_media",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "magic_link_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("redirect_path", sa.String(length=300), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["account_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_magic_link_tokens_expires_at"),
        "magic_link_tokens",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_magic_link_tokens_token_digest"),
        "magic_link_tokens",
        ["token_digest"],
        unique=True,
    )
    op.create_index(
        op.f("ix_magic_link_tokens_user_id"),
        "magic_link_tokens",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["account_users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_sessions_expires_at"),
        "user_sessions",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_sessions_token_digest"),
        "user_sessions",
        ["token_digest"],
        unique=True,
    )
    op.create_index(
        op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"], unique=False
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_token_digest"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_expires_at"), table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index(op.f("ix_magic_link_tokens_user_id"), table_name="magic_link_tokens")
    op.drop_index(
        op.f("ix_magic_link_tokens_token_digest"), table_name="magic_link_tokens"
    )
    op.drop_index(
        op.f("ix_magic_link_tokens_expires_at"), table_name="magic_link_tokens"
    )
    op.drop_table("magic_link_tokens")
    op.drop_index(
        op.f("ix_account_saved_media_user_id"), table_name="account_saved_media"
    )
    op.drop_index(
        op.f("ix_account_saved_media_media_id"), table_name="account_saved_media"
    )
    op.drop_table("account_saved_media")
    op.drop_index(
        op.f("ix_account_preferences_user_id"), table_name="account_preferences"
    )
    op.drop_table("account_preferences")
    op.drop_index(
        op.f("ix_account_followed_podcasts_user_id"),
        table_name="account_followed_podcasts",
    )
    op.drop_index(
        op.f("ix_account_followed_podcasts_podcast_id"),
        table_name="account_followed_podcasts",
    )
    op.drop_table("account_followed_podcasts")
    op.drop_index(op.f("ix_account_users_email"), table_name="account_users")
    op.drop_table("account_users")
