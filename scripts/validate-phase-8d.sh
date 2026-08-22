#!/usr/bin/env bash
# =============================================================================
# scripts/validate-phase-8d.sh
#
# Phase 8D — ArtistHub Confluent Cloud connectivity validation.
#
# Validates as much as possible without a live OpenShift cluster.
# Live Confluent Cloud checks are gated on credentials being present.
#
# Usage:
#   # Set credentials in .env then source them:
#   set -a; source .env; set +a
#   bash scripts/validate-phase-8d.sh
#
# Exit codes:
#   0  all applicable checks passed (BLOCKED checks are not failures)
#   1  one or more checks FAILED
#
# SECURITY:
#   - Credential values are NEVER echoed or logged by this script.
#   - The script checks for credential presence only, never their values.
#   - No credentials are stored, committed, or passed as arguments.
#
# Sections:
#   A. Local regression gates (no Confluent required)
#   B. Docker regression gates
#   C. Local Avro schema validation (no SR required)
#   D. Confluent credential presence check
#   E. Schema Registry validation (requires credentials)
#   F. Kafka connectivity + topic verification (requires credentials)
#   G. Producer + consumer smoke tests (requires credentials)
#   H. OpenShift manifest check (kustomize build)
#   I. Security: confirm no credentials committed to git
# =============================================================================

set -euo pipefail

# ─── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

pass()    { echo -e "  ${GREEN}[PASS]${RESET} $*";    PASSED=$((PASSED+1)); }
fail()    { echo -e "  ${RED}[FAIL]${RESET} $*";    FAILED=$((FAILED+1)); }
blocked() { echo -e "  ${YELLOW}[BLOCKED]${RESET} $*"; BLOCKED_N=$((BLOCKED_N+1)); }
info()    { echo -e "  ${BLUE}[INFO]${RESET} $*"; }
title()   { echo -e "\n${BOLD}${CYAN}═══ $* ═══${RESET}\n"; }

PASSED=0; FAILED=0; BLOCKED_N=0

# ─── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$REPO_ROOT/backend"
VENV_PYTHON="$REPO_ROOT/backend/venv/bin/python3"
# Fall back to system python if venv not present
if [[ ! -f "$VENV_PYTHON" ]]; then
  VENV_PYTHON=$(which python3)
fi
VENV_PYTEST="$REPO_ROOT/backend/venv/bin/pytest"
if [[ ! -f "$VENV_PYTEST" ]]; then
  VENV_PYTEST="$VENV_PYTHON -m pytest"
fi
VENV_FLAKE8="$REPO_ROOT/backend/venv/bin/flake8"
if [[ ! -f "$VENV_FLAKE8" ]]; then
  VENV_FLAKE8="$VENV_PYTHON -m flake8"
fi

# ─── Confluent credential presence (no values logged) ────────────────────────
CONFLUENT_READY=false
SR_READY=false

_check_confluent_creds() {
  local bootstrap="${KAFKA_BOOTSTRAP_SERVERS:-}"
  local protocol="${KAFKA_SECURITY_PROTOCOL:-PLAINTEXT}"
  local api_key="${CONFLUENT_API_KEY:-}"
  local api_secret="${CONFLUENT_API_SECRET:-}"

  if [[ "$protocol" == "SASL_SSL" ]] \
     && [[ "$bootstrap" == *"confluent.cloud"* ]] \
     && [[ -n "$api_key" ]] \
     && [[ -n "$api_secret" ]]; then
    CONFLUENT_READY=true
  fi
}

_check_sr_creds() {
  local sr_url="${SCHEMA_REGISTRY_URL:-}"
  local sr_key="${SCHEMA_REGISTRY_API_KEY:-}"
  local sr_secret="${SCHEMA_REGISTRY_API_SECRET:-}"

  if [[ "$sr_url" == *"confluent.cloud"* ]] \
     && [[ -n "$sr_key" ]] \
     && [[ -n "$sr_secret" ]]; then
    SR_READY=true
  fi
}

_check_confluent_creds
_check_sr_creds

# =============================================================================
title "A. Local Regression Gates"
# =============================================================================

info "Running pytest (SQLite suite) …"
if cd "$BACKEND" && $VENV_PYTEST tests/ --tb=short -q 2>&1 \
     | tee /tmp/pytest_8d.txt | tail -3; then
  PYTEST_RESULT=$(tail -1 /tmp/pytest_8d.txt)
  pass "pytest: $PYTEST_RESULT"
