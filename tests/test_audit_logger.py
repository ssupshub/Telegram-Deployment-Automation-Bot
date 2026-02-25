"""
test_audit_logger.py - Tests for AuditLogger
===============================================
Covers: event structure, file persistence, get_recent(), IOError handling.
"""

import json
import os
import tempfile
import pytest
from audit_logger import AuditLogger


@pytest.fixture
def tmp_log(tmp_path):
    """Return a fresh AuditLogger writing to a temp file."""
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
        assert event["full_name"] == "Alice Admin"

    def test_log_includes_metadata_fields(self, tmp_log):
        logger, path = tmp_log
        logger.log(USER, "deploy_success", {"env": "production", "commit": "def456"})
        with open(path) as f:
            event = json.loads(f.readline())
        assert event["env"] == "production"
        assert event["commit"] == "def456"

    def test_log_includes_timestamp(self, tmp_log):
        logger, path = tmp_log
        logger.log(USER, "deploy_started", {})
        with open(path) as f:
            event = json.loads(f.readline())
        assert "timestamp" in event
        assert "T" in event["timestamp"]  # ISO 8601 format

    def test_multiple_logs_append_as_separate_lines(self, tmp_log):
        logger, path = tmp_log
        logger.log(USER, "deploy_started", {"env": "staging"})
        logger.log(USER, "deploy_success", {"env": "staging"})
        with open(path) as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 2
        events = [json.loads(l) for l in lines]
        assert events[0]["action"] == "deploy_started"
        assert events[1]["action"] == "deploy_success"

    def test_log_does_not_raise_on_ioerror(self, tmp_path):
        """If the log file can't be written, the bot should not crash."""
        bad_path = "/root/no_permission/audit.log"
        logger = AuditLogger(log_path=bad_path)
        # Should log a warning but NOT raise
        logger.log(USER, "deploy_started", {})  # no exception


class TestGetRecent:
    def test_get_recent_returns_empty_list_when_no_file(self, tmp_path):
        logger = AuditLogger(log_path=str(tmp_path / "nonexistent.log"))
        result = logger.get_recent()
        assert result == []

    def test_get_recent_returns_all_events_when_fewer_than_limit(self, tmp_log):
        logger, _ = tmp_log
        logger.log(USER, "action_1", {})
        logger.log(USER, "action_2", {})
        result = logger.get_recent(limit=20)
        assert len(result) == 2

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
        logger, path = tmp_log
        logger.log(USER, "good_event", {})
        with open(path, "a") as f:
            f.write("this is not json\n")
        # Should not raise — corrupt lines are skipped
        result = logger.get_recent()
        assert isinstance(result, list)
