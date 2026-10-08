"""Runtime configuration resolution (provider, api key, endpoint, model).

Hosts :func:`resolve_runtime_config`, the single place that resolves the
runtime ``(base_url, api_key, model)`` triple from the auth store
(``~/.janito/auth.json``) and the config file (``~/.janito/config.json``)
without relying on ``OPENAI_*`` environment variables.

This is a **config-layer** module: it reads the auth/config stores and the
provider registry, and it must never be imported by ``janito.llm_clients``
-- the clients consume the already-resolved values through the frozen
:class:`~janito.llm_clients.api_config.APIConfig` (built once per session by
``build_api_config``, which delegates its config/auth-store reads to this
module).  Other early-validation callers (the CLI setup check, the chat
model display, the web agent loop) resolve the triple here too.
"""

import logging
from dataclasses import dataclass

from .auth_config import get_api_key
from .config_loaders import load_endpoint_from_config, load_model_from_config
from .general_config import load_provider_from_config
from .providers.registry import get_provider
from .providers.validation import is_custom_provider

# Configure logger for this module
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedCredential:
    """Explicit authentication selection for a session (issue #154).

    ``auth_type`` is ``"api_key"`` or ``"chatgpt_oauth"``; ``credential`` is
    the Bearer value to send (API key or OAuth access token).
    ``requires_stateless`` is True for ChatGPT OAuth sessions, where the
    documented Responses requirements (``store:false``) force effective
    stateless behavior regardless of the configured ``stateless_mode``.
    """

    auth_type: str
    credential: str
    requires_stateless: bool = False


def resolve_credential(provider: str) -> ResolvedCredential:
    """Resolve the authentication for ``provider`` deterministically.

    - An API key in the auth store always wins (backward compatible).
    - Otherwise, for ``openai`` only, a stored ChatGPT OAuth record is used
      (refreshed transparently when expired).
    - Never silently switches billing modes: OAuth refresh/expiry failures
      raise instead of falling back to an API key, and vice versa.

    Raises:
        ValueError: If neither authentication mode can be resolved.
    """
    api_key = get_api_key(provider)
    if api_key:
        return ResolvedCredential(auth_type="api_key", credential=api_key)

    if provider == "openai":
        from .auth_config import get_chatgpt_oauth
        from .openai_oauth import ChatGPTAuthError, ensure_fresh_record

        record = get_chatgpt_oauth()
        if record is not None:
            try:
                fresh = ensure_fresh_record(record)
            except ChatGPTAuthError as e:
                raise ValueError(str(e)) from e
            token = fresh.get("access_token")
            if not token:
                raise ValueError(
                    "ChatGPT credentials are incomplete. Re-run: janito --login"
                )
            return ResolvedCredential(
                auth_type="chatgpt_oauth",
                credential=token,
                requires_stateless=True,
            )

    raise ValueError(
        f"No API key configured for provider '{provider}'. "
        f"Set one with: janito --set-api-key <key> --provider {provider}"
    )


def oauth_session_active(provider: str) -> bool:
    """Whether an OAuth session would be used, without network refresh (issue #154).

    Display-only helper for banners/status: ``True`` when no API key is set
    and a ChatGPT OAuth record exists for ``openai``.  Never refreshes or
    raises; request-time code uses :func:`resolve_credential` instead.
    """
    if get_api_key(provider):
        return False
    if provider != "openai":
        return False
    from .auth_config import get_chatgpt_oauth

    return get_chatgpt_oauth() is not None


def resolve_runtime_full(
    cli_model: str | None = None,
    cli_provider: str | None = None,
    cli_api_type: str | None = None,
) -> tuple[str | None, str, str, ResolvedCredential]:
    """Resolve ``(base_url, api_key, model, credential)`` with one credential read.

    Single-resolution entry point (issue #154): the credential is resolved
    exactly once so an expiring OAuth record triggers at most one refresh.
    :func:`resolve_runtime_config` delegates here for backward compatibility.
    """
    # Provider: --provider CLI arg, then config.json.  The default provider
    # is stored under the ``provider`` key in config.json -- never in
    # auth.json.  If none of these is set, report that no provider is
    # configured rather than silently assuming "openai".
    provider = cli_provider or load_provider_from_config()
    if not provider:
        logger.error("No provider configured")
        raise ValueError(
            "No provider is configured. "
            "Set one with: janito --set provider=<name> (e.g. janito --set provider=alibaba) "
            "or pass --provider <name>."
        )
    logger.debug(f"Resolving runtime config for provider: {provider}")

    # Auth: API key wins; openai falls back to ChatGPT OAuth (issue #154).
    # Failures raise instead of silently switching billing modes.
    try:
        resolved = resolve_credential(provider)
    except ValueError:
        logger.error(f"No API key configured for provider '{provider}'")
        raise
    api_key = resolved.credential

    # Model: --model, then the provider's configured model, and finally the
    # provider's built-in default model (from the provider config).  A
    # provider whose built-in default is the "custom" placeholder (e.g.
    # "openrouter") has no usable default: the placeholder "custom" model
    # entry only carries built-in defaults (the default API type), so the
    # user must supply the model explicitly (--model or <provider>.model in
    # config.json).  When it cannot be resolved, report it here instead of
    # silently sending the placeholder to the API.
    model = cli_model or load_model_from_config(provider)
    if not model:
        found = get_provider(provider)
        if found is not None:
            model = found.default_model()
            # A provider whose built-in default is the "custom" placeholder
            # (e.g. "openrouter") has no usable default: the placeholder only
            # carries built-in defaults (the default API type), so the user
            # must supply the model explicitly.
            if model == "custom":
                model = None
    if not model:
        logger.error(f"No model configured for provider '{provider}'")
        raise ValueError(
            f"No model configured for provider '{provider}'. "
            f"Pass --model <name> or set it with: "
            f"janito --provider {provider} --set model=<name>"
        )

    # Base URL: configured endpoint for the provider, otherwise the provider's
    # built-in default resolved for the effective API type (None for standard
    # OpenAI). The effective API type comes from --api-type, then the
    # provider's configured api-type, then its built-in default.
    base_url = load_endpoint_from_config(provider)
    if not base_url:
        if is_custom_provider(provider):
            logger.warning(f"Custom provider '{provider}' has no endpoint configured")
            raise ValueError(
                f"Provider '{provider}' requires an endpoint. "
                f"Set it with: janito --provider {provider} --set endpoint=<url>"
            )
        from .general_config import resolve_api_type

        api_type = resolve_api_type(cli_api_type, provider)
        found = get_provider(provider)
        base_url = found.endpoint_for(api_type) if found is not None else None

    logger.debug(f"Runtime config resolved: base_url={base_url}, model={model}")
    return base_url, api_key, model, resolved


def resolve_runtime_config(
    cli_model: str | None = None,
    cli_provider: str | None = None,
    cli_api_type: str | None = None,
) -> tuple[str | None, str, str]:
    """Resolve ``(base_url, api_key, model)`` (delegates to :func:`resolve_runtime_full`).

    Backward-compatible 3-tuple wrapper: the Bearer ``api_key`` element is
    the resolved credential (API key or ChatGPT OAuth access token).
    """
    base_url, api_key, model, _ = resolve_runtime_full(cli_model, cli_provider, cli_api_type)
    return base_url, api_key, model


__all__ = [
    "ResolvedCredential",
    "oauth_session_active",
    "resolve_credential",
    "resolve_runtime_config",
    "resolve_runtime_full",
]
