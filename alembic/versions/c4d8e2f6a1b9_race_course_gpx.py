"""Course GPX on a race, for the pacing plan.

Revision ID: c4d8e2f6a1b9
Revises: b7e2c4d9f1a3
Create Date: 2026-10-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4d8e2f6a1b9'
down_revision: Union[str, Sequence[str], None] = 'b7e2c4d9f1a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('races', sa.Column('course_gpx', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('races', 'course_gpx')
