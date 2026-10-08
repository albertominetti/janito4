from argparse import Namespace

from janito.cli.handlers import login


def test_login_does_nothing_when_oauth_tokens_are_present(monkeypatch, capsys):
    record = {"access_token": "access", "refresh_token": "refresh", "email": "user@example.com"}
    monkeypatch.setattr(login, "_resolve_login_provider", lambda args: "openai")
    monkeypatch.setattr("janito.auth_config.get_chatgpt_oauth", lambda: record)
    monkeypatch.setattr(
        "janito.openai_oauth.do_login",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("interactive login must not run")),
    )

    assert login.handle_login(Namespace()) == 0
    output = capsys.readouterr().out
    assert "already signed in" in output.lower()
    assert "user@example.com" in output


def test_login_starts_flow_without_existing_oauth_tokens(monkeypatch, capsys):
    record = {"access_token": "access", "refresh_token": "refresh", "email": "user@example.com"}
    monkeypatch.setattr(login, "_resolve_login_provider", lambda args: "openai")
    monkeypatch.setattr("janito.auth_config.get_chatgpt_oauth", lambda: None)
    monkeypatch.setattr("janito.openai_oauth.do_login", lambda **kwargs: record)
    monkeypatch.setattr("janito.openai_oauth.mask_record", lambda value: value)

    assert login.handle_login(Namespace()) == 0
    assert "sign-in complete" in capsys.readouterr().out
