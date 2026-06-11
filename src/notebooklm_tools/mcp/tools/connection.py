"""Connection repair MCP tool — fix_connection."""

from ._utils import ResultDict, error_result, logged_tool


@logged_tool()
def fix_connection(
    error_message: str = "",
    http_status: int = 0,
    profile: str = "",
) -> ResultDict:
    """Diagnose and automatically repair connection issues between Claude and NotebookLM.

    Call this tool whenever a NotebookLM tool returns an authentication error,
    network failure, or any unexpected connection problem. The tool runs up to
    three recovery layers automatically and reports what it did.

    Recovery layers (attempted in order):
      1. Refresh CSRF token and session ID from the live NotebookLM page (~1-2s)
      2. Reload saved credentials from disk (picks up a fresh `nlm login` run)
      3. Re-authenticate via headless Chrome (requires a saved Chrome profile)

    After a successful repair the original failing tool should be retried immediately.
    If all layers fail, the tool returns clear instructions for manual re-auth.

    Args:
        error_message: The error text from the failed tool call (helps diagnosis).
        http_status: HTTP status code if known (401, 403, 400, 429, etc.).
        profile: Profile name to repair. Leave empty to use the default profile.

    Returns:
        dict with status, diagnosis, repair result, and next_step on failure.

    Example:
        # After notebook_query returns 401:
        fix_connection(error_message="401 Unauthorized", http_status=401)
        # → retries notebook_query
    """
    from notebooklm_tools.services.connection import (
        diagnose_connection_error,
        repair_connection,
        verify_connection,
    )

    # Step 1: Diagnose
    diagnosis = diagnose_connection_error(error_message=error_message, http_status=http_status)

    if not diagnosis["recoverable"]:
        return {
            "status": "unrecoverable",
            "diagnosis": diagnosis["code"],
            "title": diagnosis["title"],
            "description": diagnosis["description"],
            "action_required": diagnosis["action"],
        }

    # Step 2: Repair
    repair = repair_connection(profile=profile or None)

    if not repair["success"]:
        return {
            "status": "error",
            "diagnosis": diagnosis["code"],
            "title": diagnosis["title"],
            "repair_attempted": True,
            "repair_layers_tried": 3,
            "error": repair["message"],
            "next_step": repair.get("next_step", "Run 'nlm login' to re-authenticate."),
        }

    # Step 3: Verify
    verification = verify_connection()

    if verification["success"]:
        return {
            "status": "success",
            "diagnosis": diagnosis["code"],
            "repair_layer": repair["layer"],
            "repair_message": repair["message"],
            "verification": verification["message"],
            "next_step": "Retry the original operation — connection is restored.",
        }

    # Repaired but verification failed (edge case)
    return {
        "status": "partial",
        "diagnosis": diagnosis["code"],
        "repair_layer": repair["layer"],
        "repair_message": repair["message"],
        "verification_error": verification.get("error"),
        "next_step": (
            "Tokens were refreshed but verification failed. "
            "Try the original operation; if it still fails run 'nlm login'."
        ),
    }
