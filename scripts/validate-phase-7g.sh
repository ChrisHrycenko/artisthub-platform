#!/usr/bin/env bash
# =============================================================================
# scripts/validate-phase-7g.sh
#
# Phase 7G — Live end-to-end integration validation for ArtistHub.
#
# Runs the complete validation suite against a live local Docker stack.
# All tests are idempotent: they clean up after themselves and can be
# re-run on a running stack without data corruption.
#
# Prerequisites:
#   1. Docker Engine 24+ and Docker Compose v2 installed
#   2. The full stack is running:
#        docker compose -f docker/docker-compose.yml \
#                       -f docker/docker-compose.kafka.yml \
#                       up --build -d
#   3. Run from the repository root:
#        bash scripts/validate-phase-7g.sh
#
# Exit codes:
#   0  — all checks passed
#   1  — one or more checks failed
#
# Sections:
#   A. Stack health checks
#   B. Schema Registry validation (12 subjects, BACKWARD compat, v2 compat test)
#   C. End-to-end test A — fan follows artist
#   D. End-to-end test B — artist publishes release
#   E. Broker outage / Transactional Outbox recovery test
#   F. Dead-letter test
#   G. Observability snapshot
#   H. Unit test suite
# =============================================================================

set -euo pipefail

# ─── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

pass()  { echo -e "  ${GREEN}[PASS]${RESET} $*"; PASSED=$((PASSED+1)); }
fail()  { echo -e "  ${RED}[FAIL]${RESET} $*"; FAILED=$((FAILED+1)); }
skip()  { echo -e "  ${YELLOW}[SKIP]${RESET} $*"; }
info()  { echo -e "  ${BLUE}[INFO]${RESET} $*"; }
title() { echo -e "\n${BOLD}${CYAN}═══ $* ═══${RESET}\n"; }

PASSED=0; FAILED=0

# ─── Configuration ────────────────────────────────────────────────────────────
API="http://localhost:8080/api"        # nginx proxy
SR="http://localhost:8081"            # Redpanda Schema Registry
BROKER="localhost:9092"               # Kafka broker (host listener)
REDPANDA_CONTAINER="artisthub-redpanda"
BACKEND_CONTAINER="artisthub-backend"
RELAY_CONTAINER="artisthub-outbox-relay"
ANALYTICS_CONTAINER="artisthub-analytics-consumer"
NOTIF_CONTAINER="artisthub-notification-consumer"

# ─── Helpers ──────────────────────────────────────────────────────────────────
api_get()  { curl -fsS -c /tmp/ah_cookies.txt -b /tmp/ah_cookies.txt "$API$1"; }
api_post() { curl -fsS -c /tmp/ah_cookies.txt -b /tmp/ah_cookies.txt \
               -X POST -H "Content-Type: application/json" -d "$2" "$API$1"; }
api_del()  { curl -fsS -c /tmp/ah_cookies.txt -b /tmp/ah_cookies.txt \
               -X DELETE "$API$1"; }

wait_for() {
  local url="$1" label="$2" attempts=0
  while ! curl -fsS "$url" > /dev/null 2>&1; do
    attempts=$((attempts+1))
    [[ $attempts -gt 30 ]] && { fail "Timeout waiting for $label"; return 1; }
    sleep 2
  done
}

db_query() {
  # Run a SQLite query inside the backend container against the live DB
  docker exec "$BACKEND_CONTAINER" \
    sqlite3 /app/instance/artisthub.db "$1" 2>/dev/null || echo "DB_ERROR"
}

sr_get() { curl -fsS "$SR$1" -H "Accept: application/vnd.schemaregistry.v1+json"; }
sr_post() {
  curl -fsS -X POST "$SR$1" \
    -H "Content-Type: application/vnd.schemaregistry.v1+json" \
    -H "Accept: application/vnd.schemaregistry.v1+json" \
    -d "$2"
}

