import time
from argparse import Namespace

from janito.cli.handlers import login
from janito.openai_oauth import ChatGPTAuthError


def _fresh_record(**overrides):
    record = {
        "access_token": "access",
        "refresh_token": "refresh",
        "email": "user@example.com",
        "expires_at": time.time() + 3600,
    }
    record.update(overrides)
    return record


def _expired_record(**overrides):
    return _fresh_record(expires_at=time.time() - 3600, **overrides)


def test_login_skips_when_session_fresh(monkeypatch):
    monkeypatch.setattr(login, "_resolve_login_provider", lambda args: "openai")
    monkeypatch.setattr("janito.auth_config.get_chatgpt_oauth", lambda: _fresh_record())
    monkeypatch.setattr(
        "janito.openai_oauth.do_login",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("interactive login must not run")),
    )

    assert login.handle_login(Namespace(force=False)) == 0


def test_login_starts_flow_without_existing_oauth_tokens(monkeypatch, capsys):
    record = _fresh_record()
    monkeypatch.setattr(login, "_resolve_login_provider", lambda args: "openai")
    monkeypatch.setattr("janito.auth_config.get_chatgpt_oauth", lambda: None)
    monkeypatch.setattr("janito.openai_oauth.do_login", lambda **kwargs: record)
    monkeypatch.setattr("janito.openai_oauth.mask_record", lambda value: value)

    assert login.handle_login(Namespace(force=False)) == 0
    assert "sign-in complete" in capsys.readouterr().out


def test_login_reauthenticates_when_refresh_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(login, "_resolve_login_provider", lambda args: "openai")
    monkeypatch.setattr("janito.auth_config.get_chatgpt_oauth", lambda: _expired_record())
    monkeypatch.setattr(
        "janito.openai_oauth.ensure_fresh_record",
        lambda record: (_ for _ in ()).throw(ChatGPTAuthError("expired", kind="expired")),
    )
    monkeypatch.setattr(
        "janito.openai_oauth.do_login", lambda **kwargs: calls.append(kwargs) or _fresh_record()
    )
    monkeypatch.setattr("janito.openai_oauth.mask_record", lambda value: value)

    assert login.handle_login(Namespace(force=False)) == 0
    assert len(calls) == 1


def test_login_refreshes_transparently_when_possible(monkeypatch):
    fresh = _fresh_record()
    monkeypatch.setattr(login, "_resolve_login_provider", lambda args: "openai")
    monkeypatch.setattr("janito.auth_config.get_chatgpt_oauth", lambda: _expired_record())
    monkeypatch.setattr("janito.openai_oauth.ensure_fresh_record", lambda record: fresh)
    monkeypatch.setattr(
        "janito.openai_oauth.do_login",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("interactive login must not run")),
    )

    assert login.handle_login(Namespace(force=False)) == 0


def test_login_force_reauthenticates_when_fresh(monkeypatch):
    calls = []
    monkeypatch.setattr(login, "_resolve_login_provider", lambda args: "openai")
    monkeypatch.setattr("janito.auth_config.get_chatgpt_oauth", lambda: _fresh_record())
    monkeypatch.setattr(
        "janito.openai_oauth.do_login", lambda **kwargs: calls.append(kwargs) or _fresh_record()
    )
    monkeypatch.setattr("janito.openai_oauth.mask_record", lambda value: value)

    assert login.handle_login(Namespace(force=True)) == 0
    assert len(calls) == 1
