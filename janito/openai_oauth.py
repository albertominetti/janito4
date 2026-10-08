"""ChatGPT-plan OAuth for the ``openai`` provider (issue #154).

Implements the documented OpenAI OAuth flow for open-source/local
applications ("Sign in with ChatGPT"):

- PKCE (S256), cryptographically secure ``state``/``nonce``
- browser-based authorization against ``auth.openai.com``
- localhost loopback callback (dynamic port, fixed path)
- dynamic client bootstrap/registration (``dynamic_agent_client`` first,
  persisted ``oaiapp_...`` client id afterwards)
- authorization-code exchange, ID-token validation, scope validation
- access-token expiry handling, refresh with rotation

Only stdlib plus the project's existing ``requests`` dependency and
``PyJWT[crypto]`` (required for ID-token signature validation) are used.
Never log tokens, codes, verifiers, or secrets from this module.

Official docs (authoritative): ``developers.openai.com/siwc/*``.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

import jwt
import requests

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Endpoints / protocol constants (current official documentation)
# ----------------------------------------------------------------------

AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
JWKS_URL = "https://auth.openai.com/.well-known/jwks.json"
ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"

DYNAMIC_CLIENT_ID = "dynamic_agent_client"
REQUIRED_SCOPE = "chatgpt.tokens.use.direct"
DEFAULT_SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"

REDIRECT_PATH = "/auth/callback"
REDIRECT_HOST = "127.0.0.1"

EXPIRY_SKEW_SECONDS = 60
CALLBACK_TIMEOUT_SECONDS = 300
HTTP_TIMEOUT_SECONDS = 30

# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------


class ChatGPTAuthError(ValueError):
    """Raised for any ChatGPT OAuth failure (login, refresh, validation)."""

    def __init__(self, message: str, *, kind: str = "auth"):
        super().__init__(message)
        self.kind = kind


# ----------------------------------------------------------------------
# PKCE / state / nonce
# ----------------------------------------------------------------------


def generate_code_verifier() -> str:
    """Generate a PKCE ``code_verifier`` (43-128 chars, unreserved)."""
    return secrets.token_urlsafe(64)


def generate_code_challenge(verifier: str) -> str:
    """Derive the S256 ``code_challenge`` for a verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    """Generate a cryptographically secure OAuth ``state`` value."""
    return secrets.token_urlsafe(32)


def generate_nonce() -> str:
    """Generate a cryptographically secure OIDC ``nonce`` value."""
    return secrets.token_urlsafe(32)


def generate_host_id() -> str:
    """Generate a stable per-host ``ext_agent_host_id`` (``urn:uuid:…``)."""
    import uuid

    return f"urn:uuid:{uuid.uuid4()}"


# ----------------------------------------------------------------------
# Authorization URL
# ----------------------------------------------------------------------


def build_authorization_url(
    *,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
    host_id: str,
    client_id: str | None = None,
    agent_name: str = "Janito",
    id_token_hint: str | None = None,
    login_hint: str | None = None,
) -> str:
    """Build the browser authorization URL.

    First sign-in (``client_id`` empty/``dynamic_agent_client``) includes
    ``agent_name_hint``; re-authentication uses the persisted issued client
    id plus ``id_token_hint``/``login_hint`` when available.
    """
    params: dict[str, str] = {
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "resource": RESOURCE,
        "scope": DEFAULT_SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "ext_agent_host_id": host_id,
    }
    if not client_id or client_id == DYNAMIC_CLIENT_ID:
        params["client_id"] = DYNAMIC_CLIENT_ID
        params["agent_name_hint"] = agent_name
    else:
        params["client_id"] = client_id
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        if login_hint:
            params["login_hint"] = login_hint
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


# ----------------------------------------------------------------------
# Loopback callback
# ----------------------------------------------------------------------


