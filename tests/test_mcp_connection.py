"""Unit tests for the fix_connection MCP tool."""

from unittest.mock import patch

from notebooklm_tools.mcp.tools import connection


def test_fix_connection_unrecoverable_returns_action_required():
    """A non-recoverable diagnosis short-circuits before attempting repair."""
    with patch(
        "notebooklm_tools.services.connection.diagnose_connection_error",
        return_value={
            "code": "rate_limited",
            "title": "Rate limit reached",
            "description": "Throttled.",
            "action": "Wait until tomorrow.",
            "recoverable": False,
        },
    ):
        result = connection.fix_connection(error_message="429", http_status=429)

    assert result["status"] == "unrecoverable"
    assert result["diagnosis"] == "rate_limited"
    assert result["action_required"] == "Wait until tomorrow."


def test_fix_connection_full_success_flow():
    """Recoverable error → repair succeeds → verification passes."""
    with (
        patch(
            "notebooklm_tools.services.connection.diagnose_connection_error",
            return_value={
                "code": "auth_expired",
                "title": "Authentication expired",
                "description": "Session expired.",
                "action": "Refreshing.",
                "recoverable": True,
            },
        ),
        patch(
            "notebooklm_tools.services.connection.repair_connection",
            return_value={"success": True, "layer": 1, "message": "Refreshed."},
        ),
        patch(
            "notebooklm_tools.services.connection.verify_connection",
            return_value={"success": True, "notebook_count": 3, "message": "Verified — 3 notebook(s)."},
        ),
    ):
        result = connection.fix_connection(error_message="401", http_status=401)

    assert result["status"] == "success"
    assert result["repair_layer"] == 1
    assert "Retry" in result["next_step"]


def test_fix_connection_repair_fails():
    """Recoverable diagnosis but all repair layers fail."""
    with (
        patch(
            "notebooklm_tools.services.connection.diagnose_connection_error",
            return_value={
                "code": "auth_expired",
                "title": "Authentication expired",
                "description": "Session expired.",
                "action": "Refreshing.",
                "recoverable": True,
            },
        ),
        patch(
            "notebooklm_tools.services.connection.repair_connection",
            return_value={
                "success": False,
                "layer": 0,
                "message": "All failed.",
                "next_step": "Run 'nlm login'.",
            },
        ),
    ):
        result = connection.fix_connection(error_message="401", http_status=401)

    assert result["status"] == "error"
    assert result["repair_attempted"] is True
    assert result["next_step"] == "Run 'nlm login'."


def test_fix_connection_partial_when_verify_fails():
    """Repair succeeds but verification fails → partial status."""
    with (
        patch(
            "notebooklm_tools.services.connection.diagnose_connection_error",
            return_value={
                "code": "csrf_invalid",
                "title": "Invalid CSRF token",
                "description": "Stale token.",
                "action": "Refreshing.",
                "recoverable": True,
            },
        ),
        patch(
            "notebooklm_tools.services.connection.repair_connection",
            return_value={"success": True, "layer": 2, "message": "Reloaded."},
        ),
        patch(
            "notebooklm_tools.services.connection.verify_connection",
            return_value={"success": False, "error": "still failing", "message": "failed"},
        ),
    ):
        result = connection.fix_connection(error_message="400", http_status=400)

    assert result["status"] == "partial"
    assert result["repair_layer"] == 2
    assert result["verification_error"] == "still failing"


def test_fix_connection_passes_profile_through():
    """The profile arg is forwarded to repair_connection."""
    with (
        patch(
            "notebooklm_tools.services.connection.diagnose_connection_error",
            return_value={
                "code": "auth_expired",
                "title": "x",
                "description": "x",
                "action": "x",
                "recoverable": True,
            },
        ),
        patch(
            "notebooklm_tools.services.connection.repair_connection",
            return_value={"success": True, "layer": 1, "message": "ok"},
        ) as mock_repair,
        patch(
            "notebooklm_tools.services.connection.verify_connection",
            return_value={"success": True, "notebook_count": 0, "message": "ok"},
        ),
    ):
        connection.fix_connection(profile="work")

    mock_repair.assert_called_once_with(profile="work")
