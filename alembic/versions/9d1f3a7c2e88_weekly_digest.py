"""Weekly digest email opt-in and last-sent time on the user.

Revision ID: 9d1f3a7c2e88
Revises: 7b4e2d9c1a55
Create Date: 2026-10-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9d1f3a7c2e88'
down_revision: Union[str, Sequence[str], None] = '7b4e2d9c1a55'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('weekly_digest', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('users', sa.Column('digest_sent_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'digest_sent_at')
    op.drop_column('users', 'weekly_digest')
