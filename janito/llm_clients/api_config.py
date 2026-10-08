"""Resolved, immutable per-session API configuration (issue #70).

Everything a turn needs that can be resolved *before* the call starts.
Built once per session (or per provider/model switch) by
``build_api_config``; never mutated afterwards.  The turn pipeline and the
five ``run_turn`` entry points consume it instead of re-reading the
config store / auth store / provider registry at call time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class APIConfig:
    """Resolved, immutable per-session API configuration.

    Frozen -- the whole point is that the pipeline can't mutate session
    config; per-call variance is handled by per-call args (``verbose`` and
    the conversation-context kwargs) or by rebuilding the config (cheap, and
    exactly what happens on a provider/model or ``/thinking`` switch).

    Attributes:
        provider: The effective provider name.
        api_type: The canonical API type (``"Completions"``, ``"Responses"``,
            ``"Anthropic"``, ``"DashScope"``, ``"Gemini"``).
        model: The effective model name.
        base_url: The resolved API base URL (``None`` = the standard OpenAI
            endpoint).
        api_key: The resolved Bearer credential: the API key from the auth
            store, or the ChatGPT OAuth access token when ``auth_type`` is
            ``"chatgpt_oauth"`` (issue #154).  The origin is explicit in
            ``auth_type`` so downstream code never guesses billing mode.
        auth_type: How the Bearer credential was resolved: ``"api_key"`` or
            ``"chatgpt_oauth"`` (openai only).
        force_stateless: Runtime requirement forcing effective stateless
            Responses behavior regardless of the configured
            ``stateless_mode`` (True for ChatGPT OAuth sessions, where the
            documented ``store:false`` requirement applies).  Never written
            back to persistent config.
        max_output_tokens: Resolved max output tokens (never ``None``: falls
            back to the built-in default, then to 100_000).
        max_input_tokens: Resolved max input tokens (``None`` = unknown
            context window; the usage display omits the total).
        reasoning_effort: Resolved reasoning depth (``None`` = the API's own
            default applies).
        thinking: The resolved thinking mode for the session: the explicit
            ``--thinking`` / ``/thinking`` flag when set, otherwise the
            provider's built-in default (``True``, a pass-through dict such
            as MiniMax-M3's ``{'type': 'adaptive'}``, or ``False``).  A falsy
            value means the flag was not forced on; the resolved value is
            what gets sent to the API.
        preserve_thinking: The resolved ``preserve_thinking`` for the
            provider/model -- the model's built-in default from the provider
            config (e.g. ``True`` for Alibaba/Qwen's hybrid-thinking models,
            whose API appends previous ``reasoning_content`` to the next
            input), or ``None`` when the model declares none (the caller
            sends no flag and the API's own default applies).
        use_mcp: Whether to load and use MCP tools.
    """

    # --- Identity / endpoint (from resolve_runtime_config) ---
    provider: str
    api_type: str  # "Completions" | "Responses" | "Anthropic" | "DashScope" | "Gemini"
    model: str
    base_url: str | None  # None = standard OpenAI endpoint
    api_key: str
    auth_type: str = "api_key"  # "api_key" | "chatgpt_oauth"
    force_stateless: bool = False

    # --- Resolved model settings (config override -> built-in default) ---
    max_output_tokens: int = 100_000  # never None: falls back to 100_000
    max_input_tokens: int | None = None
    reasoning_effort: str | None = None  # None = API's own default applies
    thinking: bool | dict | None = None  # resolved: --thinking / /thinking flag or provider built-in default
    preserve_thinking: Any = None  # config value; may be None
    use_mcp: bool = True


def build_api_config(
    *,
    api_type: str,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    reasoning_effort: str | None = None,
    thinking: bool | None = None,
    use_mcp: bool = True,
) -> APIConfig:
    """Resolve everything a turn needs into an immutable APIConfig.

    The ONLY place that touches the config store / auth store / provider
    registry (issue #70): the turn pipeline becomes a pure function of its
    inputs.  ``thinking`` is resolved here too: the explicit
    ``--thinking`` / ``/thinking`` flag wins, otherwise the provider's
    static built-in default applies (a ``True`` flag or a pass-through dict
    such as MiniMax-M3's ``{'type': 'adaptive'}``).  The shell's ``/thinking``
    toggle flips it mid-session by rebuilding the config through the
    ``turn_factory`` -- the same cheap rebuild that a provider/model
    switch performs.

    ChatGPT OAuth (issue #154): when the resolved ``auth_type`` is
    ``"chatgpt_oauth"``, ``force_stateless`` is set so the Responses client
    sends ``store:false`` with full input-item replay, and
    ``OAuth + Completions`` is rejected with an actionable error (token
    sharing is Responses-only) instead of silently falling back to API-key
    billing.

    Args:
        api_type: The canonical API type (``"Completions"``, ``"Responses"``,
            ``"Anthropic"``, ``"DashScope"``, ``"Gemini"``).  Also selects the
            built-in default endpoint for providers that declare
            ``endpoint_by_api_type`` (native-SDK types resolve their native
            base URL, e.g. the Anthropic / DashScope / Gemini SDK endpoints).
        cli_provider: Provider passed via ``--provider`` (may be ``None``).
        cli_model: Model passed via ``--model`` (may be ``None``).
        reasoning_effort: Reasoning depth passed via ``--effort``
            (may be ``None``).
        thinking: The ``--thinking`` CLI flag / shell ``/thinking`` override
            (may be ``None``).  ``True`` forces thinking on; ``False`` (or
            ``None``) leaves it to the provider's built-in default.
        use_mcp: Whether to load and use MCP tools (default ``True``).

    Returns:
        A fully resolved, frozen :class:`APIConfig`.

    Raises:
        ValueError: If the API key, model or endpoint cannot be resolved
            (propagated from ``resolve_runtime_config``), or if ChatGPT OAuth
            is combined with an unsupported API type.
    """
    # Lazy imports avoid a cycle: completions_api imports APIConfig.
    from janito.config_loaders import (
        load_effort,
        load_max_input_tokens,
        load_max_output_tokens,
    )
    from janito.general_config import get_active_provider
    from janito.providers.registry import get_provider
    from janito.runtime_config import resolve_runtime_full

    provider = cli_provider or get_active_provider()
    base_url, api_key, model, credential = resolve_runtime_full(cli_model, cli_provider, cli_api_type=api_type)
    auth_type = credential.auth_type
    force_stateless = bool(credential.requires_stateless)

    if auth_type == "chatgpt_oauth" and api_type != "Responses":
        raise ValueError(
            f"ChatGPT-plan authentication requires the Responses API, not '{api_type}'. "
            f"Use --api-type Responses (or the provider's Responses default) "
            f"with ChatGPT OAuth, or use API-key authentication for '{api_type}'."
        )

    found = get_provider(provider)
    found_max_output = found.model_config(model).get("max_output_tokens") if found is not None else None
    found_max_input = found.model_config(model).get("max_input_tokens") if found is not None else None
    found_reasoning = found.model_config(model).get("default_reasoning_effort") if found is not None else None
    found_thinking = found.model_config(model).get("thinking", False) if found is not None else False
    found_preserve_thinking = found.model_config(model).get("preserve_thinking") if found is not None else None

    max_output_tokens = load_max_output_tokens(provider, model) or found_max_output or 100_000
    max_input_tokens = load_max_input_tokens(provider, model) or found_max_input
    reasoning_effort = reasoning_effort or load_effort(provider, model) or found_reasoning
    thinking = thinking or found_thinking

    return APIConfig(
        provider=provider,
        api_type=api_type,
        model=model,
        base_url=base_url,
        api_key=api_key,
        auth_type=auth_type,
        force_stateless=force_stateless,
        max_output_tokens=max_output_tokens,
        max_input_tokens=max_input_tokens,
        reasoning_effort=reasoning_effort,
        thinking=thinking,
        preserve_thinking=found_preserve_thinking,
        use_mcp=use_mcp,
    )


__all__ = [
    "APIConfig",
    "build_api_config",
]
