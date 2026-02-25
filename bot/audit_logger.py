"""
audit_logger.py - Structured Audit Logging
============================================
Every deployment action is written to a structured log.
This is your forensic trail: who did what, when, and with which commit.

Log format: JSON Lines (one JSON object per line) for easy ingestion
into CloudWatch, ELK, Datadog, etc.

Tamper-detection: In production, ship these logs to an immutable store
(S3 with Object Lock, CloudWatch Logs, etc.) immediately.

Design note: The log directory is created lazily on first write, NOT in
__init__. This means importing the module (and instantiating AuditLogger
at module level in bot.py) never touches the filesystem — which is
essential for running tests in sandboxed CI environments like GitHub Actions
where /var/log/deploybot doesn't exist and can't be created.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self, log_path: str = None):
        from config import Config
        self.log_path = log_path or Config.AUDIT_LOG_PATH
        # ⚠️  Do NOT create directories here.
        # __init__ is called at module import time (bot.py line 39).
        # Any filesystem call here will fail in CI where /var/log/deploybot
        # is a restricted path. Directory creation is deferred to _ensure_log_dir()
        # which is only called when an actual log write is attempted.

    def _ensure_log_dir(self):
        """
        Create the log directory if it doesn't exist.
        Called lazily before the first write — never at import time.
        Failures are caught and logged so a missing log dir never crashes the bot.
        """
        try:
            Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error("Cannot create audit log directory: %s", e)

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

        # Always emit to the Python logger (captured by Docker/systemd/CloudWatch)
        logger.info("AUDIT: %s", json.dumps(event))

        # Lazily ensure the directory exists, then write
        self._ensure_log_dir()
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(event) + "\n")
        except OSError as e:
            logger.error("Failed to write audit log: %s", e)

    def get_recent(self, limit: int = 20) -> list:
        """Return the last N audit events (for /history command)."""
        try:
            with open(self.log_path) as f:
                lines = f.readlines()
            events = [json.loads(line) for line in lines if line.strip()]
            return events[-limit:]
        except (FileNotFoundError, json.JSONDecodeError):
            return []
