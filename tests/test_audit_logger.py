"""test_audit_logger.py - Tests for AuditLogger"""
import json
import os
import pytest
from audit_logger import AuditLogger

@pytest.fixture
def tmp_log(tmp_path):
    log_file = str(tmp_path / "audit.log")
    return AuditLogger(log_path=log_file), log_file

USER = {"id": 111, "username": "alice", "full_name": "Alice Admin"}


class TestAuditLoggerWrite:
    def test_log_creates_file_if_not_exists(self, tmp_log):
        logger, path = tmp_log
        assert not os.path.exists(path)
        logger.log(USER, "deploy_started", {"env": "staging"})
        assert os.path.exists(path)

    def test_log_writes_valid_json(self, tmp_log):
        logger, path = tmp_log
        logger.log(USER, "deploy_started", {"env": "staging", "commit": "abc123"})
        with open(path) as f:
            event = json.loads(f.readline())
        assert event["action"] == "deploy_started"

    def test_log_includes_user_fields(self, tmp_log):
        logger, path = tmp_log
        logger.log(USER, "deploy_started", {})
        with open(path) as f:
            event = json.loads(f.readline())
        assert event["user_id"] == 111
        assert event["username"] == "alice"

    def test_core_fields_overwrite_metadata(self, tmp_log):
        """Fix #11: metadata must not be able to corrupt core audit fields."""
        logger, path = tmp_log
        # Pass metadata that tries to overwrite the action and user_id fields
        malicious_metadata = {"action": "fake_action", "user_id": 0}
        logger.log(USER, "deploy_started", malicious_metadata)
        with open(path) as f:
            event = json.loads(f.readline())
        # Core fields must win
        assert event["action"] == "deploy_started", "metadata must not overwrite action"
        assert event["user_id"] == 111, "metadata must not overwrite user_id"

    def test_metadata_fields_are_present(self, tmp_log):
        """Non-conflicting metadata fields should still appear in the event."""
        logger, path = tmp_log
        logger.log(USER, "deploy_success", {"env": "production", "commit": "def456"})
        with open(path) as f:
            event = json.loads(f.readline())
        assert event["env"] == "production"
        assert event["commit"] == "def456"

    def test_multiple_logs_append_as_separate_lines(self, tmp_log):
        logger, path = tmp_log
        logger.log(USER, "deploy_started", {"env": "staging"})
        logger.log(USER, "deploy_success", {"env": "staging"})
        with open(path) as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 2

    def test_log_does_not_raise_on_ioerror(self, tmp_path):
        from unittest.mock import patch
        log_path = str(tmp_path / "audit.log")
        audit_logger = AuditLogger(log_path=log_path)
        with patch("builtins.open", side_effect=OSError("Permission denied")):
            audit_logger.log(USER, "deploy_started", {})  # must not raise


class TestGetRecent:
    def test_get_recent_returns_empty_list_when_no_file(self, tmp_path):
        logger = AuditLogger(log_path=str(tmp_path / "nonexistent.log"))
        assert logger.get_recent() == []

    def test_get_recent_respects_limit(self, tmp_log):
        logger, _ = tmp_log
        for i in range(10):
            logger.log(USER, f"action_{i}", {})
        result = logger.get_recent(limit=3)
        assert len(result) == 3

    def test_get_recent_returns_last_n_events(self, tmp_log):
        logger, _ = tmp_log
        for i in range(5):
            logger.log(USER, f"action_{i}", {"seq": i})
        result = logger.get_recent(limit=2)
        assert result[0]["seq"] == 3
        assert result[1]["seq"] == 4

    def test_get_recent_handles_corrupt_line(self, tmp_log):
        """A single corrupt line must not cause all events to be lost."""
        logger, path = tmp_log
        logger.log(USER, "good_event", {})
        with open(path, "a") as f:
            f.write("this is not json\n")
        result = logger.get_recent()
        assert len(result) == 1
        assert result[0]["action"] == "good_event"
