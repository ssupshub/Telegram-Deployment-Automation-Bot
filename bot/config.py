"""
config.py - Centralized configuration from environment variables.
Never hardcode secrets. All sensitive values come from the environment.
"""

import os
from typing import Set


class Config:
    # ── Bot Secrets ──────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")

    # ── RBAC: Comma-separated Telegram user IDs ──────────────────────────────
    # Admin: can deploy to production, rollback, full access
    # Staging users: can deploy to staging and check status only
    _ADMIN_IDS_RAW: str = os.environ.get("ADMIN_TELEGRAM_IDS", "")
    _STAGING_IDS_RAW: str = os.environ.get("STAGING_TELEGRAM_IDS", "")

    @classmethod
    def _parse_ids(cls, raw: str) -> Set[int]:
        """Parse comma-separated integer IDs, ignoring blanks/invalid."""
        ids = set()
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        return ids

    @classmethod
    def admin_ids(cls) -> Set[int]:
        return cls._parse_ids(cls._ADMIN_IDS_RAW)

    @classmethod
    def staging_ids(cls) -> Set[int]:
        """Staging users PLUS all admins (admins are supersets)."""
        return cls._parse_ids(cls._STAGING_IDS_RAW) | cls.admin_ids()

    @classmethod
    def is_admin(cls, user_id: int) -> bool:
        return user_id in cls.admin_ids()

    @classmethod
    def is_authorized(cls, user_id: int) -> bool:
        """Any user in either list is authorized to use the bot."""
        return user_id in cls.staging_ids()

    # ── GitHub / Registry ────────────────────────────────────────────────────
    GITHUB_REPO: str = os.environ.get("GITHUB_REPO", "myorg/myapp")
    GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "")
    GITHUB_BRANCH_STAGING: str = os.environ.get("GITHUB_BRANCH_STAGING", "develop")
    GITHUB_BRANCH_PRODUCTION: str = os.environ.get("GITHUB_BRANCH_PRODUCTION", "main")

    REGISTRY_URL: str = os.environ.get("REGISTRY_URL", "")          # e.g. 123456789.dkr.ecr.us-east-1.amazonaws.com
    REGISTRY_IMAGE: str = os.environ.get("REGISTRY_IMAGE", "myapp")

    # ── Server / SSH ─────────────────────────────────────────────────────────
    STAGING_HOST: str = os.environ.get("STAGING_HOST", "")
    PRODUCTION_HOST: str = os.environ.get("PRODUCTION_HOST", "")
    DEPLOY_USER: str = os.environ.get("DEPLOY_USER", "deploy")
    SSH_KEY_PATH: str = os.environ.get("SSH_KEY_PATH", "/app/secrets/deploy_key")

    # ── Health Checks ────────────────────────────────────────────────────────
    STAGING_HEALTH_URL: str = os.environ.get("STAGING_HEALTH_URL", "http://staging.example.com/health")
    PRODUCTION_HEALTH_URL: str = os.environ.get("PRODUCTION_HEALTH_URL", "http://production.example.com/health")
    HEALTH_CHECK_TIMEOUT: int = int(os.environ.get("HEALTH_CHECK_TIMEOUT", "30"))
    HEALTH_CHECK_RETRIES: int = int(os.environ.get("HEALTH_CHECK_RETRIES", "5"))

    # ── Kubernetes (optional) ─────────────────────────────────────────────────
    USE_KUBERNETES: bool = os.environ.get("USE_KUBERNETES", "false").lower() == "true"
    KUBE_NAMESPACE: str = os.environ.get("KUBE_NAMESPACE", "default")
    KUBE_DEPLOYMENT_STAGING: str = os.environ.get("KUBE_DEPLOYMENT_STAGING", "myapp-staging")
    KUBE_DEPLOYMENT_PRODUCTION: str = os.environ.get("KUBE_DEPLOYMENT_PRODUCTION", "myapp-production")

    # ── Audit Logging ─────────────────────────────────────────────────────────
    AUDIT_LOG_PATH: str = os.environ.get("AUDIT_LOG_PATH", "/var/log/deploybot/audit.log")

    @classmethod
    def validate(cls):
        """Call on startup to fail fast if required config is missing."""
        required = {
            "TELEGRAM_BOT_TOKEN": cls.TELEGRAM_BOT_TOKEN,
            "ADMIN_TELEGRAM_IDS": cls._ADMIN_IDS_RAW,
            "REGISTRY_URL": cls.REGISTRY_URL,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise EnvironmentError(f"Missing required environment variables: {missing}")