class _CallbackHandler(BaseHTTPRequestHandler):
    """Single-shot handler capturing the OAuth redirect query params."""

    result: dict | None = None
    error: Exception | None = None
    expected_state: str = ""
    expected_path: str = REDIRECT_PATH

    def log_message(self, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != self.expected_path:
                # Ignore stray requests (favicon, probes) - do not fail login.
                try:
                    self._respond(404, "Not found.")
                except OSError:
                    pass
                return
            query = urllib.parse.parse_qs(parsed.query)

            def get(key: str) -> str | None:
                return query.get(key, [None])[0]
            if get("error"):
                desc = get("error_description") or get("error")
                self._respond(200, "Authorization denied. You can close this tab.")
                type(self).error = ChatGPTAuthError(f"Authorization denied: {desc}", kind="denied")
                return
            code = get("code")
            state = get("state")
            if not code or not state:
                self._respond(400, "Invalid callback: missing code/state.")
                type(self).error = ChatGPTAuthError("Invalid callback: missing code/state.", kind="invalid_callback")
                return
            if state != self.expected_state:
                self._respond(400, "State mismatch. Login aborted.")
                type(self).error = ChatGPTAuthError("OAuth state mismatch.", kind="state_mismatch")
                return
            type(self).result = {
                "code": code,
                "state": state,
                "client_id": get("client_id"),
                "scope": get("scope"),
            }
            self._respond(200, "Authentication complete. You can close this tab.")
        except Exception as e:  # noqa: BLE001 - report via error slot
            type(self).error = e if isinstance(e, ChatGPTAuthError) else ChatGPTAuthError(str(e), kind="invalid_callback")
            try:
                self._respond(400, "Login failed. You can close this tab.")
            except OSError:
                pass

    def _respond(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def start_callback_listener(
    expected_state: str, *, timeout: int = CALLBACK_TIMEOUT_SECONDS
) -> tuple[HTTPServer, type]:
    """Start the loopback listener and return ``(server, handler, redirect_uri)``.

    Split out of :func:`wait_for_callback` so :func:`do_login` can build the
    authorization URL (which needs the redirect URI) and open the browser
    before blocking on the callback.
    """
    handler = type(
        "_BoundCallbackHandler",
        (_CallbackHandler,),
        {"expected_state": expected_state, "result": None, "error": None},
    )
    server = ThreadingHTTPServer((REDIRECT_HOST, 0), handler)
    server.daemon_threads = True
    server.timeout = timeout
    return server, handler


def redirect_uri_for(server: HTTPServer) -> str:
    """Return the redirect URI for a listener started by :func:`start_callback_listener`."""
    port = server.server_address[1]
    return f"http://{REDIRECT_HOST}:{port}{REDIRECT_PATH}"


def await_callback(
    server: HTTPServer,
    handler: type,
    *,
    timeout: int = CALLBACK_TIMEOUT_SECONDS,
) -> dict:
    """Block until the OAuth redirect arrives; return its params.

    Uses ``serve_forever`` in a daemon thread (threaded server) so stray
    requests (favicon, probes) never block or abort the login.  On timeout,
    raises :class:`ChatGPTAuthError`.  Always shuts down ``server``.
    """
    worker = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
    )
    worker.start()
    deadline = time.monotonic() + timeout
    try:
        while handler.result is None and handler.error is None:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        worker.join(timeout=5)
        server.server_close()
    if handler.error is not None:
        raise handler.error
    if handler.result is not None:
        return handler.result
    raise ChatGPTAuthError(
        "Login timed out waiting for the browser callback.",
        kind="timeout",
    )


def wait_for_callback(expected_state: str, *, timeout: int = CALLBACK_TIMEOUT_SECONDS) -> tuple[dict, str]:
    """Start a loopback listener and wait for the OAuth redirect.

    Returns ``(params, redirect_uri)`` where ``params`` carries
    ``code``/``state``/``client_id``/``scope``.  Raises
    :class:`ChatGPTAuthError` on timeout, denial, invalid callback, or
    state mismatch.
    """
    server, handler = start_callback_listener(expected_state, timeout=timeout)
    redirect_uri = redirect_uri_for(server)
    params = await_callback(server, handler, timeout=timeout)
    return params, redirect_uri


# ----------------------------------------------------------------------
# Token endpoint
# ----------------------------------------------------------------------


def _post_token(data: dict[str, str]) -> dict:
    """POST a form-encoded grant to the token endpoint; return parsed JSON."""
    try:
        response = requests.post(TOKEN_URL, data=data, timeout=HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise ChatGPTAuthError(f"Token request failed: {e}", kind="network") from e
    try:
        payload = response.json()
    except ValueError as e:
        raise ChatGPTAuthError("Malformed token response (not JSON).", kind="malformed") from e
    if response.status_code != 200:
        detail = payload.get("error_description") or payload.get("error") or f"HTTP {response.status_code}"
        raise ChatGPTAuthError(f"Token request failed: {detail}", kind="exchange_failed")
    if not isinstance(payload, dict):
        raise ChatGPTAuthError("Malformed token response.", kind="malformed")
    return payload


def exchange_code(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
) -> dict:
    """Exchange an authorization code for tokens; validate required fields."""
    payload = _post_token(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "resource": RESOURCE,
        }
    )
    return _require_token_fields(payload)


def refresh_access_token(*, refresh_token: str, client_id: str) -> dict:
    """Refresh an access token (rotation-aware); validate required fields."""
    payload = _post_token(
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "resource": RESOURCE,
        }
    )
    return _require_token_fields(payload)


