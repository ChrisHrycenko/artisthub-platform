#!/usr/bin/env bash
# =============================================================================
# scripts/observe-kafka.sh
#
# ArtistHub Kafka observability helper.
# Prints a human-readable snapshot of:
#   - Topics (partitions, offsets, retention)
#   - Consumer groups and per-partition lag
#   - Schema Registry subjects and versions
#   - Recent Avro event payloads (decoded JSON header info)
#   - Outbox relay status from the database
#
# Requires the full stack to be running.
# Run from the repository root:
#     bash scripts/observe-kafka.sh
#
# Uses: docker, rpk (inside the Redpanda container), curl, sqlite3
# =============================================================================

set -euo pipefail

CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'; YELLOW='\033[1;33m'
GREEN='\033[0;32m'

sec() { echo -e "\n${BOLD}${CYAN}─── $* ───${RESET}"; }
SR="http://localhost:8081"
CONTAINER="artisthub-redpanda"
BACKEND="artisthub-backend"

sec "Topics"
docker exec "$CONTAINER" rpk topic list 2>/dev/null || echo "(rpk unavailable)"

sec "Topic Details — Message Counts"
for topic in artisthub.social artisthub.catalog artisthub.identity artisthub.deadletter; do
  echo -e "\n${YELLOW}$topic${RESET}"
  docker exec "$CONTAINER" rpk topic describe "$topic" 2>/dev/null || echo "  (topic not found)"
done

sec "Consumer Groups"
docker exec "$CONTAINER" rpk group list 2>/dev/null || echo "(rpk unavailable)"

sec "Consumer Group Lag: artisthub.analytics.v1"
docker exec "$CONTAINER" rpk group describe artisthub.analytics.v1 2>/dev/null \
  || echo "(group not yet committed)"

sec "Consumer Group Lag: artisthub.notifications.v1"
docker exec "$CONTAINER" rpk group describe artisthub.notifications.v1 2>/dev/null \
  || echo "(group not yet committed)"

sec "Schema Registry — All Subjects"
curl -fsS "$SR/subjects" -H "Accept: application/vnd.schemaregistry.v1+json" \
  | python3 -c "import sys,json; [print('  ' + s) for s in sorted(json.load(sys.stdin))]" \
  || echo "(Schema Registry unreachable)"

sec "Schema Registry — Compatibility Modes"
for subject in \
  io.artisthub.events.FanFollowedArtist \
  io.artisthub.events.ArtistReleaseCreated \
  io.artisthub.events.ArtistRegistered; do
  COMPAT=$(curl -fsS "$SR/config/$subject" \
    -H "Accept: application/vnd.schemaregistry.v1+json" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('compatibilityLevel') or d.get('compatibility','?'))" \
    2>/dev/null || echo "?")
  echo "  $subject  →  $COMPAT"
done

sec "Outbox Relay Status (SQLite)"
docker exec "$BACKEND" sqlite3 /app/instance/artisthub.db \
  "SELECT event_type, COUNT(*) as total,
          SUM(CASE WHEN published_at IS NOT NULL THEN 1 ELSE 0 END) as published,
          SUM(CASE WHEN published_at IS NULL THEN 1 ELSE 0 END) as pending
   FROM event_outbox
   GROUP BY event_type
   ORDER BY event_type;" 2>/dev/null \
  || echo "(DB query unavailable)"

sec "Analytics State"
docker exec "$BACKEND" sqlite3 /app/instance/artisthub.db \
  "SELECT artist_id, follower_count, release_count, post_count, merch_count
   FROM analytics_state
   ORDER BY artist_id LIMIT 20;" 2>/dev/null \
  || echo "(DB query unavailable)"

sec "Recent Processed Events (last 10)"
docker exec "$BACKEND" sqlite3 /app/instance/artisthub.db \
  "SELECT event_id, event_type, topic, partition, offset
   FROM processed_event
   ORDER BY rowid DESC LIMIT 10;" 2>/dev/null \
  || echo "(DB query unavailable)"

sec "Pending Notifications (first 10)"
docker exec "$BACKEND" sqlite3 /app/instance/artisthub.db \
  "SELECT id, fan_id, artist_id, notification_type, status, created_at
   FROM notification
   WHERE status='pending'
   ORDER BY id DESC LIMIT 10;" 2>/dev/null \
  || echo "(DB query unavailable)"

sec "Recent Dead-Letter Messages (last 5)"
docker exec "$CONTAINER" \
  rpk topic consume artisthub.deadletter --offset start --num 5 2>/dev/null \
  | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        msg = json.loads(line)
        val = msg.get('value','')
        try:
            dl = json.loads(val)
            print(f\"  topic={dl.get('original_topic')} reason={dl.get('failure_reason','?')[:60]}\")
        except Exception:
            print(f'  raw: {val[:80]}')
    except Exception:
        pass
" 2>/dev/null || echo "(no dead-letter messages or rpk unavailable)"

echo -e "\n${GREEN}${BOLD}Observability snapshot complete.${RESET}"
echo "For live UI: http://localhost:8082 (Redpanda Console)"
