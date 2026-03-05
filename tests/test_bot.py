"""test_bot.py - Integration tests for Telegram command handlers"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from conftest import make_update, make_context, make_callback_update


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
            await cmd_deploy(update, ctx)
        update.message.reply_text.assert_awaited_once()
        call_kwargs = update.message.reply_text.call_args[1]
        assert "reply_markup" in call_kwargs

    @pytest.mark.asyncio
    async def test_confirm_dialog_uses_config_branch_not_local_head(self):
        """Fix #13: confirmation shows the configured production branch, not local HEAD."""
        from bot import cmd_deploy
        update = make_update(user_id=111)
        ctx = make_context(args=["production"])
        with patch("rbac.Config.is_authorized", return_value=True), \
             patch("bot.Config.is_admin", return_value=True), \
             patch("bot.Config.github_branch_production", return_value="main"), \
             patch("bot.deploy_manager") as mock_mgr:
            mock_mgr.get_latest_commit.return_value = "abc1234"
            await cmd_deploy(update, ctx)
        msg = update.message.reply_text.call_args[0][0]
        assert "main" in msg

    @pytest.mark.asyncio
    async def test_staging_deploy_runs_for_staging_user(self):
        from bot import cmd_deploy
        import bot
        bot._deploying.clear()
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

    @pytest.mark.asyncio
    async def test_failed_deploy_triggers_rollback(self):
        """Deploy failure must trigger auto-rollback."""
        from bot import cmd_deploy
        import bot
        bot._deploying.clear()
        update = make_update(user_id=333)
        ctx = make_context(args=["staging"])

        async def fake_deploy_fail(env, commit):
            yield "[INFO] Starting deploy"
            yield "ERROR: Deploy script exited with code 1"

        rollback_called = []

        async def tracking_rollback(env):
            rollback_called.append(env)
            yield "[ROLLBACK] Done"

        with patch("rbac.Config.is_authorized", return_value=True), \
             patch("bot.Config.is_admin", return_value=False), \
             patch("bot.deploy_manager") as mock_mgr, \
             patch("bot.send_chunked", new_callable=AsyncMock), \
             patch("bot.audit"):
            mock_mgr.get_latest_commit.return_value = "abc1234"
            mock_mgr.run_deployment = fake_deploy_fail
            mock_mgr.run_rollback = tracking_rollback
            await cmd_deploy(update, ctx)

        assert rollback_called, "Rollback must be called when deployment fails"

    @pytest.mark.asyncio
    async def test_deploy_lock_prevents_concurrent_deploys(self):
        """Fix #5: second deploy to same env is rejected while first is running."""
        from bot import _run_deployment
        import bot
        bot._deploying.add("staging")
        try:
            update = make_update(user_id=333)
            ctx = make_context()
            with patch("bot.deploy_manager") as mock_mgr:
                mock_mgr.get_latest_commit.return_value = "abc1234"
                await _run_deployment(update, ctx, "staging", {"id": 333, "username": "u", "full_name": "u"})
            # Should send "already in progress" message, not start a deploy
            sent_texts = [call[1].get("text", "") or call[0][0] if call[0] else call[1].get("text","")
                         for call in ctx.bot.send_message.call_args_list]
            assert any("already" in str(t).lower() or "progress" in str(t).lower()
                      for t in sent_texts)
        finally:
            bot._deploying.discard("staging")

    @pytest.mark.asyncio
    async def test_deploy_lock_released_after_success(self):
        """Fix #5: lock must be released after deploy completes."""
        from bot import _run_deployment
        import bot
        bot._deploying.clear()

        async def fake_deploy(env, commit):
            yield "[INFO] Done"

        update = make_update(user_id=333)
        ctx = make_context()
        with patch("bot.deploy_manager") as mock_mgr, \
             patch("bot.send_chunked", new_callable=AsyncMock), \
             patch("bot.audit"):
            mock_mgr.get_latest_commit.return_value = "abc1234"
            mock_mgr.run_deployment = fake_deploy
            await _run_deployment(update, ctx, "staging", {"id": 333, "username": "u", "full_name": "u"})

        assert "staging" not in bot._deploying, "Lock must be released after deploy"

    @pytest.mark.asyncio
    async def test_deploy_lock_released_after_exception(self):
        """Fix #5: lock must be released even if deploy raises an exception."""
        from bot import _run_deployment
        import bot
        bot._deploying.clear()

        async def exploding_deploy(env, commit):
            raise RuntimeError("unexpected error")
            yield  # make it an async generator

        update = make_update(user_id=333)
        ctx = make_context()
        with patch("bot.deploy_manager") as mock_mgr, \
             patch("bot.audit"):
            mock_mgr.get_latest_commit.return_value = "abc1234"
            mock_mgr.run_deployment = exploding_deploy
            try:
                await _run_deployment(update, ctx, "staging", {"id": 333, "username": "u", "full_name": "u"})
            except Exception:
                pass

        assert "staging" not in bot._deploying, "Lock must be released even after exception"


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
        from bot import handle_callback
        update = make_callback_update(user_id=333, data="deploy:production:abc1234")
        with patch("bot.Config.is_admin", return_value=False):
            await handle_callback(update, make_context())
        msg = update.callback_query.edit_message_text.call_args[0][0]
        assert any(w in msg.lower() for w in ["permission", "denied", "no longer"])

    @pytest.mark.asyncio
    async def test_callback_blocked_when_deploy_in_flight(self):
        """Fix #5: callback must be rejected if env is already deploying."""
        from bot import handle_callback
        import bot
        bot._deploying.add("production")
        try:
            update = make_callback_update(user_id=111, data="deploy:production:abc1234")
            with patch("bot.Config.is_admin", return_value=True):
                await handle_callback(update, make_context())
            msg = update.callback_query.edit_message_text.call_args[0][0]
            assert "already" in msg.lower() or "progress" in msg.lower()
        finally:
            bot._deploying.discard("production")

    @pytest.mark.asyncio
    async def test_none_user_in_callback_is_handled_gracefully(self):
        """Fix #3: callback with None user must not raise AttributeError."""
        from bot import handle_callback
        update = make_callback_update(user_id=111, data="deploy:cancel")
        update.effective_user = None
        # Should return silently without crashing
        await handle_callback(update, make_context())

    @pytest.mark.asyncio
    async def test_callback_with_colon_in_commit_hash(self):
        """maxsplit=2 means colons in the commit slot are handled correctly."""
        from bot import handle_callback
        update = make_callback_update(user_id=333, data="deploy:production:abc:extra")
        with patch("bot.Config.is_admin", return_value=False):
            await handle_callback(update, make_context())
        msg = update.callback_query.edit_message_text.call_args[0][0]
        assert any(w in msg.lower() for w in ["permission", "denied", "no longer"])


class TestStreamToChat:
    @pytest.mark.asyncio
    async def test_flushes_on_count(self):
        """Buffer flushes when 10 lines accumulate."""
        from bot import _stream_to_chat
        ctx = make_context()
        flush_calls = []

        async def fake_send_chunked(context, chat_id, text):
            flush_calls.append(text)

        async def gen():
            for i in range(10):
                yield f"line {i}"

        with patch("bot.send_chunked", side_effect=fake_send_chunked):
            success, lines = await _stream_to_chat(ctx, 123, gen())

        assert len(flush_calls) >= 1
        assert success is True

    @pytest.mark.asyncio
    async def test_detects_error_line(self):
        """Any ERROR: line marks the result as failed."""
        from bot import _stream_to_chat
        ctx = make_context()

        async def gen():
            yield "INFO: starting"
            yield "ERROR: Deploy script exited with code 1"

        with patch("bot.send_chunked", new_callable=AsyncMock):
            success, lines = await _stream_to_chat(ctx, 123, gen())

        assert success is False


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