def _require_token_fields(payload: dict) -> dict:
    for field in ("access_token", "id_token", "refresh_token", "expires_in"):
        if not payload.get(field):
            raise ChatGPTAuthError(f"Token response missing required field: {field}.", kind="malformed")
    return payload


# ----------------------------------------------------------------------
# ID-token + scope validation
# ----------------------------------------------------------------------

_jwks_client: jwt.PyJWKClient | None = None
_jwks_lock = threading.Lock()


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        with _jwks_lock:
            if _jwks_client is None:
                _jwks_client = jwt.PyJWKClient(JWKS_URL)
    return _jwks_client


def validate_id_token(id_token: str, *, client_id: str, nonce: str | None = None) -> dict:
    """Validate the ID token signature/issuer/audience/expiry/nonce.

    Returns the decoded claims.  Raises :class:`ChatGPTAuthError` with kind
    ``invalid_id_token`` on any failure.  Never logs the token.
    """
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=ISSUER,
            leeway=120,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise ChatGPTAuthError(f"Invalid ID token: {e}", kind="invalid_id_token") from e
    if nonce is not None and claims.get("nonce") != nonce:
        raise ChatGPTAuthError("Invalid ID token: nonce mismatch.", kind="invalid_id_token")
    return claims


def granted_scopes(payload: dict | str | list | None) -> list[str]:
    """Normalize a granted-scope value to a list of scope strings."""
    if payload is None:
        return []
    if isinstance(payload, list):
        raw: str = " ".join(str(entry) for entry in payload)
    elif isinstance(payload, dict):
        raw = str(payload.get("scope", ""))
    else:
        raw = str(payload)
    return [entry for entry in raw.split() if entry]


def has_required_scope(scopes: list[str]) -> bool:
    """Whether the granted scopes include ChatGPT-plan token sharing."""
    return REQUIRED_SCOPE in scopes


# ----------------------------------------------------------------------
# Stored-record helpers (auth.json dict, issue #154)
# ----------------------------------------------------------------------


