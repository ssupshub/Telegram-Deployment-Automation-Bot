"""
conftest.py - Shared Pytest Fixtures
"""
import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock

_HERE = os.path.dirname(os.path.abspath(__file__))
_BOT_DIR = os.path.normpath(os.path.join(_HERE, "..", "bot"))
if _BOT_DIR not in sys.path:
    sys.path.insert(0, _BOT_DIR)


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234567890:test-token-abc")
    monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111,222")
    monkeypatch.setenv("STAGING_TELEGRAM_IDS", "333,444")
    monkeypatch.setenv("REGISTRY_URL", "123456789.dkr.ecr.us-east-1.amazonaws.com")
    monkeypatch.setenv("REGISTRY_IMAGE", "myapp")
    monkeypatch.setenv("STAGING_HOST", "10.0.1.10")
    monkeypatch.setenv("PRODUCTION_HOST", "10.0.2.10")
    monkeypatch.setenv("DEPLOY_USER", "deploy")
    monkeypatch.setenv("SSH_KEY_PATH", "/tmp/test_deploy_key")
    monkeypatch.setenv("STAGING_HEALTH_URL", "http://staging.example.com/health")
    monkeypatch.setenv("PRODUCTION_HEALTH_URL", "http://production.example.com/health")
    monkeypatch.setenv("AUDIT_LOG_PATH", "/tmp/test_audit.log")
    monkeypatch.setenv("USE_KUBERNETES", "false")
    monkeypatch.setenv("KUBE_NAMESPACE", "default")
    monkeypatch.setenv("AWS_REGION", "us-east-1")


def make_user(user_id: int, username: str = "testuser"):
    user = MagicMock()
    user.id = user_id
    user.username = username
    user.full_name = f"Test User {user_id}"
    return user


def make_update(user_id: int, username: str = "testuser", text: str = "/deploy staging"):
    user = make_user(user_id, username)
    message = MagicMock()
    message.text = text
    message.reply_text = AsyncMock()
    message.chat_id = 99999
    update = MagicMock()
    update.effective_user = user
    update.effective_message = message
    update.effective_chat = MagicMock()
    update.effective_chat.id = 99999
    update.message = message
    return update


def make_callback_update(user_id: int, data: str):
    user = make_user(user_id)
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.effective_user = user
    update.callback_query = query
    update.effective_chat = MagicMock()
    update.effective_chat.id = 99999
    return update


def make_context(args=None):
    context = MagicMock()
    context.args = args or []
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


@pytest.fixture
def admin_update():
    return make_update(user_id=111, username="admin_user")

@pytest.fixture
def staging_update():
    return make_update(user_id=333, username="staging_user")

@pytest.fixture
def unauthorized_update():
    return make_update(user_id=999, username="hacker")

@pytest.fixture
def ctx():
    return make_context()
