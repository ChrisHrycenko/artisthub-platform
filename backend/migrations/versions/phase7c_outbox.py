"""phase7c: add event_outbox table

Revision ID: phase7c_outbox
Revises:
Create Date: 2026-08-19

Adds the event_outbox table used by the Transactional Outbox Pattern
(Phase 7C). Events are written here atomically with the business object
mutation and published to Kafka by the outbox relay.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'phase7c_outbox'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    """Create the event_outbox table."""
    op.create_table(
        'event_outbox',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_id', sa.String(length=36), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('event_version', sa.String(length=10), nullable=False),
        sa.Column('topic', sa.String(length=255), nullable=False),
        sa.Column('message_key', sa.String(length=255), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),
        sa.Column('correlation_id', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('publish_attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id'),
    )
    op.create_index(
        'ix_event_outbox_event_id',
        'event_outbox',
        ['event_id'],
        unique=True,
    )
    op.create_index(
        'ix_event_outbox_published_at',
        'event_outbox',
        ['published_at'],
        unique=False,
    )


def downgrade():
    """Drop the event_outbox table."""
    op.drop_index('ix_event_outbox_published_at', table_name='event_outbox')
    op.drop_index('ix_event_outbox_event_id', table_name='event_outbox')
    op.drop_table('event_outbox')
