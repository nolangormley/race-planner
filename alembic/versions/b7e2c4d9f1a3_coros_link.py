"""COROS Training Hub link on the user: session token, region, sync state.

Revision ID: b7e2c4d9f1a3
Revises: 9d1f3a7c2e88
Create Date: 2026-10-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7e2c4d9f1a3'
down_revision: Union[str, Sequence[str], None] = '9d1f3a7c2e88'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('coros_user_id', sa.String(), nullable=True))
    op.add_column('users', sa.Column('coros_access_token', sa.String(), nullable=True))
    op.add_column('users', sa.Column('coros_region', sa.String(length=8), nullable=True))
    op.add_column('users', sa.Column('coros_linked_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('coros_synced_at', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('coros_error', sa.String(), nullable=True))


def downgrade() -> None:
    for column in ('coros_error', 'coros_synced_at', 'coros_linked_at', 'coros_region', 'coros_access_token', 'coros_user_id'):
        op.drop_column('users', column)
