#!/usr/bin/env python3
"""
validate_schemas.py
-------------------
Validates that all ArtistHub Avro event schemas are correctly registered
in the Schema Registry and that representative sample events conform to
those schemas using structural (JSON) validation.

What this script checks
~~~~~~~~~~~~~~~~~~~~~~~
1. Every expected subject exists in the Schema Registry.
2. Each subject's compatibility mode is BACKWARD.
3. The schema stored in the SR matches the local .avsc file exactly
   (by normalised JSON comparison).
4. A hand-crafted sample event for each schema passes structural
   validation against the schema's field definitions.

No third-party libraries are required — validation uses stdlib json
and structural checks only (no Avro binary encoding/decoding).

Usage:
    python3 kafka/validate_schemas.py [--sr-url http://localhost:8081]

Exit codes:
    0  — all checks passed
    1  — one or more checks failed
"""

import argparse
import glob
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_SR_URL: str = "http://localhost:8081"
SCHEMAS_DIR: str = os.path.join(os.path.dirname(__file__), "schemas")

# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------


def _request(url: str, method: str = "GET") -> Tuple[int, Any]:
    """Perform a GET/PUT request and return (status_code, parsed_json_body)."""
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/vnd.schemaregistry.v1+json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(body_text)
        except json.JSONDecodeError:
            return exc.code, {"message": body_text}
    except (urllib.error.URLError, OSError) as exc:
        return 0, {"message": str(exc)}


# ---------------------------------------------------------------------------
# Schema Registry queries
# ---------------------------------------------------------------------------


def sr_subjects(base_url: str) -> List[str]:
    """Return all subjects currently registered in the SR."""
    status, body = _request(f"{base_url}/subjects")
    if status != 200:
        raise RuntimeError(f"Could not list subjects: HTTP {status} — {body}")
    return body  # type: ignore[return-value]


def sr_latest_schema(base_url: str, subject: str) -> Optional[str]:
    """Return the raw schema string for the latest version of subject."""
    status, body = _request(
        f"{base_url}/subjects/{subject}/versions/latest"
    )
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError(
            f"Could not fetch schema for {subject}: HTTP {status} — {body}"
        )
    return body.get("schema")


def sr_compatibility(base_url: str, subject: str) -> Optional[str]:
    """Return the compatibility mode for subject, or None if default."""
    status, body = _request(f"{base_url}/config/{subject}")
    if status == 404:
        # No subject-level override; check global default
        g_status, g_body = _request(f"{base_url}/config")
        if g_status == 200:
            return g_body.get("compatibilityLevel") or g_body.get("compatibility")
        return None
    if status == 200:
        return body.get("compatibilityLevel") or body.get("compatibility")
    return None


# ---------------------------------------------------------------------------
# Structural event validator
# ---------------------------------------------------------------------------

# Type mapping: Avro primitive -> Python types that are acceptable
_AVRO_PRIMITIVES: Dict[str, Tuple[type, ...]] = {
    "string": (str,),
    "int": (int,),
    "long": (int,),
    "float": (float, int),
    "double": (float, int),
    "boolean": (bool,),
    "null": (type(None),),
    "bytes": (str, bytes),
}


def _effective_types(avro_type: Any) -> Tuple[type, ...]:
    """
    Return Python types that are valid for an Avro type spec.
    Handles primitives, unions (list), and nested records (dict).
    """
    if isinstance(avro_type, str):
        return _AVRO_PRIMITIVES.get(avro_type, (object,))
    if isinstance(avro_type, list):
        # union — valid if any branch matches
        combined: Tuple[type, ...] = ()
        for branch in avro_type:
            combined += _effective_types(branch)
        return combined
    if isinstance(avro_type, dict):
        if avro_type.get("type") == "record":
            return (dict,)
        if avro_type.get("type") == "array":
            return (list,)
        if avro_type.get("type") == "map":
            return (dict,)
    return (object,)


