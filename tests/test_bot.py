"""
test_bot.py - Integration tests for Telegram command handlers
==============================================================
Covers: /deploy, /rollback, /status, /help — including RBAC enforcement,
        production confirmation flow, callback handling, and error handler.

All Telegram API calls and DeploymentManager methods are mocked.

Design note: Config class attributes are set at import time via os.environ.get().
We patch Config.is_admin / Config.is_authorized directly rather than trying
to retroactively change env vars after the module is cached.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from conftest import make_update, make_context, make_callback_update


# ── Shared Config patches ──────────────────────────────────────────────────────

def _admin_patches():
    """Context managers that make user 111 look like an admin."""
    return [
        patch("bot.Config.is_admin", return_value=True),
        patch("bot.Config.is_authorized", return_value=True),
        patch("rbac.Config.is_admin", return_value=True),
        patch("rbac.Config.is_authorized", return_value=True),
    ]

def _staging_patches():
    """Context managers that make user 333 look like a staging user (not admin)."""
    return [
        patch("bot.Config.is_admin", return_value=False),
        patch("bot.Config.is_authorized", return_value=True),
        patch("rbac.Config.is_admin", return_value=False),
        patch("rbac.Config.is_authorized", return_value=True),
    ]

def _unauth_patches():
    """Context managers for an unauthorized user."""
    return [
        patch("bot.Config.is_admin", return_value=False),
        patch("bot.Config.is_authorized", return_value=False),
        patch("rbac.Config.is_admin", return_value=False),
        patch("rbac.Config.is_authorized", return_value=False),
    ]


# ── /help ─────────────────────────────────────────────────────────────────────

class TestHelpCommand:
    @pytest.mark.asyncio
    async def test_unauthorized_user_gets_denied(self):
        from bot import cmd_help
        update = make_update(user_id=999)
        with patch("bot.Config.is_authorized", return_value=False), \
             patch("bot.Config.is_admin", return_value=False):
            await cmd_help(update, make_context())
        msg = update.message.reply_text.call_args[0][0]
        assert "not authorized" in msg.lower()

    @pytest.mark.asyncio
    async def test_staging_user_sees_limited_commands(self):
        from bot import cmd_help
        update = make_update(user_id=333)
        with patch("bot.Config.is_authorized", return_value=True), \
             patch("bot.Config.is_admin", return_value=False):
            await cmd_help(update, make_context())
        msg = update.message.reply_text.call_args[0][0]
        assert "/deploy staging" in msg
        assert "/deploy production" not in msg

    @pytest.mark.asyncio
    async def test_admin_user_sees_all_commands(self):
        from bot import cmd_help
        update = make_update(user_id=111)
        with patch("bot.Config.is_authorized", return_value=True), \
             patch("bot.Config.is_admin", return_value=True):
            await cmd_help(update, make_context())
        msg = update.message.reply_text.call_args[0][0]
        assert "/deploy production" in msg
        assert "/rollback production" in msg


# ── /deploy ───────────────────────────────────────────────────────────────────

class TestDeployCommand:
    @pytest.mark.asyncio
    async def test_deploy_without_args_shows_usage(self):
        from bot import cmd_deploy
        update = make_update(user_id=333)
        ctx = make_context(args=[])
        with patch("rbac.Config.is_authorized", return_value=True):
            await cmd_deploy(update, ctx)
        msg = update.message.reply_text.call_args[0][0]
        assert "usage" in msg.lower() or "/deploy" in msg.lower()

    @pytest.mark.asyncio
    async def test_deploy_invalid_env_shows_usage(self):
        from bot import cmd_deploy
        update = make_update(user_id=333)
        ctx = make_context(args=["invalid_env"])
        with patch("rbac.Config.is_authorized", return_value=True):
            await cmd_deploy(update, ctx)
        update.message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_staging_user_blocked_from_production(self):
        """Staging user attempting /deploy production must be denied."""
        from bot import cmd_deploy
        update = make_update(user_id=333)
        ctx = make_context(args=["production"])
        with patch("rbac.Config.is_authorized", return_value=True), \
             patch("bot.Config.is_admin", return_value=False):
            await cmd_deploy(update, ctx)
        msg = update.message.reply_text.call_args[0][0]
        assert any(word in msg.lower() for word in ["admin", "denied", "require", "production"])

    @pytest.mark.asyncio
    async def test_unauthorized_user_blocked_entirely(self):
        from bot import cmd_deploy
        update = make_update(user_id=999)
        ctx = make_context(args=["staging"])
        with patch("rbac.Config.is_authorized", return_value=False):
            await cmd_deploy(update, ctx)
        update.effective_message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_admin_deploy_production_shows_confirmation(self):
        from bot import cmd_deploy
        update = make_update(user_id=111)
        ctx = make_context(args=["production"])
        with patch("rbac.Config.is_authorized", return_value=True), \
             patch("bot.Config.is_admin", return_value=True), \
             patch("bot.deploy_manager") as mock_mgr:
            mock_mgr.get_latest_commit.return_value = "abc1234"
            mock_mgr.get_current_branch.return_value = "main"
            await cmd_deploy(update, ctx)
        update.message.reply_text.assert_awaited_once()
        call_kwargs = update.message.reply_text.call_args[1]
        assert "reply_markup" in call_kwargs

    @pytest.mark.asyncio
    async def test_staging_deploy_runs_for_staging_user(self):
        from bot import cmd_deploy
        update = make_update(user_id=333)
        ctx = make_context(args=["staging"])

        async def fake_deploy(env, commit):
            yield "[INFO] Pulling code"
            yield "[INFO] Build complete"

        with patch("rbac.Config.is_authorized", return_value=True), \
             patch("bot.Config.is_admin", return_value=False), \
             patch("bot.deploy_manager") as mock_mgr, \
             patch("bot.send_chunked", new_callable=AsyncMock), \
             patch("bot.audit"):
            mock_mgr.get_latest_commit.return_value = "abc1234"
            mock_mgr.run_deployment = fake_deploy
            await cmd_deploy(update, ctx)

        ctx.bot.send_message.assert_awaited()


# ── /rollback ─────────────────────────────────────────────────────────────────

class TestRollbackCommand:
    @pytest.mark.asyncio
    async def test_rollback_requires_admin(self):
        from bot import cmd_rollback
        update = make_update(user_id=333)
        ctx = make_context(args=["production"])
        with patch("rbac.Config.is_admin", return_value=False):
            await cmd_rollback(update, ctx)
        update.effective_message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rollback_without_args_shows_usage(self):
        from bot import cmd_rollback
        update = make_update(user_id=111)
        ctx = make_context(args=[])
        with patch("rbac.Config.is_admin", return_value=True):
            await cmd_rollback(update, ctx)
        update.message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_admin_can_rollback_production(self):
        from bot import cmd_rollback
        update = make_update(user_id=111)
        ctx = make_context(args=["production"])

        async def fake_rollback(env):
            yield "[ROLLBACK] Restoring previous image"

        with patch("rbac.Config.is_admin", return_value=True), \
             patch("bot.deploy_manager") as mock_mgr, \
             patch("bot.audit") as mock_audit:
            mock_mgr.run_rollback = fake_rollback
            await cmd_rollback(update, ctx)
            mock_audit.log.assert_called()

        ctx.bot.send_message.assert_awaited()


# ── /status ───────────────────────────────────────────────────────────────────

class TestStatusCommand:
    @pytest.mark.asyncio
    async def test_status_requires_staging_role(self):
        from bot import cmd_status
        update = make_update(user_id=999)
        with patch("rbac.Config.is_authorized", return_value=False):
            await cmd_status(update, make_context())
        update.effective_message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_shows_both_environments(self):
        from bot import cmd_status
        update = make_update(user_id=333)
        fake_status = {
            "staging": {"healthy": True, "commit": "abc123", "deployed_at": "2024-01-01", "health_url": "http://s/health"},
            "production": {"healthy": False, "commit": "def456", "deployed_at": "2024-01-02", "health_url": "http://p/health"},
        }
        with patch("rbac.Config.is_authorized", return_value=True), \
             patch("bot.deploy_manager") as mock_mgr, \
             patch("bot.audit"):
            mock_mgr.get_status = AsyncMock(return_value=fake_status)
            await cmd_status(update, make_context())
        msg = update.message.reply_text.call_args[0][0]
        assert "staging" in msg.lower()
        assert "production" in msg.lower()

    @pytest.mark.asyncio
    async def test_status_shows_commit_hash(self):
        from bot import cmd_status
        update = make_update(user_id=333)
        fake_status = {
            "staging": {"healthy": True, "commit": "abc1234", "deployed_at": "never", "health_url": ""},
            "production": {"healthy": True, "commit": "def5678", "deployed_at": "never", "health_url": ""},
        }
        with patch("rbac.Config.is_authorized", return_value=True), \
             patch("bot.deploy_manager") as mock_mgr, \
             patch("bot.audit"):
            mock_mgr.get_status = AsyncMock(return_value=fake_status)
            await cmd_status(update, make_context())
        msg = update.message.reply_text.call_args[0][0]
        assert "abc1234" in msg
        assert "def5678" in msg


# ── Callback Handler (inline buttons) ─────────────────────────────────────────

class TestCallbackHandler:
    @pytest.mark.asyncio
    async def test_cancel_callback_cancels_deploy(self):
        from bot import handle_callback
        update = make_callback_update(user_id=111, data="deploy:cancel")
        with patch("bot.audit"):
            await handle_callback(update, make_context())
        msg = update.callback_query.edit_message_text.call_args[0][0]
        assert "cancel" in msg.lower()

    @pytest.mark.asyncio
    async def test_production_callback_rerequires_admin(self):
        """A staging user who somehow gets the callback data must still be blocked."""
        from bot import handle_callback
        update = make_callback_update(user_id=333, data="deploy:production:abc1234")
        with patch("bot.Config.is_admin", return_value=False):
            await handle_callback(update, make_context())
        msg = update.callback_query.edit_message_text.call_args[0][0]
        assert any(w in msg.lower() for w in ["permission", "denied", "no longer"])

    @pytest.mark.asyncio
    async def test_admin_confirms_production_deploy(self):
        from bot import handle_callback
        update = make_callback_update(user_id=111, data="deploy:production:abc1234")
        ctx = make_context()

        async def fake_deploy(env, commit):
            yield "[INFO] Deploying"

        with patch("bot.Config.is_admin", return_value=True), \
             patch("bot.deploy_manager") as mock_mgr, \
             patch("bot.send_chunked", new_callable=AsyncMock), \
             patch("bot.audit"):
            mock_mgr.run_deployment = fake_deploy
            mock_mgr.get_latest_commit.return_value = "abc1234"
            await handle_callback(update, ctx)

        update.callback_query.edit_message_text.assert_awaited()


# ── Error Handler ─────────────────────────────────────────────────────────────

class TestErrorHandler:
    @pytest.mark.asyncio
    async def test_error_handler_notifies_user(self):
        from bot import error_handler
        update = make_update(user_id=111)
        context = make_context()
        context.error = ValueError("something broke")
        await error_handler(update, context)
        update.effective_message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_handler_handles_none_update(self):
        from bot import error_handler
        context = make_context()
        context.error = ValueError("something broke")
        await error_handler(None, context)  # must not raise
