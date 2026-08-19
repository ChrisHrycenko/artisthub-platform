"""phase7e: add notification table

Revision ID: phase7e_notification
Revises: phase7d_analytics
Create Date: 2026-08-19

Adds the notification table used by the Phase 7E notification consumer.
One row is created per (event_id, fan_id) pair when an artist releases
new content; a UNIQUE constraint on (event_id, fan_id) prevents
duplicate rows on Kafka re-delivery.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'phase7e_notification'
down_revision = 'phase7d_analytics'
branch_labels = None
depends_on = None


def upgrade():
    """Create the notification table."""
    op.create_table(
        'notification',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_id', sa.String(length=36), nullable=False),
        sa.Column('fan_id', sa.Integer(), nullable=False),
        sa.Column('artist_id', sa.Integer(), nullable=False),
        sa.Column('release_id', sa.Integer(), nullable=True),
        sa.Column('notification_type', sa.String(length=50), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'event_id', 'fan_id',
            name='uq_notification_event_fan',
        ),
    )
    op.create_index(
        'ix_notification_event_id', 'notification', ['event_id']
    )
    op.create_index(
        'ix_notification_fan_id', 'notification', ['fan_id']
    )
    op.create_index(
        'ix_notification_artist_id', 'notification', ['artist_id']
    )
    op.create_index(
        'ix_notification_status', 'notification', ['status']
    )


def downgrade():
    """Drop the notification table."""
    op.drop_index('ix_notification_status', table_name='notification')
    op.drop_index('ix_notification_artist_id', table_name='notification')
    op.drop_index('ix_notification_fan_id', table_name='notification')
    op.drop_index('ix_notification_event_id', table_name='notification')
    op.drop_table('notification')