def validate_event_structure(
    schema: Dict[str, Any], event: Dict[str, Any]
) -> List[str]:
    """
    Structurally validate *event* against the Avro *schema*.
    Returns a list of error strings (empty = valid).
    Only validates top-level fields and one level of nested payload record.
    """
    issues: List[str] = []
    fields = {f["name"]: f for f in schema.get("fields", [])}

    for fname, fdef in fields.items():
        # Check required fields are present (unless they have a default)
        if fname not in event:
            if "default" not in fdef:
                issues.append(f"Missing required field: '{fname}'")
            continue

        value = event[fname]
        avro_type = fdef["type"]
        expected = _effective_types(avro_type)
        if not isinstance(value, expected):
            issues.append(
                f"Field '{fname}': expected {expected}, got {type(value).__name__}"
            )

        # Recurse one level into nested payload record
        if (
            fname == "payload"
            and isinstance(avro_type, dict)
            and avro_type.get("type") == "record"
            and isinstance(value, dict)
        ):
            sub_fields = {
                f["name"]: f for f in avro_type.get("fields", [])
            }
            for sfname, sfdef in sub_fields.items():
                if sfname not in value:
                    if "default" not in sfdef:
                        issues.append(
                            f"Missing required payload field: 'payload.{sfname}'"
                        )
                    continue
                sv = value[sfname]
                exp = _effective_types(sfdef["type"])
                if not isinstance(sv, exp):
                    issues.append(
                        f"Payload field '{sfname}': expected {exp}, "
                        f"got {type(sv).__name__}"
                    )

    return issues


# ---------------------------------------------------------------------------
# Sample events (one per schema — minimal but structurally complete)
# ---------------------------------------------------------------------------

_TS = "2024-01-15T12:00:00Z"

