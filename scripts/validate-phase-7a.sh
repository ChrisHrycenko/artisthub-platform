#!/usr/bin/env bash
# scripts/validate-phase-7a.sh
#
# Phase 7A validation script.
# Run from the repo root after starting the full stack:
#
#   docker-compose \
#     -f docker/docker-compose.yml \
#     -f docker/docker-compose.kafka.yml \
#     up --build -d
#
#   bash scripts/validate-phase-7a.sh
#
# Exits 0 if all checks pass, 1 if any check fails.

set -euo pipefail

PASS=0
FAIL=0

check() {
  local desc="$1"
  local result="$2"   # "ok" or anything else = fail
  if [ "$result" = "ok" ]; then
    echo "  ✓  $desc"
    PASS=$((PASS + 1))
  else
    echo "  ✗  $desc  — $result"
    FAIL=$((FAIL + 1))
  fi
}

echo ""
echo "=== Phase 7A — ArtistHub + Redpanda Stack Validation ==="
echo ""

# ------------------------------------------------------------------ #
# 1. Wait for services to be ready                                    #
# ------------------------------------------------------------------ #
echo "--- Waiting for services to stabilise (15s) ---"
sleep 15

# ------------------------------------------------------------------ #
# 2. ArtistHub health via nginx (port 8080)                          #
# ------------------------------------------------------------------ #
echo ""
echo "--- ArtistHub health ---"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/health 2>/dev/null || echo "000")
check "GET /api/health returns 200 via nginx:8080" "$([ "$HTTP" = "200" ] && echo ok || echo "HTTP $HTTP")"

BODY=$(curl -s http://localhost:8080/api/health 2>/dev/null || echo "{}")
DB_OK=$(echo "$BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print('ok' if d.get('data',{}).get('database')=='ok' else 'fail')" 2>/dev/null || echo "fail")
check "Health response: database=ok" "$DB_OK"

# ------------------------------------------------------------------ #
# 3. Redpanda broker health                                           #
# ------------------------------------------------------------------ #
echo ""
echo "--- Redpanda broker health ---"
BROKER_STATUS=$(docker inspect --format='{{.State.Health.Status}}' artisthub-redpanda 2>/dev/null || echo "not_found")
check "Redpanda container health status = healthy" "$([ "$BROKER_STATUS" = "healthy" ] && echo ok || echo "$BROKER_STATUS")"

# ------------------------------------------------------------------ #
# 4. Redpanda Console                                                 #
# ------------------------------------------------------------------ #
echo ""
echo "--- Redpanda Console ---"
CONSOLE_HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8082 2>/dev/null || echo "000")
check "Redpanda Console responds on :8082" "$([ "$CONSOLE_HTTP" = "200" ] && echo ok || echo "HTTP $CONSOLE_HTTP")"

# ------------------------------------------------------------------ #
# 5. Topics exist with correct partition counts                       #
# ------------------------------------------------------------------ #
echo ""
echo "--- Kafka topics ---"
TOPICS=$(docker exec artisthub-redpanda rpk topic list --brokers localhost:9092 2>/dev/null || echo "error")

for TOPIC in "artisthub.social" "artisthub.catalog" "artisthub.identity" "artisthub.deadletter"; do
  check "Topic '$TOPIC' exists" "$(echo "$TOPICS" | grep -q "$TOPIC" && echo ok || echo "not found")"
done

# Partition counts via describe
DESCRIBE=$(docker exec artisthub-redpanda rpk topic describe artisthub.social --brokers localhost:9092 2>/dev/null || echo "")
check "artisthub.social has 6 partitions" "$(echo "$DESCRIBE" | grep -q "Partition.*5" && echo ok || echo "check partition count manually")"

DESCRIBE_ID=$(docker exec artisthub-redpanda rpk topic describe artisthub.identity --brokers localhost:9092 2>/dev/null || echo "")
check "artisthub.identity has 3 partitions" "$(echo "$DESCRIBE_ID" | grep -q "Partition.*2" && echo ok || echo "check partition count manually")"

# ------------------------------------------------------------------ #
# 6. Schema Registry endpoint                                         #
# ------------------------------------------------------------------ #
echo ""
echo "--- Schema Registry ---"
SR_HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/subjects 2>/dev/null || echo "000")
check "Schema Registry /subjects endpoint responds on :8081" "$([ "$SR_HTTP" = "200" ] && echo ok || echo "HTTP $SR_HTTP")"

# ------------------------------------------------------------------ #
# 7. Kafka smoke test — produce then consume                          #
# ------------------------------------------------------------------ #
echo ""
echo "--- Kafka smoke test (produce + consume) ---"
TEST_MSG="phase-7a-smoke-test-$(date +%s)"

# Produce one message to artisthub.social
PRODUCE_RESULT=$(echo "$TEST_MSG" | docker exec -i artisthub-redpanda \
  rpk topic produce artisthub.social --brokers localhost:9092 --key smoke-test 2>&1 || echo "ERROR")
check "Produce test message to artisthub.social" "$(echo "$PRODUCE_RESULT" | grep -qi "error\|fail" && echo "PRODUCE ERROR: $PRODUCE_RESULT" || echo ok)"

# Consume one message (timeout 5s)
CONSUME_RESULT=$(docker exec artisthub-redpanda \
  rpk topic consume artisthub.social --brokers localhost:9092 \
  --num 1 --offset start 2>/dev/null | head -5 || echo "")
check "Consume test message from artisthub.social" "$(echo "$CONSUME_RESULT" | grep -q "$TEST_MSG" && echo ok || echo "message not found in consume output — may be offset issue, check console")"

# ------------------------------------------------------------------ #
# 8. ArtistHub frontend loads                                         #
# ------------------------------------------------------------------ #
echo ""
echo "--- Frontend ---"
FRONTEND_HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/ 2>/dev/null || echo "000")
check "ArtistHub frontend (index.html) serves via nginx:8080" "$([ "$FRONTEND_HTTP" = "200" ] && echo ok || echo "HTTP $FRONTEND_HTTP")"

# ------------------------------------------------------------------ #
# Summary                                                             #
# ------------------------------------------------------------------ #
echo ""
echo "==================================================="
echo "  Phase 7A Results: $PASS passed / $FAIL failed"
echo "==================================================="
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo "  Some checks failed. Review output above."
  exit 1
else
  echo "  All Phase 7A checks passed."
  exit 0
fi
