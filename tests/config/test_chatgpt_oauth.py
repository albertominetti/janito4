"""Mocked tests for ChatGPT-plan OAuth (issue #154, no real credentials)."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

import janito.config_dir as config_dir_mod
from janito import openai_oauth as oauth


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch, tmp_path):
    base = tmp_path / "janito"
    monkeypatch.setattr(config_dir_mod, "_config_dir", base)
    yield
    config_dir_mod.set_local_config_mode(False)


def _seed_api_key(key="sk-test"):  # pragma: allowlist secret
    from janito.auth_config import set_api_key

    assert set_api_key("openai", key) is True


def _seed_oauth(**overrides):
    from janito.auth_config import set_chatgpt_oauth

    record = {
        "client_id": "oaiapp_test",
        "ext_agent_host_id": "urn:uuid:host",
        "email": "user@example.com",
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "id_token": "id-1",
        "expires_at": time.time() + 3600,
        "scopes": [oauth.REQUIRED_SCOPE, "openid"],
    }
    record.update(overrides)
    assert set_chatgpt_oauth(record) is True
    return record


def test_pkce_challenge_verifies():
    verifier = oauth.generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    challenge = oauth.generate_code_challenge(verifier)
    assert challenge
    assert oauth.generate_code_challenge(verifier) == challenge
    assert oauth.generate_state()
    assert oauth.generate_state() != oauth.generate_state()


def test_auth_url_carries_required_params():
    url = oauth.build_authorization_url(
        redirect_uri="http://127.0.0.1:9/auth/callback",
        state="st",
        nonce="nn",
        code_challenge="cc",
        host_id="urn:uuid:h",
    )
    assert "response_type=code" in url
    assert "code_challenge_method=S256" in url
    assert oauth.DYNAMIC_CLIENT_ID in url
    assert "chatgpt.tokens.use.direct" in url


def test_scope_helpers():
    assert oauth.has_required_scope(["openid", oauth.REQUIRED_SCOPE]) is True
    assert oauth.has_required_scope(["openid"]) is False
    assert oauth.granted_scopes("a b") == ["a", "b"]
    assert oauth.granted_scopes(None) == []


def test_exchange_requires_token_fields(monkeypatch):
    monkeypatch.setattr(oauth, "_post_token", lambda data: {"access_token": "x"})
    with pytest.raises(oauth.ChatGPTAuthError):
        oauth.exchange_code(code="c", code_verifier="v", redirect_uri="r", client_id="cid")


def test_build_record_and_expiry():
    payload = {
        "access_token": "a",
        "refresh_token": "r",
        "id_token": "i",
        "expires_in": 3600,
    }
    record = oauth.build_record(
        token_payload=payload, client_id="cid", host_id="h", scopes=["s"], id_claims={"sub": "s"}
    )
    assert record["client_id"] == "cid"
    assert oauth.is_expired(record) is False
    assert oauth.is_expired({**record, "expires_at": time.time() - 10}) is True
    assert oauth.mask_record(record).get("access_token") is None


def test_validate_id_token_rejects_garbage():
    with pytest.raises(oauth.ChatGPTAuthError):
        oauth.validate_id_token("not-a-jwt", client_id="cid")


def test_ensure_fresh_passthrough_when_valid():
    record = {"access_token": "a", "expires_at": time.time() + 3600}
    assert oauth.ensure_fresh_record(record) is record


def test_ensure_fresh_expired_without_refresh_raises():
    record = {"access_token": "a", "expires_at": time.time() - 10}
    with pytest.raises(oauth.ChatGPTAuthError):
        oauth.ensure_fresh_record(record)


def test_resolve_credential_api_key_wins_and_oauth_fallback():
    from janito.auth_config import get_api_key
    from janito.runtime_config import resolve_credential

    _seed_api_key()
    _seed_oauth()
    resolved = resolve_credential("openai")
    assert resolved.auth_type == "api_key"
    assert resolved.credential == get_api_key("openai")

    from janito.auth_config import delete_api_key

    assert delete_api_key("openai") is True
    resolved = resolve_credential("openai")
    assert resolved.auth_type == "chatgpt_oauth"
    assert resolved.requires_stateless is True


def test_resolve_credential_neither_raises():
    from janito.runtime_config import resolve_credential

    with pytest.raises(ValueError):
        resolve_credential("openai")


def test_build_api_config_rejects_oauth_completions_and_sets_stateless():
    from janito.auth_config import delete_api_key
    from janito.llm_clients.api_config import build_api_config

    _seed_oauth()
    delete_api_key("openai")
    with pytest.raises(ValueError):
        build_api_config(api_type="Completions", cli_provider="openai", cli_model="gpt-6-luna")
    config = build_api_config(api_type="Responses", cli_provider="openai", cli_model="gpt-6-luna")
    assert config.auth_type == "chatgpt_oauth"
    assert config.force_stateless is True


def test_stateless_force_override():
    from janito.llm_clients.openai.responses_state import _init_conversation_state, stateless_mode

    assert stateless_mode("openai", "gpt-6-luna", force=True) is True
    flag, response_id, _, _, _ = _init_conversation_state(
        "openai", "gpt-6-luna", "resp-1", None, None, "hi", force_stateless=True
    )
    assert flag is True
    assert response_id is None


def test_do_login_rejects_when_api_key_present():
    _seed_api_key()
    with pytest.raises(oauth.ChatGPTAuthError):
        oauth.do_login(provider="openai")


def test_do_login_rejects_non_openai():
    with pytest.raises(oauth.ChatGPTAuthError):
        oauth.do_login(provider="deepseek")


def test_do_logout_removes_oauth_keeps_api_key():
    from janito.auth_config import get_api_key, get_chatgpt_oauth

    _seed_api_key()
    _seed_oauth()
    assert oauth.do_logout(provider="openai") is True
    assert get_chatgpt_oauth() is None
    assert get_api_key("openai") == "sk-test"  # pragma: allowlist secret
    assert oauth.do_logout(provider="openai") is False