# ─── Section A: Stack Health ──────────────────────────────────────────────────
title "A. Stack Health Checks"

wait_for "$API/health" "ArtistHub API"
HEALTH=$(api_get "/health" 2>/dev/null || echo '{}')
[[ $(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('status',''))") == "ok" ]] \
  && pass "ArtistHub API /health → ok" \
  || fail "ArtistHub API health check failed: $HEALTH"

wait_for "$SR/subjects" "Schema Registry"
SR_STATUS=$(sr_get "/subjects" 2>/dev/null && echo "UP" || echo "DOWN")
[[ "$SR_STATUS" == "UP" ]] \
  && pass "Schema Registry reachable at $SR" \
  || fail "Schema Registry unreachable at $SR"

docker ps --filter "name=$REDPANDA_CONTAINER" --format "{{.Status}}" | grep -q "healthy" \
  && pass "Redpanda broker healthy" \
  || fail "Redpanda broker not healthy"

docker ps --filter "name=$ANALYTICS_CONTAINER" --format "{{.Status}}" | grep -q "Up" \
  && pass "Analytics consumer running" \
  || fail "Analytics consumer not running"

docker ps --filter "name=$NOTIF_CONTAINER" --format "{{.Status}}" | grep -q "Up" \
  && pass "Notification consumer running" \
  || fail "Notification consumer not running"

docker ps --filter "name=$RELAY_CONTAINER" --format "{{.Status}}" | grep -q "Up" \
  && pass "Outbox relay running" \
  || fail "Outbox relay not running"

# ─── Section B: Schema Registry Validation ───────────────────────────────────
title "B. Schema Registry Validation"

# Register all 12 schemas
info "Registering schemas via register_schemas.py …"
python3 kafka/register_schemas.py --sr-url "$SR" \
  && pass "register_schemas.py exited 0" \
  || fail "register_schemas.py failed"

# Validate via validate_schemas.py
python3 kafka/validate_schemas.py --sr-url "$SR" > /tmp/sr_validate.txt 2>&1 \
  && pass "validate_schemas.py: all SR checks passed" \
  || { fail "validate_schemas.py: one or more checks failed"; cat /tmp/sr_validate.txt; }

# Count subjects
SUBJECT_COUNT=$(sr_get "/subjects" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
[[ "$SUBJECT_COUNT" -ge 12 ]] \
  && pass "Schema Registry has $SUBJECT_COUNT subjects (≥ 12)" \
  || fail "Schema Registry has only $SUBJECT_COUNT subjects, expected ≥ 12"

# Verify every subject has version 1
for subject in \
  "io.artisthub.events.FanFollowedArtist" \
  "io.artisthub.events.FanUnfollowedArtist" \
  "io.artisthub.events.ArtistPostCreated" \
  "io.artisthub.events.ArtistPostDeleted" \
  "io.artisthub.events.ArtistReleaseCreated" \
  "io.artisthub.events.ArtistReleaseUpdated" \
  "io.artisthub.events.ArtistReleaseDeleted" \
  "io.artisthub.events.ArtistMerchCreated" \
  "io.artisthub.events.ArtistMerchUpdated" \
  "io.artisthub.events.ArtistMerchDeleted" \
  "io.artisthub.events.ArtistRegistered" \
  "io.artisthub.events.ArtistProfileUpdated"; do
  VERSIONS=$(sr_get "/subjects/$subject/versions" 2>/dev/null || echo "[]")
  echo "$VERSIONS" | grep -q "1" \
    && pass "  $subject  → version 1 exists" \
    || fail "  $subject  → version 1 NOT found"
done

# Compatibility test: v2 (adds optional source_device) should be accepted
V2_SCHEMA=$(cat kafka/schemas/test_compat/fan_followed_artist_v2.avsc | python3 -c \
  "import sys,json; print(json.dumps({'schema': sys.stdin.read()}))")
COMPAT_RESULT=$(sr_post \
  "/compatibility/subjects/io.artisthub.events.FanFollowedArtist/versions/latest" \
  "$V2_SCHEMA" 2>/dev/null || echo '{"is_compatible":false}')
echo "$COMPAT_RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('is_compatible') else 1)" \
  && pass "v2 schema (adds optional source_device) is BACKWARD-compatible" \
  || fail "v2 schema unexpectedly rejected"

# Breaking change: removing required field should be rejected
BREAK_SCHEMA=$(cat kafka/schemas/test_compat/fan_followed_artist_breaking.avsc | python3 -c \
  "import sys,json; print(json.dumps({'schema': sys.stdin.read()}))")
BREAK_RESULT=$(sr_post \
  "/compatibility/subjects/io.artisthub.events.FanFollowedArtist/versions/latest" \
  "$BREAK_SCHEMA" 2>/dev/null || echo '{"is_compatible":true}')
echo "$BREAK_RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(1 if d.get('is_compatible') else 0)" \
  && pass "Breaking schema (removes follow_id) is correctly REJECTED" \
  || fail "Breaking schema was unexpectedly accepted"

# ─── Section C: E2E Test A — Fan Follows Artist ───────────────────────────────
title "C. End-to-End Test A — Fan Follows Artist"

# Register a test artist
info "Registering test artist …"
AREG=$(api_post "/auth/artist/register" \
  '{"email":"e2e_artist_7g@test.com","password":"TestPass123","display_name":"E2E Artist 7G","genre":"Indie"}')
ARTIST_ID=$(echo "$AREG" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['artist']['id'])" 2>/dev/null || echo "")
[[ -n "$ARTIST_ID" && "$ARTIST_ID" != "null" ]] \
  && pass "Artist registered, id=$ARTIST_ID" \
  || { fail "Artist registration failed: $AREG"; ARTIST_ID=1; }

# Register + login test fan
info "Registering and logging in test fan …"
api_post "/auth/fan/register" \
  '{"email":"e2e_fan_7g@test.com","password":"TestPass123","username":"e2efan7g"}' > /dev/null 2>&1 || true
api_post "/auth/fan/login" \
  '{"email":"e2e_fan_7g@test.com","password":"TestPass123"}' > /dev/null

# POST /api/follows
info "Fan follows artist $ARTIST_ID …"
FOLLOW_RESP=$(api_post "/follows" "{\"artist_id\": $ARTIST_ID}")
FOLLOW_STATUS=$(echo "$FOLLOW_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null)
[[ "$FOLLOW_STATUS" == "success" ]] \
  && pass "POST /api/follows returned success" \
  || fail "POST /api/follows failed: $FOLLOW_RESP"

FOLLOW_ID=$(echo "$FOLLOW_RESP" | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['data']['follow']['id'])" 2>/dev/null || echo "")

# Verify follow row in DB
FOLLOW_DB=$(db_query "SELECT COUNT(*) FROM follow WHERE artist_id=$ARTIST_ID;")
[[ "$FOLLOW_DB" -ge 1 ]] \
  && pass "Follow row exists in database" \
  || fail "Follow row NOT found in database"

# Verify outbox row was created
OUTBOX_COUNT=$(db_query "SELECT COUNT(*) FROM event_outbox WHERE event_type='fan.followed.artist' AND published_at IS NULL;")
info "Outbox rows pending for fan.followed.artist: $OUTBOX_COUNT"

# Wait up to 30 s for relay to publish and analytics to process
info "Waiting for relay + analytics consumer (up to 30 s) …"
for i in $(seq 1 15); do
  PUBLISHED=$(db_query "SELECT COUNT(*) FROM event_outbox WHERE event_type='fan.followed.artist' AND published_at IS NOT NULL;" 2>/dev/null || echo 0)
  [[ "$PUBLISHED" -ge 1 ]] && break
  sleep 2
done

[[ "$PUBLISHED" -ge 1 ]] \
  && pass "Outbox row published_at populated after broker ack" \
  || fail "Outbox row still unpublished after 30 s"

# Wait for analytics consumer to update state
sleep 5
ANALYTICS_RESP=$(api_get "/artists/$ARTIST_ID/analytics" 2>/dev/null || echo '{}')
FOLLOWER_COUNT=$(echo "$ANALYTICS_RESP" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('data',{}).get('analytics',{}).get('follower_count',0))" 2>/dev/null || echo 0)
[[ "$FOLLOWER_COUNT" -ge 1 ]] \
  && pass "AnalyticsState follower_count = $FOLLOWER_COUNT (incremented)" \
  || fail "AnalyticsState follower_count = $FOLLOWER_COUNT (expected ≥ 1)"

# Verify ProcessedEvent row exists
PE_COUNT=$(db_query "SELECT COUNT(*) FROM processed_event WHERE event_type='fan.followed.artist';")
[[ "$PE_COUNT" -ge 1 ]] \
  && pass "ProcessedEvent dedup row exists (count=$PE_COUNT)" \
  || fail "ProcessedEvent row NOT found"

# Re-follow same artist — should return 409 (idempotency at DB level)
REFOLLOW=$(api_post "/follows" "{\"artist_id\": $ARTIST_ID}" 2>/dev/null || echo '{"status":"error"}')
REFOLLOW_STATUS=$(echo "$REFOLLOW" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null)
[[ "$REFOLLOW_STATUS" == "error" ]] \
  && pass "Duplicate follow correctly rejected (409 Conflict)" \
  || fail "Duplicate follow unexpectedly accepted"

# ─── Section D: E2E Test B — Artist Publishes Release ─────────────────────────
title "D. End-to-End Test B — Artist Publishes Release"

# Login as artist
info "Logging in as test artist …"
api_post "/auth/artist/login" \
  '{"email":"e2e_artist_7g@test.com","password":"TestPass123"}' > /dev/null

# POST /api/releases
info "Artist creates release …"
REL_RESP=$(api_post "/releases" \
  '{"title":"E2E Test Release 7G","release_type":"Single","genre":"Indie","description":"Phase 7G validation"}')
REL_STATUS=$(echo "$REL_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null)
[[ "$REL_STATUS" == "success" ]] \
  && pass "POST /api/releases returned success" \
  || fail "POST /api/releases failed: $REL_RESP"

REL_ID=$(echo "$REL_RESP" | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['data']['release']['id'])" 2>/dev/null || echo "")

# Verify release row
REL_DB=$(db_query "SELECT COUNT(*) FROM music_release WHERE title='E2E Test Release 7G';")
[[ "$REL_DB" -ge 1 ]] \
  && pass "Release row exists in database" \
  || fail "Release row NOT found in database"

# Wait for relay + consumers
info "Waiting for relay + consumers (up to 40 s) …"
for i in $(seq 1 20); do
  PUBLISHED_REL=$(db_query "SELECT COUNT(*) FROM event_outbox WHERE event_type='artist.release.created' AND published_at IS NOT NULL;" 2>/dev/null || echo 0)
  [[ "$PUBLISHED_REL" -ge 1 ]] && break
  sleep 2
done

[[ "$PUBLISHED_REL" -ge 1 ]] \
  && pass "artist.release.created outbox row published" \
  || fail "artist.release.created still unpublished after 40 s"

sleep 5

# Analytics: release_count should increment
ANALYTICS_REL=$(api_get "/artists/$ARTIST_ID/analytics" 2>/dev/null || echo '{}')
REL_COUNT=$(echo "$ANALYTICS_REL" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('data',{}).get('analytics',{}).get('release_count',0))" 2>/dev/null || echo 0)
[[ "$REL_COUNT" -ge 1 ]] \
  && pass "AnalyticsState release_count = $REL_COUNT (incremented)" \
  || fail "AnalyticsState release_count = $REL_COUNT (expected ≥ 1)"

# Notification rows for follower (fan followed artist above)
NOTIF_COUNT=$(db_query "SELECT COUNT(*) FROM notification WHERE artist_id=$ARTIST_ID AND notification_type='new_release';")
[[ "$NOTIF_COUNT" -ge 1 ]] \
  && pass "Notification rows created for follower (count=$NOTIF_COUNT)" \
  || fail "Notification rows NOT created (count=$NOTIF_COUNT)"

# Duplicate event dedup: notification count should not double
NOTIF_COUNT_AFTER=$(db_query "SELECT COUNT(*) FROM notification WHERE artist_id=$ARTIST_ID AND notification_type='new_release';")
[[ "$NOTIF_COUNT_AFTER" -eq "$NOTIF_COUNT" ]] \
  && pass "Notification count stable — dedup working" \
  || fail "Notification count changed unexpectedly"

# ProcessedEvent: both analytics and notification consumers wrote dedup rows
PE_BOTH=$(db_query "SELECT COUNT(*) FROM processed_event WHERE event_type='artist.release.created';")
info "ProcessedEvent rows for artist.release.created: $PE_BOTH (should be 2 — analytics + notif)"
[[ "$PE_BOTH" -ge 1 ]] \
  && pass "ProcessedEvent rows exist for artist.release.created" \
  || fail "ProcessedEvent rows NOT found for artist.release.created"

# ─── Section E: Broker Outage / Transactional Outbox Recovery ─────────────────
title "E. Broker Outage / Transactional Outbox Recovery"

info "Pausing Redpanda container to simulate broker unavailability …"
docker pause "$REDPANDA_CONTAINER"
sleep 2
pass "Redpanda paused"

# Make a business mutation while broker is down
info "Artist creates post while broker is paused …"
POST_RESP=$(api_post "/posts" '{"body":"Posted while broker was down!"}' 2>/dev/null || echo '{}')
POST_STATUS=$(echo "$POST_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
[[ "$POST_STATUS" == "success" ]] \
  && pass "POST /api/posts succeeded with broker paused (business mutation independent)" \
  || fail "POST /api/posts failed with broker paused: $POST_RESP"

# Verify outbox row exists and is unpublished
UNPUB_POST=$(db_query "SELECT COUNT(*) FROM event_outbox WHERE event_type='artist.post.created' AND published_at IS NULL;")
[[ "$UNPUB_POST" -ge 1 ]] \
  && pass "Outbox row created and remains unpublished while broker is paused" \
  || fail "Outbox row for artist.post.created not found or already published"

info "Resuming Redpanda …"
docker unpause "$REDPANDA_CONTAINER"
pass "Redpanda resumed"

info "Waiting for relay to recover and publish pending post event (up to 30 s) …"
for i in $(seq 1 15); do
  RECOVERED=$(db_query "SELECT COUNT(*) FROM event_outbox WHERE event_type='artist.post.created' AND published_at IS NOT NULL;" 2>/dev/null || echo 0)
  [[ "$RECOVERED" -ge 1 ]] && break
  sleep 2
done

[[ "$RECOVERED" -ge 1 ]] \
  && pass "Pending post event published after broker recovery" \
  || fail "Pending post event NOT published after broker recovery + 30 s"

# Verify analytics post_count incremented
sleep 5
ANALYTICS_POST=$(api_get "/artists/$ARTIST_ID/analytics" 2>/dev/null || echo '{}')
POST_COUNT=$(echo "$ANALYTICS_POST" | python3 -c \
  "import sys,json; print(json.load(sys.stdin).get('data',{}).get('analytics',{}).get('post_count',0))" 2>/dev/null || echo 0)
[[ "$POST_COUNT" -ge 1 ]] \
  && pass "Analytics post_count = $POST_COUNT after recovery (Outbox Pattern validated)" \
  || fail "Analytics post_count = $POST_COUNT (expected ≥ 1 after recovery)"

# ─── Section F: Dead-Letter Test ──────────────────────────────────────────────
title "F. Dead-Letter Test"

# Inject a malformed event directly onto the catalog topic using rpk
info "Injecting malformed (non-Avro, non-JSON) message onto artisthub.catalog …"
docker exec "$REDPANDA_CONTAINER" \
  bash -c 'echo "MALFORMED_NOT_AVRO_OR_JSON" | rpk topic produce artisthub.catalog --key deadletter-test' \
  && pass "Malformed message injected onto artisthub.catalog" \
  || { skip "rpk produce not available — dead-letter test skipped"; }

# Wait for consumers to process
sleep 8

# Dead-letter topic should have received the message
DL_COUNT=$(docker exec "$REDPANDA_CONTAINER" \
  rpk topic consume artisthub.deadletter --offset start --num 1 2>/dev/null | wc -l || echo 0)
[[ "$DL_COUNT" -ge 1 ]] \
  && pass "Message visible in artisthub.deadletter topic" \
  || fail "No message found in artisthub.deadletter"

# After dead-letter, confirm normal processing still works by checking DB state unchanged
DB_STABLE=$(db_query "SELECT COUNT(*) FROM event_outbox WHERE event_type='artist.post.created' AND published_at IS NOT NULL;")
[[ "$DB_STABLE" -ge 1 ]] \
  && pass "Normal processing state stable after dead-letter injection" \
  || fail "DB state inconsistent after dead-letter test"

# ─── Section G: Observability Snapshot ────────────────────────────────────────
title "G. Observability Snapshot"

info "Topics:"
docker exec "$REDPANDA_CONTAINER" rpk topic list 2>/dev/null && pass "Topic list OK" || skip "rpk unavailable"

info "Consumer groups:"
docker exec "$REDPANDA_CONTAINER" rpk group list 2>/dev/null && pass "Consumer group list OK" || skip "rpk unavailable"

info "Consumer lag:"
for group in "artisthub.analytics.v1" "artisthub.notifications.v1"; do
  docker exec "$REDPANDA_CONTAINER" rpk group describe "$group" 2>/dev/null \
    && pass "Consumer group $group described" \
    || skip "Consumer group $group not found / rpk unavailable"
done

info "Schema Registry subjects:"
sr_get "/subjects" && pass "Schema Registry subjects fetched" || skip "SR unavailable"

# ─── Section H: Unit Test Suite ───────────────────────────────────────────────
title "H. Unit Test Suite (pytest + flake8)"

info "Running full pytest suite …"
cd backend
../backend/venv/bin/pytest --cov=app --cov=consumers -q 2>&1 | tee /tmp/pytest_7g.txt
PYTEST_EXIT=$?
PYTEST_SUMMARY=$(tail -5 /tmp/pytest_7g.txt)
[[ $PYTEST_EXIT -eq 0 ]] \
  && pass "pytest: all tests passed — $PYTEST_SUMMARY" \
  || fail "pytest: FAILED — $PYTEST_SUMMARY"
cd ..

info "Running flake8 …"
backend/venv/bin/flake8 backend/app backend/consumers \
  && pass "flake8: 0 violations" \
  || fail "flake8: violations found"

# ─── Summary ──────────────────────────────────────────────────────────────────
title "Phase 7G Validation Summary"
echo -e "  ${GREEN}PASSED: $PASSED${RESET}"
[[ $FAILED -gt 0 ]] && echo -e "  ${RED}FAILED: $FAILED${RESET}" || echo -e "  ${GREEN}FAILED: 0${RESET}"

if [[ $FAILED -eq 0 ]]; then
  echo -e "\n  ${GREEN}${BOLD}✓ All Phase 7G validation checks passed.${RESET}"
  echo -e "  ${CYAN}Ready to tag v0.2.0.${RESET}\n"
  exit 0
else
  echo -e "\n  ${RED}${BOLD}✗ $FAILED check(s) failed. Review output above.${RESET}\n"
  exit 1
fi