SAMPLE_EVENTS: Dict[str, Dict[str, Any]] = {
    "io.artisthub.events.FanFollowedArtist": {
        "event_id": "evt-001",
        "event_type": "FanFollowedArtist",
        "event_version": "1.0",
        "occurred_at": _TS,
        "producer": "artisthub-backend",
        "correlation_id": "req-abc",
        "payload": {
            "follow_id": 1,
            "fan_id": 10,
            "artist_id": 20,
            "followed_at": _TS,
        },
    },
    "io.artisthub.events.FanUnfollowedArtist": {
        "event_id": "evt-002",
        "event_type": "FanUnfollowedArtist",
        "event_version": "1.0",
        "occurred_at": _TS,
        "producer": "artisthub-backend",
        "correlation_id": None,
        "payload": {
            "fan_id": 10,
            "artist_id": 20,
            "unfollowed_at": _TS,
        },
    },
    "io.artisthub.events.ArtistPostCreated": {
        "event_id": "evt-003",
        "event_type": "ArtistPostCreated",
        "event_version": "1.0",
        "occurred_at": _TS,
        "producer": "artisthub-backend",
        "correlation_id": None,
        "payload": {
            "post_id": 5,
            "artist_id": 20,
            "body": "New track dropping Friday!",
            "image_url": None,
            "posted_at": _TS,
        },
    },
    "io.artisthub.events.ArtistPostDeleted": {
        "event_id": "evt-004",
        "event_type": "ArtistPostDeleted",
        "event_version": "1.0",
        "occurred_at": _TS,
        "producer": "artisthub-backend",
        "correlation_id": None,
        "payload": {
            "post_id": 5,
            "artist_id": 20,
            "deleted_at": _TS,
        },
    },
    "io.artisthub.events.ArtistReleaseCreated": {
        "event_id": "evt-005",
        "event_type": "ArtistReleaseCreated",
        "event_version": "1.0",
        "occurred_at": _TS,
        "producer": "artisthub-backend",
        "correlation_id": None,
        "payload": {
            "release_id": 7,
            "artist_id": 20,
            "title": "Echoes",
            "release_type": "album",
            "genre": "Electronic",
            "description": None,
            "artwork_url": None,
            "streaming_url": None,
            "release_date": None,
            "created_at": _TS,
        },
    },
    "io.artisthub.events.ArtistReleaseUpdated": {
        "event_id": "evt-006",
        "event_type": "ArtistReleaseUpdated",
        "event_version": "1.0",
        "occurred_at": _TS,
        "producer": "artisthub-backend",
        "correlation_id": None,
        "payload": {
            "release_id": 7,
            "artist_id": 20,
            "title": "Echoes (Deluxe)",
            "release_type": "album",
            "genre": "Electronic",
            "description": "Deluxe edition",
            "artwork_url": None,
            "streaming_url": None,
            "release_date": None,
            "updated_at": _TS,
        },
    },
    "io.artisthub.events.ArtistReleaseDeleted": {
        "event_id": "evt-007",
        "event_type": "ArtistReleaseDeleted",
        "event_version": "1.0",
        "occurred_at": _TS,
        "producer": "artisthub-backend",
        "correlation_id": None,
        "payload": {
            "release_id": 7,
            "artist_id": 20,
            "deleted_at": _TS,
        },
    },
    "io.artisthub.events.ArtistMerchCreated": {
        "event_id": "evt-008",
        "event_type": "ArtistMerchCreated",
        "event_version": "1.0",
        "occurred_at": _TS,
        "producer": "artisthub-backend",
        "correlation_id": None,
        "payload": {
            "product_id": 3,
            "artist_id": 20,
            "product_name": "Tour Hoodie",
            "description": None,
            "price": 49.99,
            "image_url": None,
            "inventory_quantity": 100,
            "created_at": _TS,
        },
    },
    "io.artisthub.events.ArtistMerchUpdated": {
        "event_id": "evt-009",
        "event_type": "ArtistMerchUpdated",
        "event_version": "1.0",
        "occurred_at": _TS,
        "producer": "artisthub-backend",
        "correlation_id": None,
        "payload": {
            "product_id": 3,
            "artist_id": 20,
            "product_name": "Tour Hoodie",
            "description": "Limited run",
            "price": 44.99,
            "image_url": None,
            "inventory_quantity": 80,
            "updated_at": _TS,
        },
    },
    "io.artisthub.events.ArtistMerchDeleted": {
        "event_id": "evt-010",
        "event_type": "ArtistMerchDeleted",
        "event_version": "1.0",
        "occurred_at": _TS,
        "producer": "artisthub-backend",
        "correlation_id": None,
        "payload": {
            "product_id": 3,
            "artist_id": 20,
            "deleted_at": _TS,
        },
    },
    "io.artisthub.events.ArtistRegistered": {
        "event_id": "evt-011",
        "event_type": "ArtistRegistered",
        "event_version": "1.0",
        "occurred_at": _TS,
        "producer": "artisthub-backend",
        "correlation_id": None,
        "payload": {
            "artist_id": 20,
            "email": "artist@example.com",
            "display_name": "DJ Artsy",
            "genre": None,
            "location": None,
            "registered_at": _TS,
        },
    },
    "io.artisthub.events.ArtistProfileUpdated": {
        "event_id": "evt-012",
        "event_type": "ArtistProfileUpdated",
        "event_version": "1.0",
        "occurred_at": _TS,
        "producer": "artisthub-backend",
        "correlation_id": None,
        "payload": {
            "artist_id": 20,
            "display_name": "DJ Artsy",
            "bio": "Electronic producer",
            "profile_image_url": None,
            "genre": "Electronic",
            "location": "Berlin",
            "updated_at": _TS,
        },
    },
}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_schemas() -> Dict[str, Dict[str, Any]]:
    """Return {subject: parsed_schema} for all event .avsc files."""
    pattern = os.path.join(SCHEMAS_DIR, "**", "*.avsc")
    result: Dict[str, Dict[str, Any]] = {}
    for path in sorted(glob.glob(pattern, recursive=True)):
        if os.sep + "common" + os.sep in path:
            continue
        with open(path) as fh:
            schema = json.load(fh)
        subject = f"{schema['namespace']}.{schema['name']}"
        result[subject] = schema
    return result


