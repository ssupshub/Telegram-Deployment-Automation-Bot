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

BUGS FIXED:
  - get_latest_commit() now honours the `branch` parameter instead of ignoring it.
  - Commit hash validation rewritten as a clear explicit check instead of the
    confusing generator-filter form that was hard to reason about.
  - AWS_REGION added to _safe_env() so ECR login works when the region is not
    us-east-1 (the deploy.sh script uses ${AWS_REGION:-us-east-1}, meaning a
    misconfigured region silently fell back to the wrong region).
  - run_deployment() now also yields the exit-code error line *before* the
    async-for loop exits, so callers that check for "ERROR" in streamed lines
    will reliably detect failures regardless of what the script printed.
"""

import asyncio
import logging
import re
import subprocess
from typing import AsyncGenerator

import aiohttp

from config import Config

logger = logging.getLogger(__name__)

# Valid short-SHA / full-SHA: 4-40 hex characters.
_COMMIT_RE = re.compile(r'^[0-9a-f]{4,40}$')

_VALID_ENVS = frozenset({"staging", "production"})


class DeploymentManager:

    # ── Git Helpers ────────────────────────────────────────────────────────────

    def get_latest_commit(self, branch: str = None) -> str:
        """
        Get the latest commit hash from the local git repo.

        BUG FIX: the original signature accepted `branch` but never used it,
        so the returned hash could be from whichever branch happened to be
        checked out locally — not the branch being deployed.  We now fetch
        and resolve the remote ref when a branch is specified.
        """
        try:
            if branch:
                # Resolve the remote ref without checking it out locally.
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

        BUG FIX — commit validation:
          The original assertion was:
              assert all(c in "0123456789abcdef" for c in commit if commit != "unknown")
          The `if commit != "unknown"` is the filter on the *generator*, not a guard
          around the whole expression.  That reads as: "check only those characters c
          for which `commit != 'unknown'`".  Since `commit != 'unknown'` is evaluated
          once against the *string* (not each character), it correctly short-circuits
          for commit=="unknown", but the intent is completely hidden and the pattern
          breaks the moment someone writes a similar expression and puts the condition
          in the wrong place.  Replaced with an explicit, readable guard.
        """
        # --- input validation (belt-and-suspenders; bot.py also checks) --------
        if environment not in _VALID_ENVS:
            raise AssertionError(f"Invalid environment: {environment!r}")

        # Allow the sentinel "unknown" through; reject anything that isn't a
        # proper hex commit SHA.
        if commit != "unknown" and not _COMMIT_RE.match(commit):
            raise AssertionError(f"Suspicious commit hash: {commit!r}")

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

            # Stream stdout/stderr merged output line by line.
            async for line in proc.stdout:
                decoded = line.decode("utf-8", errors="replace").rstrip()
                logger.info("[deploy/%s] %s", environment, decoded)
                yield decoded

            await proc.wait()

            if proc.returncode != 0:
                # Yield the error line BEFORE returning so callers that check
                # streamed output for "ERROR" will always see it.
                error_line = f"ERROR: Deploy script exited with code {proc.returncode}"
                logger.error("[deploy/%s] %s", environment, error_line)
                yield error_line

        except Exception as e:
            logger.exception("Deployment execution error")
            yield f"ERROR: {e}"

    async def run_rollback(self, environment: str) -> AsyncGenerator[str, None]:
        """Stream output from the rollback script."""
        if environment not in _VALID_ENVS:
            raise AssertionError(f"Invalid environment: {environment!r}")

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
            if proc.returncode != 0:
                yield f"ERROR: Rollback script exited with code {proc.returncode}"
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

        # Check health endpoints concurrently.
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

        BUG FIX: AWS_REGION was missing.  deploy.sh calls:
            aws ecr get-login-password --region "${AWS_REGION:-us-east-1}"
        Without AWS_REGION in the subprocess environment the region always
        falls back to us-east-1, silently breaking ECR auth for any other region.
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
            # BUG FIX: include AWS_REGION so ECR login targets the correct region.
            "AWS_REGION": Config.AWS_REGION,
            "STAGING_HEALTH_URL": Config.STAGING_HEALTH_URL,
            "PRODUCTION_HEALTH_URL": Config.PRODUCTION_HEALTH_URL,
        }
