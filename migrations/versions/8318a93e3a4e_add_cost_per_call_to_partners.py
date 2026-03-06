"""Add cost_per_call to partners

Revision ID: 8318a93e3a4e
Revises: c971712c3690
Create Date: 2026-03-06 22:50:44.886894

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8318a93e3a4e'
down_revision = 'c971712c3690'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('partners', sa.Column('cost_per_call', sa.Numeric(precision=10, scale=2), server_default='0'))


def downgrade():
    op.drop_column('partners', 'cost_per_call')