# ---------------------------------------------------------------------------
# Main validation loop
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point — run all validation checks and report results."""
    parser = argparse.ArgumentParser(
        description="Validate ArtistHub Avro schemas in Schema Registry."
    )
    parser.add_argument(
        "--sr-url",
        default=os.environ.get("SCHEMA_REGISTRY_URL", DEFAULT_SR_URL),
        help="Schema Registry base URL (default: http://localhost:8081)",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Skip SR checks; only validate local .avsc structure + sample events.",
    )
    args = parser.parse_args()

    base_url = args.sr_url.rstrip("/")
    schemas = discover_schemas()
    failures = 0
    total_checks = 0

    print(f"Validating {len(schemas)} schema(s) …\n")

    # ------------------------------------------------------------------
    # (A) Structural validation of local .avsc files + sample events
    # ------------------------------------------------------------------
    print("── A) Local schema structure + sample event validation ──")
    for subject, schema in schemas.items():
        total_checks += 1
        sample = SAMPLE_EVENTS.get(subject)
        if sample is None:
            print(f"  [WARN] No sample event defined for {subject}")
            continue
        issues = validate_event_structure(schema, sample)
        if issues:
            print(f"  [FAIL] {subject}")
            for issue in issues:
                print(f"         • {issue}")
            failures += 1
        else:
            print(f"  [OK]   {subject}")
    print()

    if args.local_only:
        print("Local-only mode — skipping Schema Registry checks.")
    else:
        # ------------------------------------------------------------------
        # (B) Schema Registry: subjects exist
        # ------------------------------------------------------------------
        print("── B) Schema Registry: subjects exist ──")
        try:
            registered = set(sr_subjects(base_url))
        except RuntimeError as exc:
            print(f"  [ERROR] Cannot reach Schema Registry: {exc}", file=sys.stderr)
            print("  Hint: run 'python3 kafka/register_schemas.py' first, "
                  "or use --local-only to skip SR checks.")
            sys.exit(1)

        for subject in schemas:
            total_checks += 1
            if subject in registered:
                print(f"  [OK]   {subject}")
            else:
                print(f"  [FAIL] {subject} — NOT found in Schema Registry")
                failures += 1
        print()

        # ------------------------------------------------------------------
        # (C) Schema Registry: compatibility is BACKWARD
        # ------------------------------------------------------------------
        print("── C) Schema Registry: compatibility mode = BACKWARD ──")
        for subject in schemas:
            total_checks += 1
            compat = sr_compatibility(base_url, subject)
            if compat and "BACKWARD" in compat.upper():
                print(f"  [OK]   {subject}  ({compat})")
            else:
                print(f"  [FAIL] {subject}  (got: {compat!r}, expected BACKWARD)")
                failures += 1
        print()

        # ------------------------------------------------------------------
        # (D) Schema Registry: stored schema matches local file
        # ------------------------------------------------------------------
        print("── D) Schema Registry: stored schema matches local .avsc ──")
        for subject, schema in schemas.items():
            total_checks += 1
            stored_raw = sr_latest_schema(base_url, subject)
            if stored_raw is None:
                print(f"  [FAIL] {subject} — no schema in SR")
                failures += 1
                continue
            try:
                stored = json.loads(stored_raw)
            except json.JSONDecodeError:
                print(f"  [FAIL] {subject} — SR returned non-JSON schema")
                failures += 1
                continue
            if stored == schema:
                print(f"  [OK]   {subject}")
            else:
                print(f"  [FAIL] {subject} — local and SR schemas differ")
                failures += 1
        print()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"Checks run: {total_checks}  |  Failures: {failures}")
    if failures:
        print(f"\nVALIDATION FAILED — {failures} check(s) did not pass.",
              file=sys.stderr)
        sys.exit(1)
    print("\nAll checks passed ✓")


if __name__ == "__main__":
    main()
