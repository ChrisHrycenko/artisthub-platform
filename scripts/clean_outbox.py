"""
clean_outbox.py
---------------
One-shot script to delete malformed event_outbox rows where required int fields
(e.g. follow_id, post_id, release_id) are null.

These rows were produced before the db.session.flush() fix in Phase 7G and
can never be successfully published. Deleting them unblocks the relay.

Usage (run inside the backend container):
    docker exec docker-backend-1 python3 /app/scripts/clean_outbox.py
"""
import json
import os
import sqlite3

DB_PATH = os.environ.get("DB_PATH", "/app/instance/artisthub.db")

conn = sqlite3.connect(DB_PATH)

rows = conn.execute(
    "SELECT id, event_type, payload FROM event_outbox WHERE published_at IS NULL"
).fetchall()

delete_ids = []
for row_id, evt_type, payload_json in rows:
    try:
        payload = json.loads(payload_json)
        inner = payload.get("payload", {})
        null_int_fields = [
            k for k, v in inner.items()
            if k.endswith("_id") and v is None
        ]
        if null_int_fields:
            delete_ids.append(row_id)
            print("Delete id=%d type=%s null=%s" % (row_id, evt_type, null_int_fields))
    except Exception as exc:
        print("Error parsing row %d: %s" % (row_id, exc))

if delete_ids:
    placeholders = ",".join(str(i) for i in delete_ids)
    conn.execute("DELETE FROM event_outbox WHERE id IN (%s)" % placeholders)
    conn.commit()
    print("Deleted %d malformed outbox rows." % len(delete_ids))
else:
    print("No malformed rows found.")

remaining = conn.execute(
    "SELECT id, event_type, published_at IS NOT NULL FROM event_outbox ORDER BY id"
).fetchall()
print("Remaining: %d rows" % len(remaining))
for r in remaining:
    print("  id=%s type=%s published=%s" % (r[0], r[1], bool(r[2])))

conn.close()
