"""Configuration loading, defaults and validation."""

from __future__ import annotations

import pytest

from copynion.config import Config
from copynion.models import Visibility


def test_defaults_are_the_private_option():
    cfg = Config()
    assert cfg.privacy.default_visibility == Visibility.APP_ONLY.value
    assert cfg.privacy.redaction_enabled is True
    assert cfg.privacy.allow_unencrypted_titles is False
    assert cfg.privacy.respect_private_browsing is True
    assert cfg.retention.detail_days == 90


def test_missing_config_file_yields_defaults(tmp_path):
    cfg = Config.load(tmp_path / "nope.toml")
    assert cfg.privacy.default_visibility == "app_only"


def test_written_default_config_reloads_identically(tmp_path):
    path = tmp_path / "config.toml"
    Config().write_default(path)
    assert path.stat().st_mode & 0o777 == 0o600
    cfg = Config.load(path)
    assert cfg.observation.poll_interval_seconds == 5.0
    assert cfg.retention.title_days == 30


def test_app_rules_are_read(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[privacy.apps]\ncode = "full"\n"1password" = "drop"\n')
    cfg = Config.load(path)
    assert cfg.privacy.apps == {"code": "full", "1password": "drop"}


def test_invalid_visibility_is_rejected(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[privacy]\ndefault_visibility = "everything"\n')
    with pytest.raises(ValueError, match="default_visibility"):
        Config.load(path)


def test_invalid_app_visibility_is_rejected(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[privacy.apps]\ncode = "maximum"\n')
    with pytest.raises(ValueError, match="must be one of"):
        Config.load(path)


def test_typo_in_setting_name_is_rejected(tmp_path):
    """Silently ignoring an unknown key could silently disable a privacy setting."""
    path = tmp_path / "c.toml"
    path.write_text("[privacy]\nredaction_enable = false\n")
    with pytest.raises(ValueError, match="unknown setting"):
        Config.load(path)


def test_nonsense_interval_is_rejected(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[observation]\npoll_interval_seconds = 0\n")
    with pytest.raises(ValueError, match="must be positive"):
        Config.load(path)
