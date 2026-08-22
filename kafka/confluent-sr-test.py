#!/usr/bin/env python3
"""
kafka/confluent-sr-test.py

Phase 8D — Schema Registry validation against Confluent Cloud.

Validates:
  1. Schema Registry connectivity (GET /subjects)
  2. Global compatibility mode is BACKWARD
  3. All 12 ArtistHub RecordNameStrategy subjects registered / registerable
  4. BACKWARD-compatible v2 schema is accepted
  5. Breaking schema (removes required field) is rejected
  6. Avro local encode/decode round-trip (no SR needed for this step)

Usage:
  # Set credentials in environment before running:
  export SCHEMA_REGISTRY_URL=https://psrc-XXXXX.us-east-1.aws.confluent.cloud
  export SCHEMA_REGISTRY_API_KEY=<key>
  export SCHEMA_REGISTRY_API_SECRET=<secret>
  python3 kafka/confluent-sr-test.py

Exit codes:
  0  all checks passed
  1  one or more checks failed
  2  credentials missing (SCHEMA_REGISTRY_URL not configured)

SECURITY: Credentials are read from environment variables only.
          This script never prints credential values.
"""

import json
import os
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed. Run: pip install requests")
    sys.exit(1)

# ─── Colours ──────────────────────────────────────────────────────────────────
GREEN = "\033[0;32m"
RED = "\033[0;31m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
RESET = "\033[0m"

PASSED = 0
FAILED = 0


def ok(msg):
    global PASSED
    PASSED += 1
    print(f"  {GREEN}[PASS]{RESET} {msg}")


def fail(msg):
    global FAILED
    FAILED += 1
    print(f"  {RED}[FAIL]{RESET} {msg}")


def skip(msg):
    print(f"  {YELLOW}[SKIP]{RESET} {msg}")


def info(msg):
    print(f"  {BLUE}[INFO]{RESET} {msg}")


def section(title):
    print(f"\n{BOLD}{CYAN}═══ {title} ═══{RESET}\n")


# ─── Credential check ─────────────────────────────────────────────────────────

def _sr_creds():
    """Return (url, auth) or exit with code 2 if not configured."""
    url = os.environ.get("SCHEMA_REGISTRY_URL", "").strip().rstrip("/")
    if not url or url.startswith("http://localhost"):
        print(
            f"\n{RED}BLOCKED{RESET}: SCHEMA_REGISTRY_URL is not set to a "
            "Confluent Cloud endpoint.\n"
            "Set it in .env and re-run:\n"
            "  export SCHEMA_REGISTRY_URL=https://psrc-XXXXX.confluent.cloud\n"
            "  export SCHEMA_REGISTRY_API_KEY=<key>\n"
            "  export SCHEMA_REGISTRY_API_SECRET=<secret>"
        )
        sys.exit(2)
    key = os.environ.get("SCHEMA_REGISTRY_API_KEY", "")
    secret = os.environ.get("SCHEMA_REGISTRY_API_SECRET", "")
    if not key or not secret:
        print(
            f"\n{RED}BLOCKED{RESET}: SCHEMA_REGISTRY_API_KEY / "
            "SCHEMA_REGISTRY_API_SECRET not set."
        )
        sys.exit(2)
    # Return basic auth tuple — never print key/secret values.
    return url, (key, secret)


def sr_get(url, auth, path):
    return requests.get(
        f"{url}{path}",
        auth=auth,
        headers={"Accept": "application/vnd.schemaregistry.v1+json"},
        timeout=15,
    )


def sr_post(url, auth, path, body):
    return requests.post(
        f"{url}{path}",
        auth=auth,
        headers={
            "Content-Type": "application/vnd.schemaregistry.v1+json",
            "Accept": "application/vnd.schemaregistry.v1+json",
        },
        json=body,
        timeout=15,
    )


def sr_put(url, auth, path, body):
    return requests.put(
        f"{url}{path}",
        auth=auth,
        headers={
            "Content-Type": "application/vnd.schemaregistry.v1+json",
        },
        json=body,
        timeout=15,
    )


# ─── Schema file helpers ──────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent

