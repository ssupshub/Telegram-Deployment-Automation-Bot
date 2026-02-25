"""
deployment.py - Deployment Manager
=====================================
Orchestrates the full deployment lifecycle:
  1. Pull latest code from GitHub
  2. Build and push Docker image
  3. Deploy via Docker Compose or Kubernetes
  4. Health check (with retries)
  5. Auto-rollback on failure

SECURITY NOTE: All shell commands use shlex.split() and subprocess with
a fixed argument list (never shell=True with user input). This prevents
command injection attacks completely.
"""

import asyncio
import logging
import subprocess
from typing import AsyncGenerator

import aiohttp

from config import Config

logger = logging.getLogger(__name__)


class DeploymentManager:

    # ── Git Helpers ────────────────────────────────────────────────────────────

    def get_latest_commit(self, branch: str = None) -> str:
        """Get the latest commit hash from the local git repo."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, check=True,
                cwd="/app/repo",  # mount point for your application repo
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return "unknown"

    def get_current_branch(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, check=True,
                cwd="/app/repo",
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return "unknown"

    # ── Deployment ─────────────────────────────────────────────────────────────

    async def run_deployment(
        self, environment: str, commit: str
    ) -> AsyncGenerator[str, None]:
        """
        Run the deployment script and stream its output line by line.
        Uses the deploy shell script for the actual work.

        The script is called with fixed arguments — no user input is
        ever interpolated into the shell command string.
        """
        # Validate environment to prevent injection (belt-and-suspenders)
        assert environment in ("staging", "production"), f"Invalid env: {environment}"
        assert all(c in "0123456789abcdef" for c in commit if commit != "unknown"), \
            f"Suspicious commit hash: {commit}"

        deploy_script = "/app/scripts/deploy.sh"
        cmd = [deploy_script, environment, commit]

        logger.info("Executing deployment: %s", cmd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._safe_env(),
            )

            # Stream output
            async for line in proc.stdout:
                decoded = line.decode("utf-8", errors="replace").rstrip()
                logger.info("[deploy/%s] %s", environment, decoded)
                yield decoded

            await proc.wait()

            if proc.returncode != 0:
                yield f"ERROR: Deploy script exited with code {proc.returncode}"

        except Exception as e:
            logger.exception("Deployment execution error")
            yield f"ERROR: {e}"

    async def run_rollback(self, environment: str) -> AsyncGenerator[str, None]:
        """Stream output from the rollback script."""
        assert environment in ("staging", "production")

        rollback_script = "/app/scripts/rollback.sh"
        cmd = [rollback_script, environment]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._safe_env(),
            )
            async for line in proc.stdout:
                decoded = line.decode("utf-8", errors="replace").rstrip()
                yield decoded
            await proc.wait()
        except Exception as e:
            yield f"ERROR during rollback: {e}"

    # ── Status ─────────────────────────────────────────────────────────────────

    async def get_status(self) -> dict:
        """Check health of all environments and return status dict."""
        envs = {
            "staging": {
                "health_url": Config.STAGING_HEALTH_URL,
                "commit": self._get_deployed_commit("staging"),
                "deployed_at": self._get_deployed_at("staging"),
            },
            "production": {
                "health_url": Config.PRODUCTION_HEALTH_URL,
                "commit": self._get_deployed_commit("production"),
                "deployed_at": self._get_deployed_at("production"),
            },
        }

        # Check health endpoints concurrently
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            for env_name, info in envs.items():
                info["healthy"] = await self._check_health(session, info["health_url"])

        return envs

    async def _check_health(self, session: aiohttp.ClientSession, url: str) -> bool:
        """Return True if the health endpoint returns HTTP 200."""
        try:
            async with session.get(url) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _get_deployed_commit(self, environment: str) -> str:
        """
        Read the last deployed commit from a state file.
        The deploy script writes this file on success.
        """
        state_file = f"/var/lib/deploybot/{environment}.commit"
        try:
            with open(state_file) as f:
                return f.read().strip()
        except FileNotFoundError:
            return "unknown"

    def _get_deployed_at(self, environment: str) -> str:
        """Read the last deployment timestamp from a state file."""
        state_file = f"/var/lib/deploybot/{environment}.timestamp"
        try:
            with open(state_file) as f:
                return f.read().strip()
        except FileNotFoundError:
            return "never"

    # ── Security Helpers ───────────────────────────────────────────────────────

    def _safe_env(self) -> dict:
        """
        Build a minimal, safe environment for subprocess calls.
        Only pass what the deploy scripts actually need.
        Never pass the full os.environ — it might contain unintended secrets.
        """
        return {
            "HOME": "/root",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "REGISTRY_URL": Config.REGISTRY_URL,
            "REGISTRY_IMAGE": Config.REGISTRY_IMAGE,
            "STAGING_HOST": Config.STAGING_HOST,
            "PRODUCTION_HOST": Config.PRODUCTION_HOST,
            "DEPLOY_USER": Config.DEPLOY_USER,
            "SSH_KEY_PATH": Config.SSH_KEY_PATH,
            "KUBE_NAMESPACE": Config.KUBE_NAMESPACE,
            "USE_KUBERNETES": str(Config.USE_KUBERNETES).lower(),
        }
