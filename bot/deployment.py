"""
deployment.py - Deployment Manager
=====================================
Orchestrates the full deployment lifecycle:
  1. Pull latest code from GitHub
  2. Build and push Docker image
  3. Deploy via Docker Compose or Kubernetes
  4. Health check (with retries)
  5. Auto-rollback on failure

SECURITY NOTE: All shell commands use a fixed argument list passed to
asyncio.create_subprocess_exec (never shell=True with user input).
This prevents command injection attacks completely.
"""

import asyncio
import logging
import re
import subprocess
from typing import AsyncGenerator, Optional

import aiohttp

from config import Config

logger = logging.getLogger(__name__)

# Valid short-SHA / full-SHA: 4-40 hex characters.
_COMMIT_RE = re.compile(r'^[0-9a-f]{4,40}$')

_VALID_ENVS = frozenset({"staging", "production"})


class DeploymentManager:

    # ── Git Helpers ────────────────────────────────────────────────────────────

    def get_latest_commit(self, branch: Optional[str] = None) -> str:
        """
        Get the latest commit hash from the local git repo.
        When branch is provided, resolves the remote ref so the returned hash
        is the tip of that branch, not whatever is checked out locally.
        """
        try:
            if branch:
                result = subprocess.run(
                    ["git", "rev-parse", "--short", f"origin/{branch}"],
                    capture_output=True, text=True, check=True,
                    cwd="/app/repo",
                )
            else:
                result = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
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

        Raises ValueError (not AssertionError) for invalid inputs — fix #10.
        Wrapped in asyncio.wait_for() with a configurable timeout — fix #17.
        """
        # Fix #10: use ValueError, not AssertionError, for input validation.
        if environment not in _VALID_ENVS:
            raise ValueError(f"Invalid environment: {environment!r}")
        if commit != "unknown" and not _COMMIT_RE.match(commit):
            raise ValueError(f"Suspicious commit hash: {commit!r}")

        deploy_script = "/app/scripts/deploy.sh"
        cmd = [deploy_script, environment, commit]
        timeout = Config.deploy_timeout_seconds()

        logger.info("Executing deployment: %s (timeout=%ds)", cmd, timeout)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._safe_env(),
            )

            # Fix #17: enforce a hard timeout so a hanging deploy.sh never
            # blocks the bot indefinitely.
            try:
                async with asyncio.timeout(timeout):
                    async for line in proc.stdout:
                        decoded = line.decode("utf-8", errors="replace").rstrip()
                        logger.info("[deploy/%s] %s", environment, decoded)
                        yield decoded
                    await proc.wait()
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                yield f"ERROR: Deploy timed out after {timeout}s"
                return

            if proc.returncode != 0:
                error_line = f"ERROR: Deploy script exited with code {proc.returncode}"
                logger.error("[deploy/%s] %s", environment, error_line)
                yield error_line

        except Exception as e:
            logger.exception("Deployment execution error")
            yield f"ERROR: {e}"

    async def run_rollback(self, environment: str) -> AsyncGenerator[str, None]:
        """Stream output from the rollback script with a hard timeout."""
        if environment not in _VALID_ENVS:
            raise ValueError(f"Invalid environment: {environment!r}")

        rollback_script = "/app/scripts/rollback.sh"
        cmd = [rollback_script, environment]
        timeout = Config.deploy_timeout_seconds()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._safe_env(),
            )
            try:
                async with asyncio.timeout(timeout):
                    async for line in proc.stdout:
                        decoded = line.decode("utf-8", errors="replace").rstrip()
                        yield decoded
                    await proc.wait()
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                yield f"ERROR: Rollback timed out after {timeout}s"
                return

            if proc.returncode != 0:
                yield f"ERROR: Rollback script exited with code {proc.returncode}"
        except Exception as e:
            yield f"ERROR during rollback: {e}"

    # ── Status ─────────────────────────────────────────────────────────────────

    async def get_status(self) -> dict:
        """
        Check health of all environments and return status dict.

        Fix #2: health checks run concurrently via asyncio.gather(), not
        sequentially — a 10s timeout on one env no longer blocks the other.
        """
        staging_health_url = Config.staging_health_url()
        production_health_url = Config.production_health_url()

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            # Fix #2: concurrent health checks
            staging_healthy, production_healthy = await asyncio.gather(
                self._check_health(session, staging_health_url),
                self._check_health(session, production_health_url),
            )

        return {
            "staging": {
                "health_url": staging_health_url,
                "commit": self._get_deployed_commit("staging"),
                "deployed_at": self._get_deployed_at("staging"),
                "healthy": staging_healthy,
            },
            "production": {
                "health_url": production_health_url,
                "commit": self._get_deployed_commit("production"),
                "deployed_at": self._get_deployed_at("production"),
                "healthy": production_healthy,
            },
        }

    async def _check_health(self, session: aiohttp.ClientSession, url: str) -> bool:
        """Return True if the health endpoint returns HTTP 200."""
        try:
            async with session.get(url) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _get_deployed_commit(self, environment: str) -> str:
        """Read the last deployed commit from a state file written by deploy.sh."""
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

        Fix #8: HOME is set to /home/botuser to match the non-root container
        user defined in Dockerfile, so AWS CLI, SSH known_hosts, and Docker
        credential helpers all resolve paths correctly.
        """
        return {
            # Fix #8: match the non-root user in Dockerfile (USER botuser)
            "HOME": "/home/botuser",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "REGISTRY_URL": Config.registry_url(),
            "REGISTRY_IMAGE": Config.registry_image(),
            "STAGING_HOST": Config.staging_host(),
            "PRODUCTION_HOST": Config.production_host(),
            "DEPLOY_USER": Config.deploy_user(),
            "SSH_KEY_PATH": Config.ssh_key_path(),
            "KUBE_NAMESPACE": Config.kube_namespace(),
            "USE_KUBERNETES": str(Config.use_kubernetes()).lower(),
            "AWS_REGION": Config.aws_region(),
            "STAGING_HEALTH_URL": Config.staging_health_url(),
            "PRODUCTION_HEALTH_URL": Config.production_health_url(),
        }
