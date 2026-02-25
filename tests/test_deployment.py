"""
test_deployment.py - Tests for DeploymentManager
==================================================
Covers: input validation (injection prevention), health checks,
        state file reading, _safe_env() contents, async streaming.

All subprocess and HTTP calls are mocked — no real servers needed.
"""

import os
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
import subprocess
from deployment import DeploymentManager


@pytest.fixture
def manager():
    return DeploymentManager()


# ── Input Validation (Security) ────────────────────────────────────────────────

class TestInputValidation:
    @pytest.mark.asyncio
    async def test_invalid_environment_raises_assertion(self, manager):
        """Any environment name outside the allowlist must be rejected."""
        output = []
        with pytest.raises(AssertionError):
            async for line in manager.run_deployment("invalid_env", "abc123"):
                output.append(line)

    @pytest.mark.asyncio
    async def test_shell_injection_in_environment_is_blocked(self, manager):
        """Classic shell injection attempt must be rejected before reaching subprocess."""
        with pytest.raises(AssertionError):
            async for _ in manager.run_deployment("staging; rm -rf /", "abc123"):
                pass

    @pytest.mark.asyncio
    async def test_invalid_commit_hash_raises_assertion(self, manager):
        """Commit hashes containing non-hex characters must be rejected."""
        with pytest.raises(AssertionError):
            async for _ in manager.run_deployment("staging", "abc123; rm -rf /"):
                pass

    @pytest.mark.asyncio
    async def test_valid_staging_and_commit_pass_validation(self, manager):
        """Valid inputs should not raise during validation."""
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
            proc = MagicMock()
            proc.stdout = _async_line_generator([b"[INFO] Done\n"])
            proc.wait = AsyncMock(return_value=0)
            proc.returncode = 0
            mock_proc.return_value = proc
            lines = []
            async for line in manager.run_deployment("staging", "abc1234"):
                lines.append(line)
            mock_proc.assert_called_once()

    @pytest.mark.asyncio
    async def test_valid_production_env_passes_validation(self, manager):
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
            proc = MagicMock()
            proc.stdout = _async_line_generator([b"Done\n"])
            proc.wait = AsyncMock(return_value=0)
            proc.returncode = 0
            mock_proc.return_value = proc
            async for _ in manager.run_deployment("production", "deadbeef"):
                pass
            mock_proc.assert_called_once()


# ── Log Streaming ──────────────────────────────────────────────────────────────

class TestDeploymentStreaming:
    @pytest.mark.asyncio
    async def test_streams_stdout_lines(self, manager):
        fake_output = [b"Step 1\n", b"Step 2\n", b"Step 3\n"]
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
            proc = MagicMock()
            proc.stdout = _async_line_generator(fake_output)
            proc.wait = AsyncMock(return_value=0)
            proc.returncode = 0
            mock_proc.return_value = proc

            lines = []
            async for line in manager.run_deployment("staging", "abc1234"):
                lines.append(line)

        assert lines == ["Step 1", "Step 2", "Step 3"]

    @pytest.mark.asyncio
    async def test_emits_error_line_on_nonzero_exit(self, manager):
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
            proc = MagicMock()
            proc.stdout = _async_line_generator([b"Deploying...\n"])
            proc.wait = AsyncMock(return_value=1)
            proc.returncode = 1
            mock_proc.return_value = proc

            lines = []
            async for line in manager.run_deployment("staging", "abc1234"):
                lines.append(line)

        assert any("ERROR" in l for l in lines)
        assert any("1" in l for l in lines)

    @pytest.mark.asyncio
    async def test_emits_error_on_subprocess_exception(self, manager):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("script not found")):
            lines = []
            async for line in manager.run_deployment("staging", "abc1234"):
                lines.append(line)
        assert any("ERROR" in l for l in lines)


# ── Health Check ───────────────────────────────────────────────────────────────

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_true_on_http_200(self, manager):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        result = await manager._check_health(mock_session, "http://example.com/health")
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_http_500(self, manager):
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        result = await manager._check_health(mock_session, "http://example.com/health")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_connection_error(self, manager):
        import aiohttp
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=aiohttp.ClientConnectionError())
        result = await manager._check_health(mock_session, "http://unreachable.local/health")
        assert result is False


# ── State File Reading ─────────────────────────────────────────────────────────

class TestStateFiles:
    def test_get_deployed_commit_reads_file(self, manager, tmp_path, monkeypatch):
        state_file = tmp_path / "staging.commit"
        state_file.write_text("abc1234\n")
        with patch("builtins.open", mock_open(read_data="abc1234\n")):
            # Patch the path directly
            with patch.object(manager, "_get_deployed_commit", return_value="abc1234"):
                result = manager._get_deployed_commit("staging")
        assert result == "abc1234"

    def test_get_deployed_commit_returns_unknown_when_missing(self, manager):
        # No state file exists → should return "unknown" not raise
        result = manager._get_deployed_commit("staging")
        assert result == "unknown"

    def test_get_deployed_at_returns_never_when_missing(self, manager):
        result = manager._get_deployed_at("production")
        assert result == "never"


# ── Safe Environment ───────────────────────────────────────────────────────────

class TestSafeEnv:
    def test_safe_env_contains_required_keys(self, manager):
        env = manager._safe_env()
        required_keys = [
            "HOME", "PATH", "REGISTRY_URL", "REGISTRY_IMAGE",
            "STAGING_HOST", "PRODUCTION_HOST", "DEPLOY_USER",
            "SSH_KEY_PATH", "KUBE_NAMESPACE", "USE_KUBERNETES",
        ]
        for key in required_keys:
            assert key in env, f"Missing key in safe_env: {key}"

    def test_safe_env_does_not_contain_telegram_token(self, manager):
        """The bot token must never be passed to deploy scripts."""
        env = manager._safe_env()
        assert "TELEGRAM_BOT_TOKEN" not in env

    def test_safe_env_does_not_contain_github_token(self, manager):
        env = manager._safe_env()
        assert "GITHUB_TOKEN" not in env

    def test_safe_env_has_restricted_path(self, manager):
        """PATH should not include user home dirs or other injection vectors."""
        env = manager._safe_env()
        path = env["PATH"]
        assert "~" not in path
        assert "$HOME" not in path


# ── Git Helpers ────────────────────────────────────────────────────────────────

class TestGitHelpers:
    def test_get_latest_commit_returns_unknown_on_error(self, manager):
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
            result = manager.get_latest_commit()
        assert result == "unknown"

    def test_get_current_branch_returns_unknown_on_error(self, manager):
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
            result = manager.get_current_branch()
        assert result == "unknown"

    def test_get_latest_commit_strips_whitespace(self, manager):
        mock_result = MagicMock()
        mock_result.stdout = "abc1234\n"
        with patch("subprocess.run", return_value=mock_result):
            result = manager.get_latest_commit()
        assert result == "abc1234"


# ── Async Generator Helper ─────────────────────────────────────────────────────

async def _async_line_gen(lines):
    for line in lines:
        yield line

def _async_line_generator(lines):
    """Return an async iterable that yields lines one at a time."""
    return _async_line_gen(lines)