else
  fail "pytest: FAILED — see /tmp/pytest_8d.txt"
fi
cd "$REPO_ROOT"

info "Running flake8 …"
if cd "$BACKEND" && $VENV_FLAKE8 app consumers 2>&1; then
  pass "flake8: 0 violations"
else
  fail "flake8: violations found"
fi
cd "$REPO_ROOT"

# PostgreSQL integration — skip if env var absent (expected in CI without PG)
if [[ -n "${ARTISTHUB_TEST_POSTGRES_URL:-}" ]]; then
  info "Running PostgreSQL integration tests …"
  if cd "$BACKEND" && $VENV_PYTEST tests/test_postgres_integration.py -v \
       --tb=short 2>&1 | tee /tmp/pg_8d.txt | tail -3; then
    pass "PostgreSQL integration: all passed"
  else
    fail "PostgreSQL integration: FAILED — see /tmp/pg_8d.txt"
  fi
  cd "$REPO_ROOT"
else
  blocked "PostgreSQL integration tests — set ARTISTHUB_TEST_POSTGRES_URL to run"
fi

# =============================================================================
title "B. Docker Regression Gates"
# =============================================================================

info "Building backend image (artisthub-backend:8d) …"
if docker build -f docker/Dockerfile -t artisthub-backend:8d . > /tmp/docker_backend_8d.log 2>&1; then
  pass "docker build Dockerfile → artisthub-backend:8d"
else
  fail "docker build Dockerfile FAILED — see /tmp/docker_backend_8d.log"
fi

info "Building frontend image (artisthub-frontend:8d) …"
if docker build -f docker/Dockerfile.frontend -t artisthub-frontend:8d . \
     > /tmp/docker_frontend_8d.log 2>&1; then
  pass "docker build Dockerfile.frontend → artisthub-frontend:8d"
else
  fail "docker build Dockerfile.frontend FAILED — see /tmp/docker_frontend_8d.log"
fi