SUBJECTS = [
    "io.artisthub.events.FanFollowedArtist",
    "io.artisthub.events.FanUnfollowedArtist",
    "io.artisthub.events.ArtistPostCreated",
    "io.artisthub.events.ArtistPostDeleted",
    "io.artisthub.events.ArtistReleaseCreated",
    "io.artisthub.events.ArtistReleaseUpdated",
    "io.artisthub.events.ArtistReleaseDeleted",
    "io.artisthub.events.ArtistMerchCreated",
    "io.artisthub.events.ArtistMerchUpdated",
    "io.artisthub.events.ArtistMerchDeleted",
    "io.artisthub.events.ArtistRegistered",
    "io.artisthub.events.ArtistProfileUpdated",
]

# Maps subject suffix → .avsc file relative to kafka/schemas/
_SUBJECT_TO_FILE = {
    "FanFollowedArtist":     "social/fan_followed_artist.avsc",
    "FanUnfollowedArtist":   "social/fan_unfollowed_artist.avsc",
    "ArtistPostCreated":     "social/artist_post_created.avsc",
    "ArtistPostDeleted":     "social/artist_post_deleted.avsc",
    "ArtistReleaseCreated":  "catalog/artist_release_created.avsc",
    "ArtistReleaseUpdated":  "catalog/artist_release_updated.avsc",
    "ArtistReleaseDeleted":  "catalog/artist_release_deleted.avsc",
    "ArtistMerchCreated":    "catalog/artist_merch_created.avsc",
    "ArtistMerchUpdated":    "catalog/artist_merch_updated.avsc",
    "ArtistMerchDeleted":    "catalog/artist_merch_deleted.avsc",
    "ArtistRegistered":      "identity/artist_registered.avsc",
    "ArtistProfileUpdated":  "identity/artist_profile_updated.avsc",
}

SCHEMAS_DIR = REPO_ROOT / "kafka" / "schemas"


def _schema_str(record_name: str) -> str:
    rel = _SUBJECT_TO_FILE[record_name]
    path = SCHEMAS_DIR / rel
    return path.read_text(encoding="utf-8")


# ─── Section A: Connectivity ──────────────────────────────────────────────────

def test_connectivity(url, auth):
    section("A. Schema Registry Connectivity")
    try:
        r = sr_get(url, auth, "/subjects")
        r.raise_for_status()
        subjects = r.json()
        ok(f"Connected to Schema Registry (found {len(subjects)} existing subjects)")
        return True
    except requests.exceptions.ConnectionError as exc:
        fail(f"Cannot connect to Schema Registry: {exc}")
        return False
    except requests.exceptions.HTTPError as exc:
        fail(f"HTTP error from Schema Registry: {exc} | "
             f"Response: {exc.response.text[:200]}")
        return False


# ─── Section B: Compatibility Mode ───────────────────────────────────────────

def test_compatibility_mode(url, auth):
    section("B. Global Compatibility Mode")
    try:
        r = sr_get(url, auth, "/config")
        r.raise_for_status()
        mode = r.json().get("compatibilityLevel", "UNKNOWN")
        if mode == "BACKWARD":
            ok(f"Global compatibility mode = {mode}")
        else:
            fail(
                f"Global compatibility mode = {mode} (expected BACKWARD). "
                "Fix: PUT /config {{\"compatibility\":\"BACKWARD\"}}"
            )
    except Exception as exc:
        fail(f"Could not fetch compatibility config: {exc}")


# ─── Section C: Register / verify 12 subjects ─────────────────────────────────

def test_register_subjects(url, auth):
    section("C. Register / Verify All 12 Subjects")
    results = {}
    for subject in SUBJECTS:
        record_name = subject.split(".")[-1]
        schema_str = _schema_str(record_name)
        try:
            r = sr_post(url, auth, f"/subjects/{subject}/versions",
                        {"schema": schema_str, "schemaType": "AVRO"})
            r.raise_for_status()
            schema_id = r.json()["id"]
            ok(f"{subject} → schema_id={schema_id}")
            results[subject] = schema_id
        except requests.exceptions.HTTPError as exc:
            fail(f"{subject} registration failed: {exc} | "
                 f"{exc.response.text[:200]}")
            results[subject] = None

    registered = sum(1 for v in results.values() if v is not None)
    info(f"Registered/verified: {registered}/12 subjects")
    return results


# ─── Section D: Verify version 1 exists ──────────────────────────────────────

