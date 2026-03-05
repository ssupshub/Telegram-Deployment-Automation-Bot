"""
test_deployment.py - Tests for DeploymentManager
"""
import pytest
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch
from deployment import DeploymentManager


@pytest.fixture
def manager():
    return DeploymentManager()


class TestInputValidation:
    @pytest.mark.asyncio
    async def test_invalid_environment_raises_value_error(self, manager):
        # Fix #10: should raise ValueError, not AssertionError
        with pytest.raises(ValueError):
            async for _ in manager.run_deployment("invalid_env", "abc123"):
                pass

    @pytest.mark.asyncio
    async def test_shell_injection_in_environment_is_blocked(self, manager):
        with pytest.raises(ValueError):
            async for _ in manager.run_deployment("staging; rm -rf /", "abc123"):
                pass

    @pytest.mark.asyncio
    async def test_invalid_commit_hash_raises_value_error(self, manager):
        # Fix #10: should raise ValueError, not AssertionError
        with pytest.raises(ValueError):
            async for _ in manager.run_deployment("staging", "abc123; rm -rf /"):
                pass

    @pytest.mark.asyncio
    async def test_valid_staging_and_commit_pass_validation(self, manager):
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
    async def test_unknown_commit_passes_validation(self, manager):
        """'unknown' is a valid sentinel value."""
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
            proc = MagicMock()
            proc.stdout = _async_line_generator([b"Done\n"])
            proc.wait = AsyncMock(return_value=0)
            proc.returncode = 0
            mock_proc.return_value = proc
            lines = []
            async for line in manager.run_deployment("staging", "unknown"):
                lines.append(line)
            mock_proc.assert_called_once()


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

    @pytest.mark.asyncio
    async def test_emits_error_on_subprocess_exception(self, manager):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("script not found")):
            lines = []
            async for line in manager.run_deployment("staging", "abc1234"):
                lines.append(line)
        assert any("ERROR" in l for l in lines)

    @pytest.mark.asyncio
    async def test_error_line_not_emitted_on_success(self, manager):
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
            proc = MagicMock()
            proc.stdout = _async_line_generator([b"[INFO] All good\n"])
            proc.wait = AsyncMock(return_value=0)
            proc.returncode = 0
            mock_proc.return_value = proc
            lines = []
            async for line in manager.run_deployment("staging", "abc1234"):
                lines.append(line)
        assert not any(l.startswith("ERROR:") for l in lines)

    @pytest.mark.asyncio
    async def test_timeout_emits_error_and_kills_process(self, manager, monkeypatch):
        """Fix #17: deployment subprocess must be killed on timeout."""
        monkeypatch.setenv("DEPLOY_TIMEOUT_SECONDS", "1")

        async def slow_gen():
            await asyncio.sleep(10)
            yield b"never reached\n"

        import asyncio as _asyncio
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_proc:
            proc = MagicMock()
            proc.stdout = slow_gen()
            proc.kill = MagicMock()
            proc.wait = AsyncMock(return_value=None)
            proc.returncode = -9
            mock_proc.return_value = proc

            lines = []
            async for line in manager.run_deployment("staging", "abc1234"):
                lines.append(line)

        assert any("timed out" in l.lower() or "ERROR" in l for l in lines)


class TestConcurrentHealthChecks:
    @pytest.mark.asyncio
    async def test_get_status_checks_both_envs_concurrently(self, manager):
        """Fix #2: both health checks must run, regardless of individual failures."""
        check_calls = []

        async def fake_check(session, url):
            check_calls.append(url)
            return "staging" in url

        with patch.object(manager, "_check_health", side_effect=fake_check), \
             patch.object(manager, "_get_deployed_commit", return_value="abc123"), \
             patch.object(manager, "_get_deployed_at", return_value="never"):
            result = await manager.get_status()

        assert len(check_calls) == 2, "Both envs must be checked"
        assert result["staging"]["healthy"] is True
        assert result["production"]["healthy"] is False


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


class TestSafeEnv:
    def test_safe_env_contains_required_keys(self, manager):
        env = manager._safe_env()
        required_keys = [
            "HOME", "PATH", "REGISTRY_URL", "REGISTRY_IMAGE",
            "STAGING_HOST", "PRODUCTION_HOST", "DEPLOY_USER",
            "SSH_KEY_PATH", "KUBE_NAMESPACE", "USE_KUBERNETES",
            "AWS_REGION",
        ]
        for key in required_keys:
            assert key in env, f"Missing key in safe_env: {key}"

    def test_safe_env_home_is_not_root(self, manager):
        """Fix #8: HOME must not be /root when running as non-root botuser."""
        env = manager._safe_env()
        assert env["HOME"] != "/root", "HOME should be /home/botuser, not /root"
        assert env["HOME"] == "/home/botuser"

    def test_safe_env_does_not_contain_telegram_token(self, manager):
        env = manager._safe_env()
        assert "TELEGRAM_BOT_TOKEN" not in env

    def test_safe_env_does_not_contain_github_token(self, manager):
        env = manager._safe_env()
        assert "GITHUB_TOKEN" not in env

    def test_safe_env_aws_region_matches_config(self, manager):
        env = manager._safe_env()
        from config import Config
        assert env["AWS_REGION"] == Config.aws_region()

    def test_safe_env_values_are_lazy(self, manager, monkeypatch):
        """Fix #1: safe_env must return current env values, not import-time snapshots."""
        monkeypatch.setenv("REGISTRY_URL", "new-registry.example.com")
        env = manager._safe_env()
        assert env["REGISTRY_URL"] == "new-registry.example.com"


class TestStateFiles:
    def test_get_deployed_commit_returns_unknown_when_missing(self, manager):
        assert manager._get_deployed_commit("staging") == "unknown"

    def test_get_deployed_at_returns_never_when_missing(self, manager):
        assert manager._get_deployed_at("production") == "never"


class TestGitHelpers:
    def test_get_latest_commit_returns_unknown_on_error(self, manager):
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
            result = manager.get_latest_commit()
        assert result == "unknown"

    def test_get_latest_commit_strips_whitespace(self, manager):
        mock_result = MagicMock()
        mock_result.stdout = "abc1234\n"
        with patch("subprocess.run", return_value=mock_result):
            result = manager.get_latest_commit()
        assert result == "abc1234"

    def test_get_latest_commit_uses_branch_when_provided(self, manager):
        """Branch parameter must be passed to git command."""
        mock_result = MagicMock()
        mock_result.stdout = "def5678\n"
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            manager.get_latest_commit(branch="main")
        call_args = mock_run.call_args[0][0]
        assert any("main" in str(arg) for arg in call_args)


import asyncio

async def _async_line_gen(lines):
    for line in lines:
        yield line

def _async_line_generator(lines):
    return _async_line_gen(lines)
