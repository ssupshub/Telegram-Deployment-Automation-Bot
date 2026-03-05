"""
test_config.py - Tests for Config class
All Config values are now lazy classmethods, so monkeypatch.setenv()
works without any module reload.
"""
import os
import pytest


class TestIdParsing:
    def test_parses_single_admin_id(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "12345")
        from config import Config
        assert 12345 in Config.admin_ids()

    def test_parses_multiple_admin_ids(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111,222,333")
        from config import Config
        assert Config.admin_ids() == {111, 222, 333}

    def test_ignores_whitespace_in_ids(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", " 111 , 222 , 333 ")
        from config import Config
        assert Config.admin_ids() == {111, 222, 333}

    def test_ignores_empty_string(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "")
        from config import Config
        assert Config.admin_ids() == set()

    def test_ignores_invalid_non_numeric_entries(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111,abc,222,!@#")
        from config import Config
        assert Config.admin_ids() == {111, 222}

    def test_ignores_negative_numbers(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "-111,222")
        from config import Config
        assert Config.admin_ids() == {222}


class TestRoleChecks:
    def test_admin_is_authorized(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("STAGING_TELEGRAM_IDS", "")
        from config import Config
        assert Config.is_admin(111) is True
        assert Config.is_authorized(111) is True

    def test_staging_user_is_authorized_but_not_admin(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("STAGING_TELEGRAM_IDS", "333")
        from config import Config
        assert Config.is_admin(333) is False
        assert Config.is_authorized(333) is True

    def test_unknown_user_is_not_authorized(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("STAGING_TELEGRAM_IDS", "333")
        from config import Config
        assert Config.is_admin(999) is False
        assert Config.is_authorized(999) is False

    def test_staging_ids_include_admins(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("STAGING_TELEGRAM_IDS", "333")
        from config import Config
        all_staging = Config.staging_ids()
        assert 111 in all_staging
        assert 333 in all_staging

    def test_empty_admin_list_means_no_admins(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "")
        from config import Config
        assert Config.is_admin(111) is False


class TestValidate:
    def test_validate_passes_when_all_required_vars_set(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token123")
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("REGISTRY_URL", "123.dkr.ecr.us-east-1.amazonaws.com")
        from config import Config
        Config.validate()

    def test_validate_raises_on_missing_token(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("REGISTRY_URL", "some-registry")
        from config import Config
        with pytest.raises(EnvironmentError, match="TELEGRAM_BOT_TOKEN"):
            Config.validate()

    def test_validate_raises_on_missing_admin_ids(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token123")
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "")
        monkeypatch.setenv("REGISTRY_URL", "some-registry")
        from config import Config
        with pytest.raises(EnvironmentError, match="ADMIN_TELEGRAM_IDS"):
            Config.validate()

    def test_validate_raises_on_missing_registry(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token123")
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("REGISTRY_URL", "")
        from config import Config
        with pytest.raises(EnvironmentError, match="REGISTRY_URL"):
            Config.validate()


class TestGetTelegramBotToken:
    def test_returns_token_from_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "9876543210:ABC-test-token")
        from config import Config
        assert Config.get_telegram_bot_token() == "9876543210:ABC-test-token"

    def test_returns_empty_string_when_not_set(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        from config import Config
        assert Config.get_telegram_bot_token() == ""

    def test_reflects_env_changes_at_call_time(self, monkeypatch):
        """All classmethods read lazily — value must reflect current env."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "first-token")
        from config import Config
        assert Config.get_telegram_bot_token() == "first-token"
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "second-token")
        assert Config.get_telegram_bot_token() == "second-token"

    def test_all_config_methods_reflect_env_changes(self, monkeypatch):
        """Verify the consistency fix: ALL config reads are lazy, not just token."""
        monkeypatch.setenv("REGISTRY_URL", "registry-v1.example.com")
        from config import Config
        assert Config.registry_url() == "registry-v1.example.com"
        monkeypatch.setenv("REGISTRY_URL", "registry-v2.example.com")
        assert Config.registry_url() == "registry-v2.example.com"

    def test_aws_region_lazy(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "eu-west-1")
        from config import Config
        assert Config.aws_region() == "eu-west-1"

    def test_deploy_timeout_default(self):
        from config import Config
        assert Config.deploy_timeout_seconds() == 600

    def test_deploy_timeout_from_env(self, monkeypatch):
        monkeypatch.setenv("DEPLOY_TIMEOUT_SECONDS", "300")
        from config import Config
        assert Config.deploy_timeout_seconds() == 300
