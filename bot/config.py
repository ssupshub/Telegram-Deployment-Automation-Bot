"""
config.py - Centralized configuration from environment variables.
Never hardcode secrets. All sensitive values come from the environment.

"""

import os
from typing import Set


class Config:
    # ── Bot Secrets ──────────────────────────────────────────────────────────
    @classmethod
    def _get(cls, key: str, default: str = "") -> str:
        """Read an env var at call time (not at import time)."""
        return os.environ.get(key, default)

    @classmethod
    def get_telegram_bot_token(cls) -> str:
        """
        Return the Telegram bot token from the environment.

        FIX FOR Ruff F811 (redefinition of unused name):
        The original code defined TELEGRAM_BOT_TOKEN twice in the same class body:
          1. As a @property (instance-level descriptor)
          2. As a plain class-level attribute (str)

        Python executes class bodies top-to-bottom, so the class attribute
        silently overwrote the @property descriptor — meaning the @property
        was NEVER reachable. Ruff F811 correctly flagged this.

        Fix: remove both conflicting definitions and replace with a single
        @classmethod. This gives one name, one definition, with lazy env
        evaluation so monkeypatch.setenv() works in tests without reloading.
        """
        return os.environ.get("TELEGRAM_BOT_TOKEN", "")

    # ── RBAC ─────────────────────────────────────────────────────────────────
    # BUG FIX: read raw ID strings inside classmethods so that monkeypatch.setenv()
    # works without reload_config().  The class-level attributes below are
    # intentionally *not* used; they exist only for static-analysis tools.
    _ADMIN_IDS_RAW: str = ""    # sentinel; actual value read in admin_ids()
    _STAGING_IDS_RAW: str = ""  # sentinel; actual value read in staging_ids()

    @classmethod
    def _parse_ids(cls, raw: str) -> Set[int]:
        """Parse comma-separated integer IDs, ignoring blanks/invalid."""
        ids: Set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        return ids

    @classmethod
    def admin_ids(cls) -> Set[int]:
        # BUG FIX: read from os.environ every time, not from the stale class attr.
        return cls._parse_ids(os.environ.get("ADMIN_TELEGRAM_IDS", ""))

    @classmethod
    def staging_ids(cls) -> Set[int]:
        """Staging users PLUS all admins (admins are supersets)."""
        return cls._parse_ids(os.environ.get("STAGING_TELEGRAM_IDS", "")) | cls.admin_ids()

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

    REGISTRY_URL: str = os.environ.get("REGISTRY_URL", "")
    REGISTRY_IMAGE: str = os.environ.get("REGISTRY_IMAGE", "myapp")

    # BUG FIX: AWS_REGION was missing; deploy.sh needs it for ECR login.
    AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")

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
        # BUG FIX: read directly from env (not stale class attrs) so this works
        # whether or not the module has been reloaded.
        required = {
            "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            "ADMIN_TELEGRAM_IDS": os.environ.get("ADMIN_TELEGRAM_IDS", ""),
            "REGISTRY_URL": os.environ.get("REGISTRY_URL", ""),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise EnvironmentError(f"Missing required environment variables: {missing}")
