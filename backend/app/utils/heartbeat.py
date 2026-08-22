"""
app/utils/heartbeat.py

Worker process heartbeat utility for ArtistHub.

Phase 8B — OpenShift worker liveness probes
============================================
OpenShift's container liveness probes cannot use pgrep (no process tools
inside a minimal Python image) and cannot use httpGet (workers expose no
HTTP server).  The supported approach is an ``exec`` probe that checks
whether a sentinel file has been updated recently.

Each worker calls ``touch_heartbeat(path)`` once per loop iteration.
The file's mtime is updated on every call.  An OpenShift exec probe
checks the elapsed time since the last update:

    # POSIX-compatible probe command (works in BusyBox and GNU coreutils):
    test $(($(date +%s) - $(stat -c %Y /tmp/relay_alive))) -lt 120

Design rationale
----------------
- The heartbeat file is written on every loop iteration, not only when
  work is found.  This means an idle consumer (no Kafka messages) still
  updates the file, preventing false liveness failures.
- The liveness threshold (default: 120 s) is intentionally generous:
    * Kafka consumers call poll(timeout=1.0) every ~1 second.
    * The relay sleeps OUTBOX_POLL_INTERVAL (default 5 s) between batches.
    * A 120 s threshold tolerates 60–120 missed iterations before the probe
      fires — appropriate for transient Kafka broker unavailability or slow
      DB operations without triggering a spurious pod restart.
- /tmp is used because it is writable by ALL UIDs (including arbitrary
  OpenShift UIDs) without any additional filesystem setup.
- Each worker uses a distinct filename so probe commands are unambiguous
  when multiple workers share a pod (not the case today, but defensive).

Heartbeat file locations
------------------------
  /tmp/relay_alive         — outbox relay
  /tmp/analytics_alive     — analytics consumer
  /tmp/notification_alive  — notification consumer

OpenShift exec probe template (Phase 8C+)
------------------------------------------
Add to each worker Deployment container spec:

    livenessProbe:
      exec:
        command:
          - sh
          - -c
          - test $(($(date +%s) - $(stat -c %Y /tmp/<name>_alive))) -lt 120
      initialDelaySeconds: 30
      periodSeconds: 30
      failureThreshold: 3

    startupProbe:
      exec:
        command: ["test", "-f", "/tmp/<name>_alive"]
      failureThreshold: 6
      periodSeconds: 10

Note on stat(1) portability
----------------------------
``stat -c %Y`` is GNU/Linux syntax (available in Debian/Alpine-based
Python images).  BusyBox ``stat`` uses ``-c %Y`` in newer versions and
``-f %m`` on macOS/BSD.  The Python image (python:3.11-slim, Debian-based)
includes GNU coreutils, so ``stat -c %Y`` is reliable inside the container.
"""

import os
import logging

logger = logging.getLogger(__name__)

# Default heartbeat directory.  /tmp is always writable by all UIDs.
_HEARTBEAT_DIR = "/tmp"


def touch_heartbeat(filename: str) -> None:
    """
    Update the modification time of a worker heartbeat sentinel file.

    Creates the file if it does not yet exist.  Uses os.open with O_CREAT
    so the operation is atomic and race-condition-safe.

    Args:
        filename: Base filename (e.g. ``"relay_alive"``).  Written to
                  ``/tmp/<filename>``.
    """
    path = os.path.join(_HEARTBEAT_DIR, filename)
    try:
        # Open or create, then close immediately — we only need the mtime.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o666)
        os.close(fd)
        # Update mtime to now.
        os.utime(path, None)
    except OSError as exc:
        # Log but never crash the worker loop — a failed heartbeat write
        # is less severe than losing Kafka message processing.
        logger.warning("Heartbeat write failed | path=%s err=%s", path, exc)


def heartbeat_path(filename: str) -> str:
    """Return the full path for a heartbeat file (for testing/docs)."""
    return os.path.join(_HEARTBEAT_DIR, filename)
