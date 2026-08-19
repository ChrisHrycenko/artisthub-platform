"""phase7d: add analytics_state and processed_event tables

Revision ID: phase7d_analytics
Revises: phase7c_outbox
Create Date: 2026-08-19

Adds the analytics_state and processed_event tables used by the
Phase 7D real-time analytics consumer.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'phase7d_analytics'
down_revision = 'phase7c_outbox'
branch_labels = None
depends_on = None


def upgrade():
    """Create analytics_state and processed_event tables."""
    op.create_table(
        'analytics_state',
        sa.Column('artist_id', sa.Integer(), nullable=False),
        sa.Column('follower_count', sa.Integer(), nullable=False),
        sa.Column('release_count', sa.Integer(), nullable=False),
        sa.Column('post_count', sa.Integer(), nullable=False),
        sa.Column('merch_count', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('artist_id'),
    )

    op.create_table(
        'processed_event',
        sa.Column('event_id', sa.String(length=36), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('topic', sa.String(length=255), nullable=False),
        sa.Column('partition', sa.Integer(), nullable=False),
        sa.Column('offset', sa.Integer(), nullable=False),
        sa.Column('artist_id', sa.Integer(), nullable=True),
        sa.Column('processed_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('event_id'),
    )


def downgrade():
    """Drop analytics_state and processed_event tables."""
    op.drop_table('processed_event')
    op.drop_table('analytics_state')
