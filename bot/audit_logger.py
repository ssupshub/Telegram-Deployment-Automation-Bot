"""
audit_logger.py - Structured Audit Logging
============================================
Every deployment action is written to a structured log.
This is your forensic trail: who did what, when, and with which commit.

Log format: JSON Lines (one JSON object per line) for easy ingestion
into CloudWatch, ELK, Datadog, etc.

Tamper-detection: In production, ship these logs to an immutable store
(S3 with Object Lock, CloudWatch Logs, etc.) immediately.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self, log_path: str = None):
        from config import Config
        self.log_path = log_path or Config.AUDIT_LOG_PATH

        # Ensure log directory exists
        log_dir = Path(self.log_path).parent
        log_dir.mkdir(parents=True, exist_ok=True)

    def log(self, user: dict, action: str, metadata: dict):
        """
        Write a structured audit event.

        Args:
            user:     {"id": int, "username": str, "full_name": str}
            action:   e.g. "deploy_started", "rollback_completed"
            metadata: arbitrary context, e.g. {"env": "production", "commit": "abc123"}
        """
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": user.get("id"),
            "username": user.get("username"),
            "full_name": user.get("full_name"),
            "action": action,
            **metadata,
        }

        # Always log to application logger (captured by Docker/systemd)
        logger.info("AUDIT: %s", json.dumps(event))

        # Also append to file for local persistence
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(event) + "\n")
        except IOError as e:
            logger.error("Failed to write audit log: %s", e)

    def get_recent(self, limit: int = 20) -> list:
        """Return the last N audit events (for /history command)."""
        try:
            with open(self.log_path) as f:
                lines = f.readlines()
            events = [json.loads(l) for l in lines if l.strip()]
            return events[-limit:]
        except (FileNotFoundError, json.JSONDecodeError):
            return []
