"""add score resets and admin actions

Revision ID: b7f3c1a9d2e4
Revises: d60ee55ebc98
Create Date: 2026-10-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7f3c1a9d2e4'
down_revision: Union[str, Sequence[str], None] = 'd60ee55ebc98'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# BIGINT в PostgreSQL, INTEGER в SQLite (как в остальных таблицах).
BigIntType = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'score_resets',
        sa.Column('id', BigIntType, nullable=False),
        sa.Column('chat_id', BigIntType, nullable=False),
        sa.Column('admin_user_id', BigIntType, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('undone_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], onupdate='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_score_resets_chat_id_created_at', 'score_resets', ['chat_id', 'created_at']
    )
    op.create_table(
        'admin_actions',
        sa.Column('id', BigIntType, nullable=False),
        sa.Column('chat_id', BigIntType, nullable=False),
        sa.Column('user_id', BigIntType, nullable=False),
        sa.Column(
            'action',
            sa.Enum(
                'stop_game',
                'reset_scores',
                'undo_reset',
                'opt_out',
                'opt_in',
                name='admin_action',
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], onupdate='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_admin_actions_chat_id_created_at', 'admin_actions', ['chat_id', 'created_at']
    )
    with op.batch_alter_table('score_events') as batch:
        batch.add_column(sa.Column('reset_id', BigIntType, nullable=True))
    op.create_index('ix_score_events_reset_id', 'score_events', ['reset_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_score_events_reset_id', table_name='score_events')
    with op.batch_alter_table('score_events') as batch:
        batch.drop_column('reset_id')
    op.drop_index('ix_admin_actions_chat_id_created_at', table_name='admin_actions')
    op.drop_table('admin_actions')
    op.drop_index('ix_score_resets_chat_id_created_at', table_name='score_resets')
    op.drop_table('score_resets')