def build_record(
    *,
    token_payload: dict,
    client_id: str,
    host_id: str,
    scopes: list[str],
    id_claims: dict,
) -> dict:
    """Build the persistable OAuth record from a token response + claims."""
    now = time.time()
    try:
        lifetime = int(token_payload["expires_in"])
    except (KeyError, TypeError, ValueError) as e:
        raise ChatGPTAuthError("Token response has invalid expires_in.", kind="malformed") from e
    return {
        "client_id": client_id,
        "ext_agent_host_id": host_id,
        "email": id_claims.get("email"),
        "sub": id_claims.get("sub"),
        "access_token": token_payload["access_token"],
        "refresh_token": token_payload["refresh_token"],
        "id_token": token_payload["id_token"],
        "token_type": token_payload.get("token_type", "Bearer"),
        "expires_at": now + lifetime,
        "scopes": scopes,
        "saved_at": now,
    }


def is_expired(record: dict, *, skew: int = EXPIRY_SKEW_SECONDS) -> bool:
    """Whether the stored access token is expired (with clock skew)."""
    try:
        return time.time() >= float(record.get("expires_at", 0)) - skew
    except (TypeError, ValueError):
        return True


_refresh_lock = threading.Lock()


def ensure_fresh_record(record: dict) -> dict:
    """Return a usable record, refreshing transparently when expired.

    Persists rotated credentials via the auth store.  Raises
    :class:`ChatGPTAuthError` (kind ``refresh_failed`` or ``expired``)
    when no refresh token can produce a valid access token.
    """
    from .auth_config import set_chatgpt_oauth

    if not is_expired(record):
        return record
    refresh_token = record.get("refresh_token")
    client_id = record.get("client_id")
    if not refresh_token or not client_id:
        raise ChatGPTAuthError(
            "ChatGPT credentials expired. Re-run: janito --login",
            kind="expired",
        )
    with _refresh_lock:
        payload = refresh_access_token(refresh_token=refresh_token, client_id=client_id)
        scopes = granted_scopes(payload.get("scope", record.get("scopes", [])))
        claims = validate_id_token(payload["id_token"], client_id=client_id, nonce=None)
        fresh = build_record(
            token_payload=payload,
            client_id=client_id,
            host_id=record.get("ext_agent_host_id", ""),
            scopes=scopes or granted_scopes(record.get("scopes")),
            id_claims=claims,
        )
        # Preserve the host id when the refresh response carries none.
        if not fresh.get("ext_agent_host_id"):
            fresh["ext_agent_host_id"] = record.get("ext_agent_host_id", "")
        set_chatgpt_oauth(fresh)
        return fresh


def mask_record(record: dict) -> dict:
    """Return a log-safe summary of an OAuth record (no secrets)."""
    return {
        "client_id": record.get("client_id"),
        "email": record.get("email"),
        "expires_at": record.get("expires_at"),
        "scopes": record.get("scopes"),
    }


