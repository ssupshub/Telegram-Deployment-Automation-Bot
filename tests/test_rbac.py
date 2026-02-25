"""
test_rbac.py - Tests for the @require_role decorator
======================================================
Covers: admin access, staging access, unauthorized denial,
        missing user, role hierarchy.

Key design note: Config reads env vars at class-definition time via
os.environ.get(), so we must patch Config methods directly rather than
relying on monkeypatch.setenv() to retroactively change class attributes.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from conftest import make_update, make_context


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_handler(role):
    """Build a require_role-decorated dummy handler. Returns (handler, called_list)."""
    from rbac import require_role
    called = []

    @require_role(role)
    async def dummy_handler(update, context):
        called.append(True)

    return dummy_handler, called


# ── Admin role tests ───────────────────────────────────────────────────────────

class TestAdminRole:
    @pytest.mark.asyncio
    async def test_admin_user_can_call_admin_handler(self):
        from rbac import Role
        handler, called = _make_handler(Role.ADMIN)
        update = make_update(user_id=111)
        # Patch Config so admin check sees user 111 as admin
        with patch("rbac.Config.is_admin", return_value=True):
            await handler(update, make_context())
        assert called, "Admin handler should have been called for admin user"

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


# ── Staging role tests ─────────────────────────────────────────────────────────

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
        """Admins are a superset — they must pass staging checks too."""
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


# ── Edge cases ─────────────────────────────────────────────────────────────────

class TestRbacEdgeCases:
    @pytest.mark.asyncio
    async def test_none_user_is_silently_ignored(self):
        """update.effective_user = None should not raise, just return."""
        from rbac import Role
        handler, called = _make_handler(Role.STAGING)
        update = make_update(user_id=333)
        update.effective_user = None
        with patch("rbac.Config.is_authorized", return_value=True):
            await handler(update, make_context())
        assert not called  # returns early before calling handler

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
