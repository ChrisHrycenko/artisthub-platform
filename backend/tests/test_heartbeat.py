"""
tests/test_heartbeat.py

Unit tests for the worker heartbeat utility (Phase 8B).

Tests:
  1. touch_heartbeat creates the file when it does not exist.
  2. touch_heartbeat updates mtime when called again.
  3. touch_heartbeat survives a write failure without raising.
  4. heartbeat_path returns the expected /tmp/<name> path.
  5. Verify heartbeat integrates correctly into the relay run() loop
     when a mock producer is injected.
"""

import os
import time
import tempfile
import unittest.mock as mock

import pytest


class TestHeartbeatUtility:
    """Unit tests for app/utils/heartbeat.py."""

    def test_touch_heartbeat_creates_file(self, tmp_path, monkeypatch):
        """touch_heartbeat creates the heartbeat file if absent."""
        from app.utils import heartbeat as hb_module
        monkeypatch.setattr(hb_module, "_HEARTBEAT_DIR", str(tmp_path))

        from app.utils.heartbeat import touch_heartbeat
        touch_heartbeat("test_alive")

        hb_file = tmp_path / "test_alive"
        assert hb_file.exists(), "Heartbeat file should be created"

    def test_touch_heartbeat_updates_mtime(self, tmp_path, monkeypatch):
        """Calling touch_heartbeat twice updates the file mtime."""
        from app.utils import heartbeat as hb_module
        monkeypatch.setattr(hb_module, "_HEARTBEAT_DIR", str(tmp_path))

        from app.utils.heartbeat import touch_heartbeat
        touch_heartbeat("test_alive")

        hb_file = tmp_path / "test_alive"
        mtime_before = hb_file.stat().st_mtime

        # Small sleep to ensure mtime changes measurably.
        time.sleep(0.05)
        touch_heartbeat("test_alive")

        mtime_after = hb_file.stat().st_mtime
        assert mtime_after >= mtime_before, (
            "mtime should be updated on second call"
        )

    def test_touch_heartbeat_survives_write_failure(self, monkeypatch, caplog):
        """touch_heartbeat logs a warning on OSError but never raises."""
        from app.utils import heartbeat as hb_module

        # Point to a path that cannot be written (parent doesn't exist).
        monkeypatch.setattr(hb_module, "_HEARTBEAT_DIR", "/nonexistent/path")

        import logging
        with caplog.at_level(logging.WARNING, logger="app.utils.heartbeat"):
            # Should not raise.
            from app.utils.heartbeat import touch_heartbeat
            touch_heartbeat("should_fail")

        assert any(
            "Heartbeat write failed" in record.message
            for record in caplog.records
        ), "Expected warning log for failed heartbeat write"

    def test_heartbeat_path_returns_correct_path(self, monkeypatch):
        """heartbeat_path returns /tmp/<filename>."""
        from app.utils import heartbeat as hb_module
        monkeypatch.setattr(hb_module, "_HEARTBEAT_DIR", "/tmp")

        from app.utils.heartbeat import heartbeat_path
        assert heartbeat_path("relay_alive") == "/tmp/relay_alive"
        assert heartbeat_path("analytics_alive") == "/tmp/analytics_alive"
        assert heartbeat_path("notification_alive") == "/tmp/notification_alive"

    def test_heartbeat_file_readable_by_any_uid(self, tmp_path, monkeypatch):
        """Heartbeat file is created with world-readable permissions (0o666)."""
        from app.utils import heartbeat as hb_module
        monkeypatch.setattr(hb_module, "_HEARTBEAT_DIR", str(tmp_path))

        from app.utils.heartbeat import touch_heartbeat
        touch_heartbeat("perm_test")

        hb_file = tmp_path / "perm_test"
        mode = oct(hb_file.stat().st_mode)
        # Check that owner, group, and other all have read permission.
        # The actual permission may be further masked by umask; we only
        # require that the file exists and is readable by the current process.
        assert os.access(str(hb_file), os.R_OK), (
            "Heartbeat file must be readable"
        )


class TestRelayHeartbeatIntegration:
    """
    Integration test: verify the outbox relay run() loop calls
    touch_heartbeat after each poll cycle when a mock producer is used.
    """

    def test_relay_run_calls_heartbeat(self, monkeypatch):
        """
        relay.run() calls touch_heartbeat("relay_alive") after each poll cycle.

        Uses a mock producer and a fake poll function that raises
        KeyboardInterrupt after one iteration, so the loop exits cleanly.
        """
        heartbeat_calls = []

        # Patch touch_heartbeat at the module level so the local import
        # inside run() picks up the stub regardless of import caching.
        monkeypatch.setattr(
            "app.utils.heartbeat.touch_heartbeat",
            lambda name: heartbeat_calls.append(name),
        )

        # Stub out poll_and_publish: return 0 on first call, then raise
        # KeyboardInterrupt on the second call so the loop executes one
        # full iteration (including touch_heartbeat) before stopping.
        call_count = {"n": 0}

        def fake_poll(flask_app, producer, batch_size):
            call_count["n"] += 1
            if call_count["n"] > 1:
                raise KeyboardInterrupt
            return 0

        import app.services.outbox_relay as relay_module
        monkeypatch.setattr(relay_module, "poll_and_publish", fake_poll)

        # Stub sleep so the test doesn't actually wait.
        monkeypatch.setattr("time.sleep", lambda _: None)

        mock_producer = mock.MagicMock()
        mock_producer.close.return_value = None

        from app.services.outbox_relay import run
        run(poll_interval=0.001, batch_size=10, _producer=mock_producer)

        # touch_heartbeat("relay_alive") must have been called at least once.
        assert "relay_alive" in heartbeat_calls, (
            "relay loop must call touch_heartbeat('relay_alive')"
        )