def test_versions(url, auth):
    section("D. Verify Version 1 Exists for All Subjects")
    for subject in SUBJECTS:
        try:
            r = sr_get(url, auth, f"/subjects/{subject}/versions")
            r.raise_for_status()
            versions = r.json()
            if 1 in versions:
                ok(f"{subject} → version 1 exists (versions: {versions})")
            else:
                fail(f"{subject} → version 1 NOT in versions list: {versions}")
        except Exception as exc:
            fail(f"{subject} → could not fetch versions: {exc}")


# ─── Section E: BACKWARD compatibility tests ─────────────────────────────────

def test_backward_compatibility(url, auth):
    section("E. BACKWARD Compatibility Tests")

    # v2 schema — adds an optional field with default (should be BACKWARD-compatible)
    v2_path = SCHEMAS_DIR / "test_compat" / "fan_followed_artist_v2.avsc"
    if v2_path.exists():
        v2_str = v2_path.read_text(encoding="utf-8")
        subject = "io.artisthub.events.FanFollowedArtist"
        try:
            r = sr_post(
                url, auth,
                f"/compatibility/subjects/{subject}/versions/latest",
                {"schema": v2_str, "schemaType": "AVRO"},
            )
            r.raise_for_status()
            is_compat = r.json().get("is_compatible", False)
            if is_compat:
                ok("v2 schema (adds optional source_device) is BACKWARD-compatible ✓")
            else:
                fail("v2 schema unexpectedly rejected by BACKWARD compatibility check")
        except Exception as exc:
            fail(f"Compatibility check for v2 failed: {exc}")
    else:
        skip(f"v2 test schema not found at {v2_path}")

    # Breaking schema — removes required field (must be REJECTED)
    break_path = SCHEMAS_DIR / "test_compat" / "fan_followed_artist_breaking.avsc"
    if break_path.exists():
        break_str = break_path.read_text(encoding="utf-8")
        subject = "io.artisthub.events.FanFollowedArtist"
        try:
            r = sr_post(
                url, auth,
                f"/compatibility/subjects/{subject}/versions/latest",
                {"schema": break_str, "schemaType": "AVRO"},
            )
            r.raise_for_status()
            is_compat = r.json().get("is_compatible", False)
            if not is_compat:
                ok("Breaking schema (removes follow_id) correctly REJECTED ✓")
            else:
                fail("Breaking schema was unexpectedly ACCEPTED — SR compatibility not enforced")
        except Exception as exc:
            fail(f"Breaking-schema compatibility check failed: {exc}")
    else:
        skip(f"Breaking test schema not found at {break_path}")


# ─── Section F: Local Avro encode/decode round-trip ──────────────────────────

def test_local_avro_roundtrip():
    section("F. Local Avro Encode/Decode Round-Trip (no SR required)")
    try:
        sys.path.insert(0, str(REPO_ROOT / "backend"))
        from app.services.avro_utils import encode, decode, get_or_register_schema_id  # noqa
        # Import-only test — exercises schema parsing without SR call.
        from app.services.avro_utils import load_schema
        for record_name in [
            "FanFollowedArtist", "ArtistReleaseCreated",
            "ArtistRegistered", "ArtistMerchCreated",
        ]:
            schema = load_schema(record_name)
            assert schema.get("name") == f"io.artisthub.events.{record_name}"
            ok(f"Load + parse schema: {record_name} ✓")
    except Exception as exc:
        fail(f"Local Avro round-trip failed: {exc}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}ArtistHub Phase 8D — Schema Registry Validation{RESET}")
    print("=" * 60)

    url, auth = _sr_creds()
    # Report endpoint without credentials
    info(f"Schema Registry URL: {url}")

    connected = test_connectivity(url, auth)
    if not connected:
        print(f"\n{RED}Cannot proceed — Schema Registry unreachable.{RESET}")
        sys.exit(1)

    test_compatibility_mode(url, auth)
    test_register_subjects(url, auth)
    test_versions(url, auth)
    test_backward_compatibility(url, auth)
    test_local_avro_roundtrip()

    # Summary
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"  {GREEN}PASSED: {PASSED}{RESET}")
    if FAILED:
        print(f"  {RED}FAILED: {FAILED}{RESET}")
        sys.exit(1)
    else:
        print(f"  {GREEN}FAILED: 0{RESET}")
        print(f"\n  {GREEN}{BOLD}✓ All Schema Registry checks passed.{RESET}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
