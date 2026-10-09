"""Shared resolved-model-settings policy (issue #31).

Single owner for model-setting resolution precedence: config override
first, then the provider's built-in default. Both entry layers (CLI
``build_api_config`` and the web loop) invoke this service; web-specific
session overrides stay with the caller.

Lives at the package root so both ``llm_clients`` and ``web`` may import
it (both allow ``root`` targets) without breaking the enforced import
matrix. Only root same-domain stores (``config_loaders``) and
``providers`` are read here.
"""

from __future__ import annotations

FALLBACK_MAX_OUTPUT_TOKENS = 100_000


def resolve_model_settings(
    provider: str | None,
    model: str | None,
    *,
    effort_override: str | None = None,
    fallback_max_output_tokens: int | None = FALLBACK_MAX_OUTPUT_TOKENS,
) -> tuple[int | None, object, str | None]:
    """Resolve ``(max_output_tokens, preserve_thinking, reasoning_effort)``.

    Precedence for each setting is the model-scoped config value first,
    then the model's built-in default from the provider config, then the
    fallback (``100_000`` output tokens when ``fallback_max_output_tokens``
    is left at its default; ``None`` when the caller passes ``None`` to
    mean "leave it to the API's own default").

    Args:
        provider: Effective provider name (may be ``None``).
        model: Effective model name (may be ``None``).
        effort_override: Explicit ``--effort`` value winning over config.
        fallback_max_output_tokens: Final fallback for max output tokens.

    Returns:
        ``(max_output_tokens, preserve_thinking, reasoning_effort)``.
    """
    from janito.config_loaders import load_effort, load_max_output_tokens
    from janito.providers.registry import get_provider

    found = get_provider(provider) if provider else None
    if found is not None and model:
        builtin_max = found.model_config(model).get("max_output_tokens")
        builtin_preserve = found.model_config(model).get("preserve_thinking")
        builtin_effort = found.model_config(model).get("default_reasoning_effort")
    else:
        builtin_max = None
        builtin_preserve = None
        builtin_effort = None

    max_output_tokens = load_max_output_tokens(provider, model) if provider else None
    if max_output_tokens is None:
        max_output_tokens = builtin_max if builtin_max is not None else fallback_max_output_tokens

    preserve_thinking = builtin_preserve

    reasoning_effort = effort_override
    if reasoning_effort is None:
        reasoning_effort = load_effort(provider, model) if provider else None
    if reasoning_effort is None:
        reasoning_effort = builtin_effort

    return max_output_tokens, preserve_thinking, reasoning_effort


__all__ = [
    "FALLBACK_MAX_OUTPUT_TOKENS",
    "resolve_model_settings",
]
