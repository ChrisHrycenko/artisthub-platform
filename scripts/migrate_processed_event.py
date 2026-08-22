"""
migrate_processed_event.py
--------------------------
One-shot migration: alter the processed_event table to use a composite
PRIMARY KEY (event_id, consumer_group) instead of a single-column PK.

SQLite does not support ALTER TABLE to modify a primary key.
The standard approach is to rename, recreate, copy, and drop.

Usage (run from within the backend container or the repo backend/):
    python3 scripts/migrate_processed_event.py [/path/to/artisthub.db]
"""
import os
import sqlite3
import sys

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "/app/instance/artisthub.db"

if not os.path.exists(DB_PATH):
    print("DB not found at %s — nothing to migrate." % DB_PATH)
    sys.exit(0)

conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA journal_mode=WAL")

# Check if consumer_group column already exists
cols = [row[1] for row in conn.execute("PRAGMA table_info(processed_event)").fetchall()]
if "consumer_group" in cols:
    print("consumer_group column already exists — migration already applied.")
    conn.close()
    sys.exit(0)

print("Migrating processed_event table …")

# Step 1: rename old table
conn.execute("ALTER TABLE processed_event RENAME TO _processed_event_old")

# Step 2: create new table with composite PK
conn.execute("""
CREATE TABLE processed_event (
    event_id       VARCHAR(36)  NOT NULL,
    consumer_group VARCHAR(100) NOT NULL,
    event_type     VARCHAR(100) NOT NULL,
    topic          VARCHAR(255) NOT NULL,
    partition      INTEGER      NOT NULL,
    "offset"       INTEGER      NOT NULL,
    artist_id      INTEGER,
    processed_at   DATETIME     NOT NULL,
    PRIMARY KEY (event_id, consumer_group)
)
""")

# Step 3: copy existing rows, defaulting consumer_group to analytics
conn.execute("""
INSERT INTO processed_event (
    event_id, consumer_group, event_type, topic, partition, "offset",
    artist_id, processed_at
)
SELECT event_id, 'artisthub.analytics.v1', event_type, topic, partition,
       "offset", artist_id, processed_at
FROM _processed_event_old
""")

count = conn.execute("SELECT COUNT(*) FROM processed_event").fetchone()[0]
print("  Migrated %d rows (consumer_group='artisthub.analytics.v1')." % count)

# Step 4: drop old table
conn.execute("DROP TABLE _processed_event_old")

conn.commit()
conn.close()
print("Migration complete.")
