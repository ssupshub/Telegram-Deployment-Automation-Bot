"""
test_config.py - Tests for Config class
"""
import os
import importlib
import pytest


def reload_config():
    import config
    importlib.reload(config)
    return config.Config


class TestIdParsing:
    def test_parses_single_admin_id(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "12345")
        Config = reload_config()
        assert 12345 in Config.admin_ids()

    def test_parses_multiple_admin_ids(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111,222,333")
        Config = reload_config()
        assert Config.admin_ids() == {111, 222, 333}

    def test_ignores_whitespace_in_ids(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", " 111 , 222 , 333 ")
        Config = reload_config()
        assert Config.admin_ids() == {111, 222, 333}

    def test_ignores_empty_string(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "")
        Config = reload_config()
        assert Config.admin_ids() == set()

    def test_ignores_invalid_non_numeric_entries(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111,abc,222,!@#")
        Config = reload_config()
        assert Config.admin_ids() == {111, 222}

    def test_ignores_negative_numbers(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "-111,222")
        Config = reload_config()
        assert Config.admin_ids() == {222}


class TestRoleChecks:
    def test_admin_is_authorized(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("STAGING_TELEGRAM_IDS", "")
        Config = reload_config()
        assert Config.is_admin(111) is True
        assert Config.is_authorized(111) is True

    def test_staging_user_is_authorized_but_not_admin(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("STAGING_TELEGRAM_IDS", "333")
        Config = reload_config()
        assert Config.is_admin(333) is False
        assert Config.is_authorized(333) is True

    def test_unknown_user_is_not_authorized(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("STAGING_TELEGRAM_IDS", "333")
        Config = reload_config()
        assert Config.is_admin(999) is False
        assert Config.is_authorized(999) is False

    def test_staging_ids_include_admins(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("STAGING_TELEGRAM_IDS", "333")
        Config = reload_config()
        all_staging = Config.staging_ids()
        assert 111 in all_staging
        assert 333 in all_staging

    def test_empty_admin_list_means_no_admins(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "")
        Config = reload_config()
        assert Config.is_admin(111) is False


class TestValidate:
    def test_validate_passes_when_all_required_vars_set(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token123")
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("REGISTRY_URL", "123.dkr.ecr.us-east-1.amazonaws.com")
        Config = reload_config()
        Config.validate()

    def test_validate_raises_on_missing_token(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("REGISTRY_URL", "some-registry")
        Config = reload_config()
        with pytest.raises(EnvironmentError, match="TELEGRAM_BOT_TOKEN"):
            Config.validate()

    def test_validate_raises_on_missing_admin_ids(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token123")
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "")
        monkeypatch.setenv("REGISTRY_URL", "some-registry")
        Config = reload_config()
        with pytest.raises(EnvironmentError, match="ADMIN_TELEGRAM_IDS"):
            Config.validate()

    def test_validate_raises_on_missing_registry(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token123")
        monkeypatch.setenv("ADMIN_TELEGRAM_IDS", "111")
        monkeypatch.setenv("REGISTRY_URL", "")
        Config = reload_config()
        with pytest.raises(EnvironmentError, match="REGISTRY_URL"):
            Config.validate()
