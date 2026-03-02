"""Simplify partners (nullable password) and add is_admin to accounts

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-03-02
"""
from alembic import op
import sqlalchemy as sa

revision = "i9j0k1l2m3n4"
down_revision = "h8i9j0k1l2m3"
branch_labels = None
depends_on = None


def upgrade():
    # Make partner password_hash nullable (partners no longer log in)
    op.alter_column(
        "partners",
        "password_hash",
        existing_type=sa.String(255),
        nullable=True,
    )

    # Add is_admin flag to accounts
    op.add_column(
        "accounts",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade():
    op.drop_column("accounts", "is_admin")

    op.alter_column(
        "partners",
        "password_hash",
        existing_type=sa.String(255),
        nullable=False,
    )
