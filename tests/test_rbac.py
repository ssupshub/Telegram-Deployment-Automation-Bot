"""test_rbac.py - Tests for the @require_role decorator"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from conftest import make_update, make_context


def _make_handler(role):
    from rbac import require_role
    called = []
    @require_role(role)
    async def dummy_handler(update, context):
        called.append(True)
    return dummy_handler, called


class TestAdminRole:
    @pytest.mark.asyncio
    async def test_admin_user_can_call_admin_handler(self):
        from rbac import Role
        handler, called = _make_handler(Role.ADMIN)
        update = make_update(user_id=111)
        with patch("rbac.Config.is_admin", return_value=True):
            await handler(update, make_context())
        assert called

    @pytest.mark.asyncio
    async def test_staging_user_blocked_from_admin_handler(self):
        from rbac import Role
        handler, called = _make_handler(Role.ADMIN)
        update = make_update(user_id=333)
        with patch("rbac.Config.is_admin", return_value=False):
            await handler(update, make_context())
        assert not called
        update.effective_message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_user_blocked_from_admin_handler(self):
        from rbac import Role
        handler, called = _make_handler(Role.ADMIN)
        update = make_update(user_id=999)
        with patch("rbac.Config.is_admin", return_value=False):
            await handler(update, make_context())
        assert not called

    @pytest.mark.asyncio
    async def test_denial_message_mentions_admin_role(self):
        from rbac import Role
        handler, _ = _make_handler(Role.ADMIN)
        update = make_update(user_id=999)
        with patch("rbac.Config.is_admin", return_value=False):
            await handler(update, make_context())
        msg = update.effective_message.reply_text.call_args[0][0]
        assert "admin" in msg.lower()


class TestStagingRole:
    @pytest.mark.asyncio
    async def test_staging_user_can_call_staging_handler(self):
        from rbac import Role
        handler, called = _make_handler(Role.STAGING)
        update = make_update(user_id=333)
        with patch("rbac.Config.is_authorized", return_value=True):
            await handler(update, make_context())
        assert called

    @pytest.mark.asyncio
    async def test_admin_user_can_call_staging_handler(self):
        from rbac import Role
        handler, called = _make_handler(Role.STAGING)
        update = make_update(user_id=111)
        with patch("rbac.Config.is_authorized", return_value=True):
            await handler(update, make_context())
        assert called

    @pytest.mark.asyncio
    async def test_unknown_user_blocked_from_staging_handler(self):
        from rbac import Role
        handler, called = _make_handler(Role.STAGING)
        update = make_update(user_id=999)
        with patch("rbac.Config.is_authorized", return_value=False):
            await handler(update, make_context())
        assert not called


class TestRbacEdgeCases:
    @pytest.mark.asyncio
    async def test_none_user_is_silently_ignored(self):
        from rbac import Role
        handler, called = _make_handler(Role.STAGING)
        update = make_update(user_id=333)
        update.effective_user = None
        with patch("rbac.Config.is_authorized", return_value=True):
            await handler(update, make_context())
        assert not called

    @pytest.mark.asyncio
    async def test_second_admin_id_also_works(self):
        from rbac import Role
        handler, called = _make_handler(Role.ADMIN)
        update = make_update(user_id=222)
        with patch("rbac.Config.is_admin", return_value=True):
            await handler(update, make_context())
        assert called

    @pytest.mark.asyncio
    async def test_decorator_preserves_function_name(self):
        from rbac import require_role, Role
        @require_role(Role.STAGING)
        async def my_special_handler(update, context):
            pass
        assert my_special_handler.__name__ == "my_special_handler"

    @pytest.mark.asyncio
    async def test_denial_reply_is_sent_once(self):
        from rbac import Role
        handler, _ = _make_handler(Role.STAGING)
        update = make_update(user_id=999)
        with patch("rbac.Config.is_authorized", return_value=False):
            await handler(update, make_context())
        assert update.effective_message.reply_text.await_count == 1
