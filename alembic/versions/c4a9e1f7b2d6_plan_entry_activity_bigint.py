"""Plan entries hold Strava activity ids, which no longer fit in 32 bits.

Revision ID: c4a9e1f7b2d6
Revises: c4d8e2f6a1b9
Create Date: 2026-10-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4a9e1f7b2d6'
down_revision: Union[str, Sequence[str], None] = 'c4d8e2f6a1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('training_plan_entries', 'strava_activity_id', existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=True)


def downgrade() -> None:
    op.alter_column('training_plan_entries', 'strava_activity_id', existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=True)
