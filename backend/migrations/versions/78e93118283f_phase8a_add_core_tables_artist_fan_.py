"""phase8a: add core tables (artist, fan, release, post, merch, follow)

Revision ID: 78e93118283f
Revises: phase7e_notification
Create Date: 2026-08-21

Adds the six core ArtistHub domain tables that were previously created
by db.create_all() and had no Alembic migration.

Phase 8A makes PostgreSQL the production database.  For a fresh
PostgreSQL deployment the full migration chain is:

  phase7c_outbox       → event_outbox
  phase7d_analytics    → analytics_state, processed_event
  phase7e_notification → notification
  78e93118283f         → artist, fan, follow, merch_product,
                          music_release, social_post

SQLite note:
  On SQLite (development / test) these tables are still created by
  db.create_all() inside create_app(), which is only called when
  DATABASE_URL starts with 'sqlite'.  The two paths are intentionally
  separate:
    SQLite    → db.create_all()  (fast, zero-config, test-friendly)
    PostgreSQL → flask db upgrade (schema-tracked, production-safe)

Downgrade:
  Drops all six tables in reverse dependency order (follow before fan/
  artist, child tables before parent tables).
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '78e93118283f'
down_revision = 'phase7e_notification'
branch_labels = None
depends_on = None


def upgrade():
    """Create core ArtistHub domain tables."""
    # ------------------------------------------------------------------ #
    # artist — must exist before fan-owned and artist-owned tables.       #
    # ------------------------------------------------------------------ #
    op.create_table(
        'artist',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=False),
        sa.Column('bio', sa.Text(), nullable=True),
        sa.Column('profile_image_url', sa.String(length=500), nullable=True),
        sa.Column('genre', sa.String(length=100), nullable=True),
        sa.Column('location', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email', name='uq_artist_email'),
    )

    # ------------------------------------------------------------------ #
    # fan                                                                  #
    # ------------------------------------------------------------------ #
    op.create_table(
        'fan',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('username', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email', name='uq_fan_email'),
        sa.UniqueConstraint('username', name='uq_fan_username'),
    )

    # ------------------------------------------------------------------ #
    # follow — references artist and fan; must come after both.           #
    # ------------------------------------------------------------------ #
    op.create_table(
        'follow',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fan_id', sa.Integer(), nullable=False),
        sa.Column('artist_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['artist_id'], ['artist.id'], ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['fan_id'], ['fan.id'], ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('fan_id', 'artist_id', name='uq_follow'),
    )
    op.create_index(
        'ix_follow_artist_id', 'follow', ['artist_id'], unique=False,
    )
    op.create_index(
        'ix_follow_fan_id', 'follow', ['fan_id'], unique=False,
    )

    # ------------------------------------------------------------------ #
    # merch_product — references artist.                                  #
    # ------------------------------------------------------------------ #
    op.create_table(
        'merch_product',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('artist_id', sa.Integer(), nullable=False),
        sa.Column('product_name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('image_url', sa.String(length=500), nullable=True),
        sa.Column('inventory_quantity', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['artist_id'], ['artist.id'], ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_merch_product_artist_id', 'merch_product', ['artist_id'],
        unique=False,
    )

    # ------------------------------------------------------------------ #
    # music_release — references artist.                                  #
    # ------------------------------------------------------------------ #
    op.create_table(
        'music_release',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('artist_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('release_type', sa.String(length=50), nullable=False),
        sa.Column('genre', sa.String(length=100), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('artwork_url', sa.String(length=500), nullable=True),
        sa.Column('streaming_url', sa.String(length=500), nullable=True),
        sa.Column('release_date', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['artist_id'], ['artist.id'], ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_music_release_artist_id', 'music_release', ['artist_id'],
        unique=False,
    )

    # ------------------------------------------------------------------ #
    # social_post — references artist.                                    #
    # ------------------------------------------------------------------ #
    op.create_table(
        'social_post',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('artist_id', sa.Integer(), nullable=False),
        sa.Column('body', sa.String(length=2000), nullable=False),
        sa.Column('image_url', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ['artist_id'], ['artist.id'], ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_social_post_artist_id', 'social_post', ['artist_id'],
        unique=False,
    )


def downgrade():
    """Drop core ArtistHub domain tables in reverse dependency order."""
    # Child tables first (foreign key dependants).
    op.drop_index('ix_social_post_artist_id', table_name='social_post')
    op.drop_table('social_post')

    op.drop_index(
        'ix_music_release_artist_id', table_name='music_release',
    )
    op.drop_table('music_release')

    op.drop_index(
        'ix_merch_product_artist_id', table_name='merch_product',
    )
    op.drop_table('merch_product')

    op.drop_index('ix_follow_fan_id', table_name='follow')
    op.drop_index('ix_follow_artist_id', table_name='follow')
    op.drop_table('follow')

    # Parent tables last.
    op.drop_table('fan')
    op.drop_table('artist')
