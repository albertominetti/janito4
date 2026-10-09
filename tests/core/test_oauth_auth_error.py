"""OAuth-aware auth explainer: 401s on a ChatGPT session guide re-login."""

import sys
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rich.console import Console

from janito.ui.observer import RichTurnObserver


def _make():
    buf = StringIO()
    return RichTurnObserver(console=Console(file=buf, width=120)), buf


def test_oauth_401_guides_relogin():
    obs, buf = _make()
    e = Exception("token_expired: Provided authentication token is expired.")
    e.status_code = 401
    obs.on_error(
        e,
        provider="openai",
        api_key="oauth-token",
        base_url=None,
        model="gpt-6.1-sol",
        error_kind="auth",
        auth_type="chatgpt_oauth",
    )
    rendered = buf.getvalue()
    assert "ChatGPT" in rendered
    assert "--logout" in rendered
    assert "API Key" not in rendered


def test_api_key_401_keeps_key_guidance():
    obs, buf = _make()
    e = Exception("Incorrect API key provided")
    e.status_code = 401
    obs.on_error(
        e,
        provider="openai",
        api_key="sk-test",  # pragma: allowlist secret
        base_url=None,
        model="gpt-6.1-sol",
        error_kind="auth",
        auth_type="api_key",
    )
    assert "API key" in buf.getvalue()
