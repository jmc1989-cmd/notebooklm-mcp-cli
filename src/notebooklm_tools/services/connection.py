"""Connection repair service — diagnose and fix Claude ↔ NotebookLM failures."""
from __future__ import annotations

import os
from typing import Any, Literal

DiagnosisCode = Literal[
    "auth_expired",
    "csrf_invalid",
    "rate_limited",
    "network_error",
    "env_override",
    "no_auth",
    "unknown",
]


def diagnose_connection_error(
    error_message: str = "",
    http_status: int = 0,
) -> dict[str, Any]:
    """Classify a connection error and return diagnosis with recommended action.

    Args:
        error_message: The error string from the failed API call.
        http_status: HTTP status code (0 if unknown or RPC-level error).

    Returns:
        dict with keys: code, title, description, action, recoverable
    """
    msg = error_message.lower()

    # Environment variable override blocks all disk-based auth
    if os.environ.get("NOTEBOOKLM_COOKIES"):
        return {
            "code": "env_override",
            "title": "Environment variable override active",
            "description": (
                "NOTEBOOKLM_COOKIES is set in the MCP config environment. "
                "It overrides all other auth sources, including nlm login and saved tokens."
            ),
            "action": (
                "Update the cookie value in your MCP config file "
                "(e.g. claude_desktop_config.json) and restart the MCP server. "
                "Or remove NOTEBOOKLM_COOKIES and use 'nlm login' instead."
            ),
            "recoverable": False,
        }

    # No credentials at all
    if "no authentication" in msg or "run 'nlm login'" in msg or not error_message:
        try:
            from notebooklm_tools.core.auth import load_cached_tokens

            if load_cached_tokens() is None:
                return {
                    "code": "no_auth",
                    "title": "No credentials found",
                    "description": "No authentication tokens are saved for any profile.",
                    "action": "Run 'nlm login' to authenticate with your Google account.",
                    "recoverable": False,
                }
        except Exception:
            pass

    # HTTP 401 / 403 or RPC error 16 — session/cookies expired
    if http_status in (401, 403) or "401" in msg or "403" in msg or "rpc error 16" in msg or "authentication" in msg:
        return {
            "code": "auth_expired",
            "title": "Authentication expired",
            "description": "Session cookies or CSRF token have expired.",
            "action": (
                "Attempting automatic token refresh. "
                "If that fails, run 'nlm login' to re-authenticate."
            ),
            "recoverable": True,
        }

    # HTTP 400 — typically an invalid or stale CSRF token
    if http_status == 400 or "csrf" in msg or "invalid token" in msg or "at=" in msg:
        return {
            "code": "csrf_invalid",
            "title": "Invalid CSRF token",
            "description": "The CSRF token (at= parameter) is stale or incorrect.",
            "action": "Refreshing CSRF token from live NotebookLM page.",
            "recoverable": True,
        }

    # HTTP 429 or RPC error 8 — rate limiting
    if http_status == 429 or "429" in msg or "rate limit" in msg or "resource exhausted" in msg or "rpc error 8" in msg:
        return {
            "code": "rate_limited",
            "title": "Rate limit reached",
            "description": "NotebookLM has throttled your requests (free tier: ~50 queries/day).",
            "action": "Wait until tomorrow or upgrade to NotebookLM Plus.",
            "recoverable": False,
        }

    # Network / connectivity errors
    if any(t in msg for t in ("connection", "timeout", "network", "unreachable", "refused", "ssl")):
        return {
            "code": "network_error",
            "title": "Network connectivity issue",
            "description": "Cannot reach notebooklm.google.com.",
            "action": "Check your internet connection and firewall settings.",
            "recoverable": False,
        }

    return {
        "code": "unknown",
        "title": "Unknown connection error",
        "description": error_message or "An unrecognized error occurred.",
        "action": (
            "Attempting token refresh as a first step. "
            "If that fails, run 'nlm login' and check 'nlm doctor' for diagnostics."
        ),
        "recoverable": True,
    }


def repair_connection(profile: str | None = None) -> dict[str, Any]:
    """Attempt to repair the connection through progressive recovery layers.

    Layer 1 — CSRF/session refresh from the live NotebookLM page (fast, ~1-2s).
    Layer 2 — Reload credentials from disk (picks up a fresh 'nlm login' run).
    Layer 3 — Headless Chrome re-authentication (requires saved Chrome profile).

    Args:
        profile: Profile name to repair. Uses the default profile if None.

    Returns:
        dict with keys: success, layer, message, next_step (on failure)
    """
    from notebooklm_tools.mcp.tools._utils import get_client, reset_client

    # Pre-check: if the connection already works, skip repair entirely
    pre = verify_connection()
    if pre["success"]:
        return {
            "success": True,
            "layer": 0,
            "message": "Connection is already working — no repair needed.",
        }

    # Layer 1: Force CSRF/session token refresh from live NotebookLM page.
    # Do NOT reset the client first — refresh in-place to preserve working state.
    try:
        client = get_client()
        client._refresh_auth_tokens()  # type: ignore[attr-defined]
        return {
            "success": True,
            "layer": 1,
            "message": "Connection restored: CSRF token and session ID refreshed from NotebookLM.",
        }
    except Exception as layer1_err:
        layer1_msg = str(layer1_err)

    # Layer 2: Reload tokens from disk (picks up externally run `nlm login`)
    try:
        from notebooklm_tools.core.auth import load_cached_tokens

        if profile:
            from notebooklm_tools.core.auth import AuthManager

            auth = AuthManager(profile)
            cached = auth.load_profile() if auth.profile_exists() else None
        else:
            cached = load_cached_tokens()

        if cached:
            reset_client()
            get_client()
            return {
                "success": True,
                "layer": 2,
                "message": f"Connection restored: credentials reloaded from disk{' (profile: ' + profile + ')' if profile else ''}.",
            }
    except Exception:
        pass

    # Layer 3: Headless Chrome re-authentication
    try:
        from notebooklm_tools.utils.cdp import run_headless_auth

        tokens = run_headless_auth()
        if tokens:
            reset_client()
            get_client()
            return {
                "success": True,
                "layer": 3,
                "message": "Connection restored: re-authenticated via headless Chrome.",
            }
    except Exception:
        pass

    return {
        "success": False,
        "layer": 0,
        "message": f"All automatic recovery attempts failed. Last error: {layer1_msg}",
        "next_step": (
            "Run 'nlm login' to manually re-authenticate, "
            "then call fix_connection or 'nlm fix-connection' again."
        ),
    }


def verify_connection() -> dict[str, Any]:
    """Verify the connection works by listing notebooks (lightweight call).

    Returns:
        dict with keys: success, notebook_count (on success), error (on failure)
    """
    try:
        from notebooklm_tools.mcp.tools._utils import get_client

        client = get_client()
        notebooks = client.list_notebooks()
        return {
            "success": True,
            "notebook_count": len(notebooks),
            "message": f"Connection verified — {len(notebooks)} notebook(s) accessible.",
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": "Connection verification failed.",
        }
