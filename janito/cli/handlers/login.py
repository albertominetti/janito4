"""ChatGPT-plan login/logout handlers (issue #154)."""

import sys

from ...general_config import load_provider_from_config


def _resolve_login_provider(args) -> str | None:
    provider = getattr(args, "provider", None)
    if provider:
        return provider
    return load_provider_from_config()


def handle_login(args) -> int:
    """Handle ``janito --login``."""
    from ...auth_config import get_chatgpt_oauth
    from ...openai_oauth import (
        ChatGPTAuthError,
        do_login,
        ensure_fresh_record,
        is_expired,
        mask_record,
    )

    provider = _resolve_login_provider(args) or "openai"
    if provider != "openai":
        print(
            f"Error: ChatGPT-plan login is only supported for provider 'openai', not '{provider}'.",
            file=sys.stderr,
        )
        return 1
    force = bool(getattr(args, "force", False))
    existing = get_chatgpt_oauth()
    if existing and not force:
        if not is_expired(existing):
            email = existing.get("email") or "(unknown account)"
            print(f"Already signed in to ChatGPT for provider 'openai' ({email}); skipping login.")
            print("Use janito --login -f --provider openai to re-authenticate.")
            return 0
        try:
            fresh = ensure_fresh_record(existing)
        except ChatGPTAuthError:
            fresh = None
        if fresh is not None:
            email = fresh.get("email") or existing.get("email") or "(unknown account)"
            print(f"ChatGPT session refreshed for provider 'openai' ({email}).")
            return 0
        # Refresh failed (revoked/expired refresh token): fall through to
        # interactive re-login instead of stranding the user on "already
        # signed in" while every API call 401s.
    try:
        record = do_login(provider=provider)
    except ChatGPTAuthError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    summary = mask_record(record)
    email = summary.get("email") or "(unknown account)"
    print(f"ChatGPT sign-in complete for provider 'openai' ({email}).")
    print("No API key is required for this mode; ChatGPT-plan allowance applies.")
    return 0


def handle_logout(args) -> int:
    """Handle ``janito --logout``."""
    from ...openai_oauth import ChatGPTAuthError, do_logout

    provider = _resolve_login_provider(args) or "openai"
    if provider != "openai":
        print(
            f"Error: ChatGPT-plan logout is only supported for provider 'openai', not '{provider}'.",
            file=sys.stderr,
        )
        return 1
    try:
        removed = do_logout(provider=provider)
    except ChatGPTAuthError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if removed:
        print("ChatGPT credentials removed for provider 'openai'.")
    else:
        print("No ChatGPT credentials stored for provider 'openai'.")
    return 0


__all__ = ["handle_login", "handle_logout"]
