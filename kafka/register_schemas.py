#!/usr/bin/env python3
"""
register_schemas.py
-------------------
Idempotently registers all ArtistHub Avro event schemas with the
Confluent-compatible Schema Registry (Redpanda embedded SR by default).

Usage:
    python3 kafka/register_schemas.py [--sr-url http://localhost:8081]

Exit codes:
    0  — all schemas registered / already up-to-date
    1  — one or more schemas could not be registered (incompatible change,
         SR unreachable after retries, or malformed schema file)

Subject naming strategy: RecordNameStrategy
    Subject = <namespace>.<RecordName>
    e.g.  io.artisthub.events.FanFollowedArtist

Compatibility mode applied per subject: BACKWARD
"""

import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_SR_URL: str = "http://localhost:8081"
SCHEMAS_DIR: str = os.path.join(os.path.dirname(__file__), "schemas")
COMPATIBILITY_MODE: str = "BACKWARD"
SR_HEALTH_RETRIES: int = 10
SR_HEALTH_INTERVAL: float = 3.0  # seconds between retries


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only — no third-party dependencies)
# ---------------------------------------------------------------------------

def _request(
    url: str,
    method: str = "GET",
    body: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Any]:
    """Perform an HTTP request and return (status_code, parsed_json_body)."""
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/vnd.schemaregistry.v1+json")
    req.add_header("Accept", "application/vnd.schemaregistry.v1+json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(body_text)
        except json.JSONDecodeError:
            return exc.code, {"message": body_text}


# ---------------------------------------------------------------------------
# Schema Registry helpers
# ---------------------------------------------------------------------------

def wait_for_sr(base_url: str) -> None:
    """Block until the Schema Registry responds to a health check."""
    print(f"Waiting for Schema Registry at {base_url} …", flush=True)
    for attempt in range(1, SR_HEALTH_RETRIES + 1):
        try:
            status, _ = _request(f"{base_url}/subjects")
            if status == 200:
                print("  Schema Registry is ready.\n")
                return
        except (urllib.error.URLError, OSError):
            pass
        print(f"  Attempt {attempt}/{SR_HEALTH_RETRIES} — not ready, "
              f"retrying in {SR_HEALTH_INTERVAL}s …")
        time.sleep(SR_HEALTH_INTERVAL)
    print("ERROR: Schema Registry did not become ready in time.", file=sys.stderr)
    sys.exit(1)


def set_compatibility(base_url: str, subject: str, mode: str) -> None:
    """Set the compatibility mode for a subject."""
    url = f"{base_url}/config/{subject}"
    payload = json.dumps({"compatibility": mode}).encode()
    status, body = _request(url, method="PUT", body=payload)
    if status not in (200, 201):
        raise RuntimeError(
            f"Failed to set compatibility for {subject}: {status} {body}"
        )


def get_existing_schema(base_url: str, subject: str) -> Optional[str]:
    """Return the schema string of the latest registered version, or None."""
    status, body = _request(f"{base_url}/subjects/{subject}/versions/latest")
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError(
            f"Unexpected SR response for {subject}: {status} {body}"
        )
    return body.get("schema")


def register_schema(base_url: str, subject: str, schema_str: str) -> int:
    """Register schema under subject; return the assigned version id."""
    url = f"{base_url}/subjects/{subject}/versions"
    payload = json.dumps({"schema": schema_str}).encode()
    status, body = _request(url, method="POST", body=payload)
    if status in (200, 201):
        return body["id"]
    raise RuntimeError(
        f"Schema registration failed for {subject}: HTTP {status} — {body}"
    )


def schemas_equal(a: str, b: str) -> bool:
    """Compare two Avro schema strings by normalised JSON value."""
    return json.loads(a) == json.loads(b)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_schemas() -> Dict[str, str]:
    """
    Walk SCHEMAS_DIR (excluding common/) and return a mapping of
    subject_name -> raw_schema_string for each .avsc file.
    """
    pattern = os.path.join(SCHEMAS_DIR, "**", "*.avsc")
    result: Dict[str, str] = {}
    for path in sorted(glob.glob(pattern, recursive=True)):
        # Skip reference-only envelope doc
        if os.sep + "common" + os.sep in path:
            continue
        with open(path) as fh:
            raw = fh.read()
        schema = json.loads(raw)
        namespace = schema["namespace"]  # e.g. io.artisthub.events
        name = schema["name"]            # e.g. FanFollowedArtist
        subject = f"{namespace}.{name}"
        result[subject] = raw
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point — parse args, wait for SR, register all schemas."""
    parser = argparse.ArgumentParser(
        description="Register ArtistHub Avro schemas with Schema Registry."
    )
    parser.add_argument(
        "--sr-url",
        default=os.environ.get("SCHEMA_REGISTRY_URL", DEFAULT_SR_URL),
        help="Schema Registry base URL (default: http://localhost:8081)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover schemas and print subjects without registering.",
    )
    args = parser.parse_args()

    base_url = args.sr_url.rstrip("/")

    schemas = discover_schemas()
    if not schemas:
        print("ERROR: No .avsc files found under kafka/schemas/", file=sys.stderr)
        sys.exit(1)

    print(f"Discovered {len(schemas)} schema(s):\n")
    for subject in schemas:
        print(f"  {subject}")
    print()

    if args.dry_run:
        print("Dry-run mode — exiting without registering.")
        return

    wait_for_sr(base_url)

    failures = 0
    for subject, schema_str in schemas.items():
        try:
            # Ensure BACKWARD compat is set before registering
            set_compatibility(base_url, subject, COMPATIBILITY_MODE)

            existing = get_existing_schema(base_url, subject)
            if existing and schemas_equal(existing, schema_str):
                print(f"  [SKIP]     {subject}  (already registered, unchanged)")
                continue

            version_id = register_schema(base_url, subject, schema_str)
            action = "UPDATED" if existing else "REGISTERED"
            print(f"  [{action}]  {subject}  → id={version_id}")

        except RuntimeError as exc:
            print(f"  [FAIL]     {subject}  — {exc}", file=sys.stderr)
            failures += 1

    print()
    if failures:
        print(f"FAILED: {failures} schema(s) could not be registered.", file=sys.stderr)
        sys.exit(1)
    print(f"Done. All {len(schemas)} schema(s) registered successfully.")


if __name__ == "__main__":
    main()
