"""ChatGPT OAuth Responses regression: no system messages (issue #154).

The Codex backend rejects ``{"type": "message", "role": "system"}`` input
items with ``400 {'detail': 'System messages are not allowed'}``. OAuth
sessions (stateless) must carry the system prompt via top-level
``instructions`` instead, with ``store:false`` and no ``temperature``.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from unittest import mock

from conftest import make_config

import janito.config_dir as config_dir_mod
import janito.tooling.used_files as used_files
import pytest

from janito.llm_clients.openai import conversations_api as api
from janito.llm_clients.openai.responses_state import (
    _build_call_kwargs,
    _init_conversation_state,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_dir_mod, "_config_dir", tmp_path)
    used_files.reset_used_files()
    yield
    used_files.reset_used_files()


def _oauth_config(**kw):
    kw.setdefault("api_type", "Responses")
    kw.setdefault("provider", "openai")
    kw.setdefault("model", "gpt-6-luna")
    return make_config(auth_type="chatgpt_oauth", force_stateless=True, **kw)


def test_init_oauth_stateless_skips_system_item():
    stateless, _, items, _, _ = _init_conversation_state(
        "openai", "gpt-6-luna", None, None, "Be helpful", "Hello",
        force_stateless=True, is_oauth=True,
    )
    assert stateless is True
    assert items == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Hello"}]}
    ]


def test_init_non_oauth_stateless_keeps_system_item():
    _, _, items, _, _ = _init_conversation_state(
        "openai", "gpt-6-luna", None, None, "Be helpful", "Hello",
        force_stateless=True, is_oauth=False,
    )
    assert items[0]["role"] == "system"


def test_build_kwargs_oauth_sends_instructions_no_system_no_temperature():
    _, _, items, _, _ = _init_conversation_state(
        "openai", "gpt-6-luna", None, None, "Be helpful", "Hello",
        force_stateless=True, is_oauth=True,
    )
    kwargs = _build_call_kwargs(
        "gpt-6-luna", items, 100_000, None, None, False, None, True,
        "Be helpful", None, provider="openai", is_oauth=True,
    )
    assert kwargs["instructions"] == "Be helpful"
    assert kwargs["store"] is False
    assert kwargs["stream"] is True
    assert "temperature" not in kwargs
    assert all(
        not (isinstance(i, dict) and i.get("role") in ("system", "developer"))
        for i in kwargs["input"]
    )


def test_build_kwargs_oauth_sanitizes_carried_system_items():
    carried = [
        {"type": "message", "role": "system", "content": [{"type": "input_text", "text": "Old sys"}]},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Hi"}]},
    ]
    kwargs = _build_call_kwargs(
        "gpt-6-luna", carried, 100_000, None, None, False, None, True,
        "Be helpful", None, provider="openai", is_oauth=True,
    )
    assert "Old sys" in kwargs["instructions"]
    assert "Be helpful" in kwargs["instructions"]
    assert all(i.get("role") != "system" for i in kwargs["input"])


class _Event:
    def __init__(self, type, **attrs):
        self.type = type
        for k, v in attrs.items():
            setattr(self, k, v)


class _Response:
    def __init__(self, id, usage=None):
        self.id = id
        self.usage = usage


def _stream(events):
    yield from events


def test_run_turn_oauth_end_to_end(monkeypatch):
    seen = []

    def create(**kwargs):
        seen.append(kwargs)
        assert kwargs["instructions"] == "Be helpful"
        assert "temperature" not in kwargs
        assert all(i.get("role") != "system" for i in kwargs["input"])
        return _stream(
            [
                _Event("response.created", response=_Response("r1")),
                _Event("response.output_text.delta", delta="ok"),
                _Event("response.completed", response=_Response("r1")),
            ]
        )

    client_inst = mock.Mock()
    client_inst.responses.create.side_effect = create
    monkeypatch.setattr(api, "OpenAI", mock.Mock(return_value=client_inst))
    monkeypatch.setattr(
        "janito.llm_clients.openai.responses_helpers.get_session_tool_schemas",
        lambda: [],
    )
    monkeypatch.setattr(api, "ToolExecutor", mock.Mock(return_value=mock.Mock()))
    # get_provider only used for builtin tools / reasoning; return empty.
    monkeypatch.setattr(
        "janito.llm_clients.openai.conversations_api.get_provider",
        lambda p: mock.Mock(
            tools=lambda model=None, api_type=None: None,
            model_config=lambda model=None: {},
        ),
    )

    result = api.run_turn(_oauth_config(), "Hello", instructions="Be helpful", tools=[])
    assert result.content == "ok"
    assert len(seen) == 1
