"""The COROS resting heart rate the metrics use, stored on the user.

Revision ID: d2f7b3c8e5a1
Revises: c4a9e1f7b2d6
Create Date: 2026-10-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2f7b3c8e5a1'
down_revision: Union[str, Sequence[str], None] = 'c4a9e1f7b2d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('coros_resting_hr', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'coros_resting_hr')
