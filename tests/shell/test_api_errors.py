"""Recoverable provider failures must not terminate the interactive session."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from janito.shell import InteractiveShell
from janito.tooling.turn_privileges import get_turn_privileges


@pytest.fixture(autouse=True)
def headless_terminal():
    """Shell recovery tests need no real terminal (including on Windows)."""
    with create_app_session(input=DummyInput(), output=DummyOutput()):
        yield


def _api_error(error_class):
    request = httpx.Request("POST", "https://example.invalid/responses")
    if issubclass(error_class, APIConnectionError):
        return error_class(request=request)
    status = {
        AuthenticationError: 401,
        BadRequestError: 400,
        InternalServerError: 500,
        RateLimitError: 429,
    }[error_class]
    return error_class("provider failure", response=httpx.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    "error_class",
    [APIConnectionError, APITimeoutError, AuthenticationError, BadRequestError, InternalServerError, RateLimitError],
)
def test_api_error_returns_to_prompt_and_next_turn_succeeds(monkeypatch, capsys, error_class):
    shell = InteractiveShell(model="test-model", no_history=True)
    shell.initialize_history(system_prompt="sys")
    original_history = list(shell.messages_history)
    prompts = iter(["failed", "retry", None])
    monkeypatch.setattr(shell, "_get_user_input", lambda: next(prompts))
    save = Mock()
    monkeypatch.setattr(shell, "_save_snapshot", save)
    received = []
    privileges_before = get_turn_privileges()

    def turn(user_input, **kwargs):
        received.append(user_input)
        history = kwargs["previous_messages"]
        assert history == original_history
        assert shell.history_turns == [len(original_history)]
        history.append({"role": "user", "content": user_input})
        if user_input == "failed":
            history.append({"role": "assistant", "content": "partial output"})
            raise _api_error(error_class)
        history.append({"role": "assistant", "content": "done"})
        return "done"

    shell.run(turn, no_tools=True)

    assert received == ["failed", "retry"]
    assert len(shell.history_turns) == 1
    assert shell.messages_history == original_history + [
        {"role": "user", "content": "retry"},
        {"role": "assistant", "content": "done"},
    ]
    assert get_turn_privileges() == privileges_before
    assert save.call_count >= 2
    out = capsys.readouterr().out
    assert "API error" in out
    assert "Traceback" not in out


def test_api_error_restores_responses_items_and_preserves_completed_chain(capsys):
    shell = InteractiveShell(model="test-model", no_history=True)
    shell.initialize_history(system_prompt="sys")
    prior_items = [{"role": "user", "content": "previous prompt"}]
    shell.conversation_items = list(prior_items)
    shell.previous_response_id = "completed-response"
    shell.response_chain = ["completed-response"]
    shell.mirrored_history = [{"role": "assistant", "content": "previous answer"}]
    prior_mirror = list(shell.mirrored_history)
    shell.history_turns = [1]

    def turn(user_input, **kwargs):
        kwargs["previous_items"].extend([
            {"role": "user", "content": user_input},
            {"type": "function_call", "call_id": "unfinished"},
        ])
        raise _api_error(APIConnectionError)

    shell.turn_func = turn
    shell.no_tools = True
    shell._run_turn("failed")

    assert shell.conversation_items == prior_items
    assert shell.previous_response_id == "completed-response"
    assert shell.response_chain == ["completed-response"]
    assert shell.mirrored_history == prior_mirror
    assert shell.history_turns == [1]
    assert capsys.readouterr().out.strip()


@pytest.mark.parametrize("module_name", ["anthropic", "google.genai.errors"])
def test_optional_sdk_loaded_during_turn_is_caught(monkeypatch, module_name):
    shell = InteractiveShell(model="test-model", no_history=True)
    shell.initialize_history(system_prompt="sys")

    class ProviderAPIError(Exception):
        pass

    def turn(*args, **kwargs):
        monkeypatch.setitem(sys.modules, module_name, SimpleNamespace(APIError=ProviderAPIError))
        raise ProviderAPIError("provider failure")

    shell.turn_func = turn
    shell.no_tools = True
    shell._run_turn("failed")
    assert shell.history_turns == []


def test_unexpected_programming_error_still_propagates():
    shell = InteractiveShell(model="test-model", no_history=True)
    shell.initialize_history(system_prompt="sys")
    shell.turn_func = Mock(side_effect=ValueError("programming bug"))
    shell.no_tools = True
    privileges_before = get_turn_privileges()

    with pytest.raises(ValueError):
        shell._run_turn("failed")
    assert get_turn_privileges() == privileges_before