def do_login(
    *,
    provider: str = "openai",
    timeout: int = CALLBACK_TIMEOUT_SECONDS,
    agent_name: str = "Janito",
) -> dict:
    """Run the interactive ChatGPT-plan login for the ``openai`` provider.

    Generates PKCE/state/nonce, starts the loopback callback, opens the
    browser (printing the URL as fallback), validates the callback,
    exchanges the code, validates the ID token and required scope, and
    persists the structured record to ``auth.json`` (0600 via the store).

    Only ``openai`` is supported.  Fails when an API key is already
    configured (deterministic billing: remove it first).  Never prints or
    logs secrets; raises :class:`ChatGPTAuthError` with a ``kind`` for
    login cancelled / timeout / state mismatch / invalid callback /
    denied / missing scope / exchange / ID-token failure.

    Returns the persisted OAuth record.
    """
    if provider != "openai":
        raise ChatGPTAuthError(
            f"ChatGPT-plan login is only supported for provider 'openai', not '{provider}'.",
            kind="unsupported_provider",
        )
    from .auth_config import get_api_key, get_chatgpt_oauth, set_chatgpt_oauth

    existing_key = get_api_key("openai")
    if existing_key:
        raise ChatGPTAuthError(
            "An API key is already configured for provider 'openai'. "
            "Remove it first (janito --delete-api-key --provider openai), "
            "then re-run: janito --login",
            kind="api_key_present",
        )
    previous = get_chatgpt_oauth() or {}
    client_id = previous.get("client_id") or DYNAMIC_CLIENT_ID
    host_id = previous.get("ext_agent_host_id") or generate_host_id()
    id_token_hint = previous.get("id_token") if client_id != DYNAMIC_CLIENT_ID else None
    login_hint = previous.get("email") if client_id != DYNAMIC_CLIENT_ID else None

    verifier = generate_code_verifier()
    challenge = generate_code_challenge(verifier)
    state = generate_state()
    nonce = generate_nonce()

    server, handler = start_callback_listener(state, timeout=timeout)
    redirect_uri = redirect_uri_for(server)
    auth_url = build_authorization_url(
        redirect_uri=redirect_uri,
        state=state,
        nonce=nonce,
        code_challenge=challenge,
        host_id=host_id,
        client_id=client_id,
        agent_name=agent_name,
        id_token_hint=id_token_hint,
        login_hint=login_hint,
    )
    print("Opening the browser for ChatGPT sign-in...")
    print(auth_url)
    print(f"Waiting for callback on {redirect_uri} (timeout {timeout}s).")
    try:
        import webbrowser

        webbrowser.open(auth_url)
    except Exception:  # noqa: BLE001 - browser open is best-effort; URL is printed
        pass
    params = await_callback(server, handler, timeout=timeout)

    issued_client_id = params.get("client_id") or client_id
    token_payload = exchange_code(
        code=params["code"],
        code_verifier=verifier,
        redirect_uri=redirect_uri,
        client_id=issued_client_id,
    )
    claims = validate_id_token(
        token_payload["id_token"], client_id=issued_client_id, nonce=nonce
    )
    scopes = granted_scopes(
        token_payload.get("scope", params.get("scope", previous.get("scopes", [])))
    )
    if not has_required_scope(scopes):
        raise ChatGPTAuthError(
            f"Missing required scope '{REQUIRED_SCOPE}'. Re-run login and approve ChatGPT-plan access.",
            kind="missing_scope",
        )
    record = build_record(
        token_payload=token_payload,
        client_id=issued_client_id,
        host_id=host_id,
        scopes=scopes,
        id_claims=claims,
    )
    if not set_chatgpt_oauth(record):
        raise ChatGPTAuthError("Failed to persist ChatGPT credentials.", kind="persist_failed")
    return record


def do_logout(*, provider: str = "openai") -> bool:
    """Remove the stored ChatGPT OAuth record for ``openai``.

    Only ``openai`` is supported.  Never touches the independently
    configured API key.  Returns ``True`` when a record was removed.
    """
    if provider != "openai":
        raise ChatGPTAuthError(
            f"ChatGPT-plan logout is only supported for provider 'openai', not '{provider}'.",
            kind="unsupported_provider",
        )
    from .auth_config import delete_chatgpt_oauth

    return bool(delete_chatgpt_oauth())


__all__ = [
    "AUTHORIZE_URL",
    "DEFAULT_SCOPES",
    "DYNAMIC_CLIENT_ID",
    "ISSUER",
    "JWKS_URL",
    "REDIRECT_PATH",
    "REQUIRED_SCOPE",
    "RESOURCE",
    "TOKEN_URL",
    "ChatGPTAuthError",
    "build_authorization_url",
    "build_record",
    "await_callback",
    "do_login",
    "do_logout",
    "ensure_fresh_record",
    "exchange_code",
    "generate_code_challenge",
    "generate_code_verifier",
    "generate_host_id",
    "generate_nonce",
    "generate_state",
    "granted_scopes",
    "has_required_scope",
    "is_expired",
    "mask_record",
    "redirect_uri_for",
    "refresh_access_token",
    "start_callback_listener",
    "validate_id_token",
    "wait_for_callback",
]
