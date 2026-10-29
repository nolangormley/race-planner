"""Record the last Strava sync and drop the AI chat history.

Revision ID: 3f2c9a1b7d4e
Revises: 6df11eefae69
Create Date: 2026-10-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3f2c9a1b7d4e'
down_revision: Union[str, Sequence[str], None] = '6df11eefae69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('strava_last_synced_at', sa.DateTime(), nullable=True))
    op.drop_index(op.f('ix_chat_messages_plan_id'), table_name='chat_messages')
    op.drop_index(op.f('ix_chat_messages_id'), table_name='chat_messages')
    op.drop_table('chat_messages')


def downgrade() -> None:
    op.create_table('chat_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('plan_id', sa.Integer(), nullable=True),
    sa.Column('role', sa.String(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['plan_id'], ['training_plans.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_chat_messages_id'), 'chat_messages', ['id'], unique=False)
    op.create_index(op.f('ix_chat_messages_plan_id'), 'chat_messages', ['plan_id'], unique=False)
    op.drop_column('users', 'strava_last_synced_at')
