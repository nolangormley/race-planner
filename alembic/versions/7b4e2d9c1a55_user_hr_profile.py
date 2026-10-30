"""Resting and max heart rate on the user.

Revision ID: 7b4e2d9c1a55
Revises: 3f2c9a1b7d4e
Create Date: 2026-10-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7b4e2d9c1a55'
down_revision: Union[str, Sequence[str], None] = '3f2c9a1b7d4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('resting_hr', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('max_hr', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'max_hr')
    op.drop_column('users', 'resting_hr')
