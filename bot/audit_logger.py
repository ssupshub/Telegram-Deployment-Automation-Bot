"""
audit_logger.py - Structured Audit Logging
============================================
Every deployment action is written to a structured log.
This is your forensic trail: who did what, when, and with which commit.

Log format: JSON Lines (one JSON object per line) for easy ingestion
into CloudWatch, ELK, Datadog, etc.

Design:
  - Directory creation is lazy (first write), never at import time.
  - Core event fields are written AFTER metadata expansion so metadata
    can never silently overwrite timestamp, user_id, or action (fix #11).
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self, log_path: str = None):
        from config import Config
        self.log_path = log_path or Config.audit_log_path()

    def _ensure_log_dir(self):
        try:
            Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error("Cannot create audit log directory: %s", e)

    def log(self, user: dict, action: str, metadata: dict) -> None:
        """
        Write a structured audit event.

        Fix #11: metadata is spread first, then core fields are written on top,
        so a metadata key like {"action": "fake"} cannot corrupt the audit trail.

        Args:
            user:     {"id": int, "username": str, "full_name": str}
            action:   e.g. "deploy_started", "rollback_completed"
            metadata: arbitrary context, e.g. {"env": "production", "commit": "abc123"}
        """
        # Fix #11: core fields overwrite any conflicting metadata keys.
        event = {
            **metadata,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": user.get("id"),
            "username": user.get("username"),
            "full_name": user.get("full_name"),
            "action": action,
        }

        logger.info("AUDIT: %s", json.dumps(event))

        self._ensure_log_dir()
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(event) + "\n")
        except OSError as e:
            logger.error("Failed to write audit log: %s", e)

    def get_recent(self, limit: int = 20) -> list:
        """
        Return the last N audit events.
        Corrupt lines are skipped individually — one bad line never loses all events.
        """
        try:
            with open(self.log_path) as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []

        events = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping corrupt audit log line: %r", line[:120])

        return events[-limit:]