info "Checking /api/health (requires running stack) …"
if curl -fsS http://localhost:8080/api/health > /tmp/health_8d.json 2>&1; then
  HEALTH_STATUS=$(python3 -c "
import sys,json
try:
    d=json.load(open('/tmp/health_8d.json'))
    print(d.get('data',{}).get('status',''))
except Exception:
    print('')
")
  if [[ "$HEALTH_STATUS" == "ok" ]]; then
    pass "/api/health → {\"status\":\"ok\"}"
  else
    fail "/api/health returned unexpected status: $HEALTH_STATUS"
  fi
else
  blocked "/api/health — start the stack first: docker compose -f docker/docker-compose.yml up -d"
fi

# =============================================================================
title "C. Local Avro Schema Validation"
# =============================================================================

info "Validating all 12 Avro schema files parse correctly …"
AVRO_CHECK=$(
  $VENV_PYTHON - <<'PYEOF'
import sys, json
from pathlib import Path

repo = Path(__file__).resolve().parent.parent if '__file__' in dir() else Path('.')

try:
    import fastavro.schema
except ImportError:
    print("ERROR: fastavro not installed")
    sys.exit(1)

SCHEMAS_DIR = Path("kafka/schemas")
REQUIRED = [
    "social/fan_followed_artist.avsc",
    "social/fan_unfollowed_artist.avsc",
    "social/artist_post_created.avsc",
    "social/artist_post_deleted.avsc",
    "catalog/artist_release_created.avsc",
    "catalog/artist_release_updated.avsc",
    "catalog/artist_release_deleted.avsc",
    "catalog/artist_merch_created.avsc",
    "catalog/artist_merch_updated.avsc",
    "catalog/artist_merch_deleted.avsc",
    "identity/artist_registered.avsc",
    "identity/artist_profile_updated.avsc",
]
failed = []
for rel in REQUIRED:
    p = SCHEMAS_DIR / rel
    if not p.exists():
        failed.append(f"MISSING: {p}")
        continue
    try:
        schema = fastavro.schema.parse_schema(json.loads(p.read_text()))
        print(f"  OK: {p.name} → {schema.get('name')}")
    except Exception as exc:
        failed.append(f"PARSE ERROR {p}: {exc}")

if failed:
    for msg in failed:
        print(f"  FAIL: {msg}")
    sys.exit(1)
PYEOF
  )
AVRO_EXIT=$?
if [[ $AVRO_EXIT -eq 0 ]]; then
  pass "All 12 Avro schema files parsed successfully"
  echo "$AVRO_CHECK" | while IFS= read -r line; do info "$line"; done
else
  fail "Avro schema validation failed"
  echo "$AVRO_CHECK"
fi

# =============================================================================
title "D. Confluent Credential Presence Check"
# =============================================================================

info "Checking KAFKA_BOOTSTRAP_SERVERS …"
BOOTSTRAP="${KAFKA_BOOTSTRAP_SERVERS:-}"
if [[ "$BOOTSTRAP" == *"confluent.cloud"* ]]; then
  pass "KAFKA_BOOTSTRAP_SERVERS points to Confluent Cloud"
  info "Endpoint: $BOOTSTRAP"
elif [[ -n "$BOOTSTRAP" ]]; then
  blocked "KAFKA_BOOTSTRAP_SERVERS is set but not to Confluent Cloud: $BOOTSTRAP"
else
  blocked "KAFKA_BOOTSTRAP_SERVERS not set — see scripts/confluent-provision.md"
fi

info "Checking KAFKA_SECURITY_PROTOCOL …"
PROTOCOL="${KAFKA_SECURITY_PROTOCOL:-}"
if [[ "$PROTOCOL" == "SASL_SSL" ]]; then
  pass "KAFKA_SECURITY_PROTOCOL=SASL_SSL ✓"
else
  blocked "KAFKA_SECURITY_PROTOCOL not set to SASL_SSL (current: '${PROTOCOL:-unset}')"
fi

info "Checking CONFLUENT_API_KEY / CONFLUENT_API_SECRET (presence only) …"
if [[ -n "${CONFLUENT_API_KEY:-}" ]] && [[ -n "${CONFLUENT_API_SECRET:-}" ]]; then
  pass "CONFLUENT_API_KEY and CONFLUENT_API_SECRET are set (not printing values)"
else
  blocked "CONFLUENT_API_KEY / CONFLUENT_API_SECRET not set"
fi

info "Checking SCHEMA_REGISTRY_URL …"
SR_URL="${SCHEMA_REGISTRY_URL:-}"
if [[ "$SR_URL" == *"confluent.cloud"* ]]; then
  pass "SCHEMA_REGISTRY_URL points to Confluent Cloud: $SR_URL"
else
  blocked "SCHEMA_REGISTRY_URL not set to Confluent Cloud (current: '${SR_URL:-unset}')"
fi

info "Checking SCHEMA_REGISTRY_API_KEY / SCHEMA_REGISTRY_API_SECRET (presence only) …"
if [[ -n "${SCHEMA_REGISTRY_API_KEY:-}" ]] && [[ -n "${SCHEMA_REGISTRY_API_SECRET:-}" ]]; then
  pass "SCHEMA_REGISTRY_API_KEY and SCHEMA_REGISTRY_API_SECRET are set"
else
  blocked "SCHEMA_REGISTRY_API_KEY / SCHEMA_REGISTRY_API_SECRET not set"
fi

# =============================================================================
title "E. Schema Registry Validation"
# =============================================================================

if [[ "$SR_READY" == "true" ]]; then
  info "Running kafka/confluent-sr-test.py …"
  if $VENV_PYTHON kafka/confluent-sr-test.py; then
    pass "Schema Registry: all checks passed"
  else
    SR_EXIT=$?
    if [[ $SR_EXIT -eq 2 ]]; then
      blocked "Schema Registry test blocked — credentials not configured"
    else
      fail "Schema Registry test FAILED (exit code $SR_EXIT)"
    fi
  fi
else
  blocked "Schema Registry test — set SCHEMA_REGISTRY_URL/API_KEY/API_SECRET to run"
  info "See scripts/confluent-provision.md Step 5 for provisioning instructions"
fi

# =============================================================================
title "F+G. Kafka Connectivity + Smoke Tests"
# =============================================================================

if [[ "$CONFLUENT_READY" == "true" ]] && [[ "$SR_READY" == "true" ]]; then
  info "Running kafka/confluent-smoke-test.py …"
  if $VENV_PYTHON kafka/confluent-smoke-test.py; then
    pass "Kafka smoke tests: all passed"
  else
    SMOKE_EXIT=$?
    if [[ $SMOKE_EXIT -eq 2 ]]; then
      blocked "Kafka smoke test blocked — credentials not configured"
    else
      fail "Kafka smoke test FAILED (exit code $SMOKE_EXIT)"
    fi
  fi
else
  blocked "Kafka smoke tests — requires all Confluent credentials to be set"
  info "See scripts/confluent-provision.md for provisioning instructions"
fi

# =============================================================================
title "H. OpenShift Manifest Validation (Kustomize)"
# =============================================================================

info "kubectl kustomize openshift/overlays/local …"
if kubectl kustomize openshift/overlays/local > /tmp/kust_local_8d.yaml 2>&1; then
  RESOURCE_COUNT=$(grep -c "^kind:" /tmp/kust_local_8d.yaml || echo 0)
  pass "kustomize local overlay: $RESOURCE_COUNT resources, no errors"
else
  fail "kustomize local overlay FAILED"
fi

info "kubectl kustomize openshift/overlays/production …"
if kubectl kustomize openshift/overlays/production > /tmp/kust_prod_8d.yaml 2>&1; then
  RESOURCE_COUNT=$(grep -c "^kind:" /tmp/kust_prod_8d.yaml || echo 0)
  pass "kustomize production overlay: $RESOURCE_COUNT resources, no errors"
else
  fail "kustomize production overlay FAILED"
fi

# =============================================================================
title "I. Security: No Credentials Committed"
# =============================================================================

info "Checking git-tracked files for credential patterns …"

# Patterns that should never appear in committed files
CRED_PATTERNS=(
  "CONFLUENT_API_KEY=[^<\$]"
  "CONFLUENT_API_SECRET=[^<\$]"
  "SCHEMA_REGISTRY_API_KEY=[^<\$]"
  "SCHEMA_REGISTRY_API_SECRET=[^<\$]"
  "pkc-[A-Za-z0-9]"
  "psrc-[A-Za-z0-9]"
  "lkc-[A-Za-z0-9]"
)

CRED_FOUND=false
for pattern in "${CRED_PATTERNS[@]}"; do
  # Search only git-tracked files (not .gitignored).
  # Exclude documentation, example, and template files that intentionally
  # contain placeholder strings like pkc-XXXXX or psrc-XXXXX (not real creds).
  MATCHES=$(git -C "$REPO_ROOT" grep -l "$pattern" \
    -- ':!*.md' ':!*.example' ':!.env.example' \
    ':!docker/.env.kafka.example' \
    ':!openshift/base/secrets.example.yaml' \
    ':!scripts/validate-phase-8d.sh' \
    ':!kafka/confluent-*.py' 2>/dev/null || true)
  if [[ -n "$MATCHES" ]]; then
    fail "Potential credential found (pattern: $pattern) in: $MATCHES"
    CRED_FOUND=true
  fi
done

if [[ "$CRED_FOUND" == "false" ]]; then
  pass "No credentials found in git-tracked files ✓"
fi

info "Checking .gitignore protects openshift/secrets/ …"
if grep -q "openshift/secrets/\*\.yaml" "$REPO_ROOT/.gitignore" \
   || grep -q "openshift/secrets/\*.yaml" "$REPO_ROOT/.gitignore"; then
  pass ".gitignore excludes openshift/secrets/*.yaml ✓"
else
  fail ".gitignore does not exclude openshift/secrets/ — check .gitignore"
fi

info "Checking .env is gitignored …"
if git -C "$REPO_ROOT" check-ignore -q .env 2>/dev/null; then
  pass ".env is excluded by .gitignore ✓"
else
  fail ".env is NOT excluded — add '.env' to .gitignore"
fi

# =============================================================================
title "Phase 8D Summary"
# =============================================================================

echo ""
echo -e "  ${GREEN}PASSED:  $PASSED${RESET}"
echo -e "  ${YELLOW}BLOCKED: $BLOCKED_N${RESET}  (not counted as failures — require external action)"
if [[ $FAILED -gt 0 ]]; then
  echo -e "  ${RED}FAILED:  $FAILED${RESET}"
else
  echo -e "  ${GREEN}FAILED:  0${RESET}"
fi

echo ""
if [[ $BLOCKED_N -gt 0 ]]; then
  echo -e "  ${YELLOW}${BOLD}⚠ $BLOCKED_N check(s) are blocked pending Confluent Cloud provisioning.${RESET}"
  echo -e "  ${YELLOW}  Follow scripts/confluent-provision.md to unblock.${RESET}"
fi

if [[ $FAILED -eq 0 ]]; then
  echo -e "  ${GREEN}${BOLD}✓ All applicable Phase 8D checks passed.${RESET}"
  echo ""
  exit 0
else
  echo -e "  ${RED}${BOLD}✗ $FAILED check(s) failed. Review output above.${RESET}"
  echo ""
  exit 1
fi
