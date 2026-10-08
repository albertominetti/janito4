"""Stored OAuth details must block CLI API-key configuration for OpenAI."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import janito.auth_config as ac
import janito.cli.handlers.auth as auth_handler
import janito.config_dir as config_dir_mod
import janito.config_store as config_store


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config_dir_mod, "_config_dir", tmp_path / ".janito")
    monkeypatch.setattr(config_dir_mod, "_local_mode", False)


def _args(provider="openai"):
    return SimpleNamespace(provider=provider, set_api_key="sk-new")


@pytest.mark.parametrize("existing_key", [None, "sk-old"])
@pytest.mark.parametrize("record", [{}, {"access_token": "oauth-token", "expires_at": 0}])
@pytest.mark.parametrize("use_default", [False, True])
def test_oauth_blocks_api_key_without_side_effects(
    isolated_config, monkeypatch, capsys, existing_key, record, use_default
):
    if existing_key is not None:
        assert ac.set_api_key("openai", existing_key)
    assert ac.set_chatgpt_oauth(record)
    if use_default:
        config_store.set_config_value("provider", "openai")
    before = ac.get_auth_file_path().read_bytes()
    prompt = Mock(side_effect=AssertionError("OAuth must be checked before prompting"))
    write = Mock(side_effect=AssertionError("OAuth must prevent writing an API key"))
    monkeypatch.setattr(auth_handler, "_confirm_overwrite", prompt)
    monkeypatch.setattr(auth_handler, "set_api_key", write)

    assert auth_handler.handle_set_api_key(_args(None if use_default else "openai")) == 1

    prompt.assert_not_called()
    write.assert_not_called()
    assert ac.get_auth_file_path().read_bytes() == before
    assert ac.get_api_key("openai") == existing_key
    assert ac.get_chatgpt_oauth() == record
    assert "error" in capsys.readouterr().err.lower()


def test_openai_oauth_does_not_block_other_provider(isolated_config):
    record = {"access_token": "oauth-token"}
    assert ac.set_chatgpt_oauth(record)

    assert auth_handler.handle_set_api_key(_args("alibaba")) == 0

    assert ac.get_api_key("alibaba") == "sk-new"
    assert ac.get_api_key("openai") is None
    assert ac.get_chatgpt_oauth() == record


def test_api_key_allowed_after_oauth_removed(isolated_config):
    assert ac.set_chatgpt_oauth({"access_token": "oauth-token"})
    assert ac.delete_chatgpt_oauth()

    assert auth_handler.handle_set_api_key(_args()) == 0

    assert ac.get_api_key("openai") == "sk-new"
    assert ac.get_chatgpt_oauth() is None
