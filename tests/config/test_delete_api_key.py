"""API-key deletion targets one config file without altering other credentials."""

import json
from types import SimpleNamespace

import pytest

import janito.auth_config as ac
import janito.config_dir as config_dir
from janito.cli.handlers.auth import handle_delete_api_key
from janito.cli.parser import create_parser
from janito.config_store import get_config_value, set_config_value


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config_dir, "_config_dir", tmp_path / "base")
    monkeypatch.setattr(config_dir, "_local_mode", False)
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("use_default", [False, True])
def test_delete_preserves_other_credentials(use_default, capsys):
    assert ac.set_api_key("openai", "test-key")
    assert ac.set_api_key("alibaba", "other-key")
    assert ac.set_chatgpt_oauth({"access_token": "test-token"})
    set_config_value("provider", "openai")

    args = SimpleNamespace(provider=None if use_default else "openai")
    assert handle_delete_api_key(args) == 0

    assert ac.get_api_key("openai") is None
    assert ac.get_api_key("alibaba") == "other-key"
    assert ac.get_chatgpt_oauth() == {"access_token": "test-token"}
    assert get_config_value("provider") == "openai"
    assert capsys.readouterr().out.strip()


@pytest.mark.parametrize("provider", [None, "openai"])
def test_missing_provider_or_key_does_not_write(provider, capsys):
    assert handle_delete_api_key(SimpleNamespace(provider=provider)) == 1
    assert not ac.get_auth_file_path().exists()
    assert "error" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("local_key", [False, True])
def test_local_deletion_preserves_base_and_does_not_copy_fallbacks(local_key):
    assert ac.set_api_key("openai", "base-key")
    assert ac.set_api_key("alibaba", "base-other")
    base_path = ac.get_auth_file_path()
    before = base_path.read_bytes()
    config_dir.set_local_config_mode(True)
    local_path = ac.get_auth_file_path()
    local_path.parent.mkdir(parents=True)
    local_path.write_text(
        json.dumps({"openai": "local-key"} if local_key else {}), encoding="utf-8"
    )

    assert handle_delete_api_key(SimpleNamespace(provider="openai")) == (0 if local_key else 1)

    assert base_path.read_bytes() == before
    assert json.loads(local_path.read_text(encoding="utf-8")) == {}
    assert ac.get_api_key("openai") == "base-key"


@pytest.mark.parametrize("content", ['{"alibaba": "other-key"}', '{"openai": {}}', 'invalid json'])
def test_missing_or_invalid_key_leaves_file_unchanged(content):
    path = ac.get_auth_file_path()
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    before = path.read_bytes()
    assert handle_delete_api_key(SimpleNamespace(provider="openai")) == 1
    assert path.read_bytes() == before


def test_save_failure_is_reported(monkeypatch):
    assert ac.set_api_key("openai", "test-key")
    monkeypatch.setattr(ac._store, "save", lambda config: False)
    assert handle_delete_api_key(SimpleNamespace(provider="openai")) == 1
    assert ac.get_api_key("openai") == "test-key"


def test_parser_and_dispatch():
    from janito.__main__ import _dispatch_flag_command

    assert ac.set_api_key("openai", "test-key")
    args = create_parser().parse_args(["--delete-api-key", "--provider", "openai"])
    assert args.delete_api_key is True
    assert _dispatch_flag_command(args) == 0
    assert ac.get_api_key("openai") is None


def test_set_and_delete_are_mutually_exclusive():
    with pytest.raises(SystemExit) as exc:
        create_parser().parse_args(["--delete-api-key", "--set-api-key", "test-key"])
    assert exc.value.code == 2


@pytest.mark.parametrize("local", [False, True])
def test_cli_config_location_override(tmp_path, local):
    from janito.__main__ import _dispatch_flag_command, _setup_runtime

    selected = tmp_path / "selected"
    config_dir.set_config_dir(selected)
    assert ac.set_api_key("openai", "base-key")
    base_path = ac.get_auth_file_path()
    before = base_path.read_bytes()
    if local:
        config_dir.set_local_config_mode(True)
        path = ac.get_auth_file_path()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"openai": "local-key"}), encoding="utf-8")
    argv = ["--delete-api-key", "--provider", "OpenAI", "--config-dir", str(selected)]
    if local:
        argv.append("--local")
    args = create_parser().parse_args(argv)
    assert _setup_runtime(args) is None
    assert args.provider == "openai"
    assert _dispatch_flag_command(args) == 0
    assert "openai" not in json.loads(ac.get_auth_file_path().read_text(encoding="utf-8"))
    if local:
        assert base_path.read_bytes() == before
        assert ac.get_api_key("openai") == "base-key"
