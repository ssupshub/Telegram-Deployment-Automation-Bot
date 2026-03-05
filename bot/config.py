"""
config.py - Centralized configuration from environment variables.
Never hardcode secrets. All sensitive values come from the environment.

Design: ALL values are read lazily via classmethods at call time — never
frozen at import time. This ensures:
  - monkeypatch.setenv() in tests works without reloading the module
  - Docker env-injection timing differences never cause stale reads
  - Config.validate() and _safe_env() always see the same values
"""

import os
from typing import Set


class Config:

    # ── Bot Token ─────────────────────────────────────────────────────────────

    @classmethod
    def get_telegram_bot_token(cls) -> str:
        """Return the Telegram bot token, read lazily from the environment."""
        return os.environ.get("TELEGRAM_BOT_TOKEN", "")

    # ── RBAC ─────────────────────────────────────────────────────────────────

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
        return cls._parse_ids(os.environ.get("ADMIN_TELEGRAM_IDS", ""))

    @classmethod
    def staging_ids(cls) -> Set[int]:
        """Staging users PLUS all admins (admins are a superset)."""
        return cls._parse_ids(os.environ.get("STAGING_TELEGRAM_IDS", "")) | cls.admin_ids()

    @classmethod
    def is_admin(cls, user_id: int) -> bool:
        return user_id in cls.admin_ids()

    @classmethod
    def is_authorized(cls, user_id: int) -> bool:
        """Any user in either list is authorized to use the bot."""
        return user_id in cls.staging_ids()

    # ── GitHub / Registry ────────────────────────────────────────────────────

    @classmethod
    def github_repo(cls) -> str:
        return os.environ.get("GITHUB_REPO", "myorg/myapp")

    @classmethod
    def github_token(cls) -> str:
        return os.environ.get("GITHUB_TOKEN", "")

    @classmethod
    def github_branch_staging(cls) -> str:
        return os.environ.get("GITHUB_BRANCH_STAGING", "develop")

    @classmethod
    def github_branch_production(cls) -> str:
        return os.environ.get("GITHUB_BRANCH_PRODUCTION", "main")

    @classmethod
    def registry_url(cls) -> str:
        return os.environ.get("REGISTRY_URL", "")

    @classmethod
    def registry_image(cls) -> str:
        return os.environ.get("REGISTRY_IMAGE", "myapp")

    @classmethod
    def aws_region(cls) -> str:
        return os.environ.get("AWS_REGION", "us-east-1")

    # ── Server / SSH ─────────────────────────────────────────────────────────

    @classmethod
    def staging_host(cls) -> str:
        return os.environ.get("STAGING_HOST", "")

    @classmethod
    def production_host(cls) -> str:
        return os.environ.get("PRODUCTION_HOST", "")

    @classmethod
    def deploy_user(cls) -> str:
        return os.environ.get("DEPLOY_USER", "deploy")

    @classmethod
    def ssh_key_path(cls) -> str:
        return os.environ.get("SSH_KEY_PATH", "/app/secrets/deploy_key")

    # ── Health Checks ────────────────────────────────────────────────────────

    @classmethod
    def staging_health_url(cls) -> str:
        return os.environ.get("STAGING_HEALTH_URL", "http://staging.example.com/health")

    @classmethod
    def production_health_url(cls) -> str:
        return os.environ.get("PRODUCTION_HEALTH_URL", "http://production.example.com/health")

    @classmethod
    def health_check_timeout(cls) -> int:
        return int(os.environ.get("HEALTH_CHECK_TIMEOUT", "30"))

    @classmethod
    def health_check_retries(cls) -> int:
        return int(os.environ.get("HEALTH_CHECK_RETRIES", "5"))

    # ── Deployment ────────────────────────────────────────────────────────────

    @classmethod
    def deploy_timeout_seconds(cls) -> int:
        """Max seconds to wait for a deploy/rollback subprocess before killing it."""
        return int(os.environ.get("DEPLOY_TIMEOUT_SECONDS", "600"))

    # ── Kubernetes (optional) ─────────────────────────────────────────────────

    @classmethod
    def use_kubernetes(cls) -> bool:
        return os.environ.get("USE_KUBERNETES", "false").lower() == "true"

    @classmethod
    def kube_namespace(cls) -> str:
        return os.environ.get("KUBE_NAMESPACE", "default")

    @classmethod
    def kube_deployment_staging(cls) -> str:
        return os.environ.get("KUBE_DEPLOYMENT_STAGING", "myapp-staging")

    @classmethod
    def kube_deployment_production(cls) -> str:
        return os.environ.get("KUBE_DEPLOYMENT_PRODUCTION", "myapp-production")

    # ── Audit Logging ─────────────────────────────────────────────────────────

    @classmethod
    def audit_log_path(cls) -> str:
        return os.environ.get("AUDIT_LOG_PATH", "/var/log/deploybot/audit.log")

    # ── Validation ────────────────────────────────────────────────────────────

    @classmethod
    def validate(cls) -> None:
        """Call on startup to fail fast if required config is missing."""
        required = {
            "TELEGRAM_BOT_TOKEN": cls.get_telegram_bot_token(),
            "ADMIN_TELEGRAM_IDS": os.environ.get("ADMIN_TELEGRAM_IDS", ""),
            "REGISTRY_URL": cls.registry_url(),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise EnvironmentError(f"Missing required environment variables: {missing}")
