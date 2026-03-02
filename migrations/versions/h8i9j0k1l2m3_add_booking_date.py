"""Add booking_date column to calls table

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-03-02 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'h8i9j0k1l2m3'
down_revision = 'g7h8i9j0k1l2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('calls', sa.Column('booking_date', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('calls', 'booking_date')
