"""Tests for services.connection module — connection diagnosis and repair."""

from unittest.mock import MagicMock, patch

from notebooklm_tools.services.connection import (
    diagnose_connection_error,
    repair_connection,
    verify_connection,
)


class TestDiagnoseConnectionError:
    """Test the error classification logic."""

    def test_env_override_takes_precedence(self, monkeypatch):
        """NOTEBOOKLM_COOKIES env var overrides everything and is unrecoverable."""
        monkeypatch.setenv("NOTEBOOKLM_COOKIES", "SID=abc; HSID=def")
        result = diagnose_connection_error("401 Unauthorized", 401)
        assert result["code"] == "env_override"
        assert result["recoverable"] is False

    def test_401_is_auth_expired(self, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_COOKIES", raising=False)
        result = diagnose_connection_error("401 Unauthorized", 401)
        assert result["code"] == "auth_expired"
        assert result["recoverable"] is True

    def test_403_is_auth_expired(self, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_COOKIES", raising=False)
        result = diagnose_connection_error("403 Forbidden", 403)
        assert result["code"] == "auth_expired"
        assert result["recoverable"] is True

    def test_rpc_error_16_is_auth_expired(self, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_COOKIES", raising=False)
        result = diagnose_connection_error("RPC Error 16: unauthenticated", 0)
        assert result["code"] == "auth_expired"
        assert result["recoverable"] is True

    def test_400_is_csrf_invalid(self, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_COOKIES", raising=False)
        result = diagnose_connection_error("400 Bad Request", 400)
        assert result["code"] == "csrf_invalid"
        assert result["recoverable"] is True

    def test_csrf_keyword_is_csrf_invalid(self, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_COOKIES", raising=False)
        result = diagnose_connection_error("Invalid CSRF token", 0)
        assert result["code"] == "csrf_invalid"
        assert result["recoverable"] is True

    def test_429_is_rate_limited(self, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_COOKIES", raising=False)
        result = diagnose_connection_error("429 Too Many Requests", 429)
        assert result["code"] == "rate_limited"
        assert result["recoverable"] is False

    def test_resource_exhausted_is_rate_limited(self, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_COOKIES", raising=False)
        result = diagnose_connection_error("RPC Error 8: resource exhausted", 0)
        assert result["code"] == "rate_limited"
        assert result["recoverable"] is False

    def test_network_error(self, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_COOKIES", raising=False)
        result = diagnose_connection_error("Connection refused", 0)
        assert result["code"] == "network_error"
        assert result["recoverable"] is False

    def test_timeout_is_network_error(self, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_COOKIES", raising=False)
        result = diagnose_connection_error("Request timeout after 30s", 0)
        assert result["code"] == "network_error"

    @patch("notebooklm_tools.core.auth.load_cached_tokens", return_value=None)
    def test_empty_error_with_no_tokens_is_no_auth(self, _mock_load, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_COOKIES", raising=False)
        result = diagnose_connection_error("", 0)
        assert result["code"] == "no_auth"
        assert result["recoverable"] is False

    @patch("notebooklm_tools.core.auth.load_cached_tokens")
    def test_unknown_error_with_tokens_is_recoverable(self, mock_load, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_COOKIES", raising=False)
        mock_load.return_value = MagicMock()  # tokens exist
        result = diagnose_connection_error("Something weird happened", 500)
        assert result["code"] == "unknown"
        assert result["recoverable"] is True

    def test_result_has_required_keys(self, monkeypatch):
        monkeypatch.delenv("NOTEBOOKLM_COOKIES", raising=False)
        result = diagnose_connection_error("401", 401)
        for key in ("code", "title", "description", "action", "recoverable"):
            assert key in result


class TestRepairConnection:
    """Test the layered repair logic with mocked client/auth."""

    @patch("notebooklm_tools.mcp.tools._utils.reset_client")
    @patch("notebooklm_tools.mcp.tools._utils.get_client")
    def test_layer1_success(self, mock_get_client, _mock_reset):
        """Layer 1 succeeds when CSRF refresh works."""
        client = MagicMock()
        mock_get_client.return_value = client

        result = repair_connection()

        assert result["success"] is True
        assert result["layer"] == 1
        client._refresh_auth_tokens.assert_called_once()

    @patch("notebooklm_tools.core.auth.load_cached_tokens")
    @patch("notebooklm_tools.mcp.tools._utils.reset_client")
    @patch("notebooklm_tools.mcp.tools._utils.get_client")
    def test_layer2_success_after_layer1_fails(
        self, mock_get_client, _mock_reset, mock_load
    ):
        """Layer 1 raises, Layer 2 reloads from disk successfully."""
        client = MagicMock()
        client._refresh_auth_tokens.side_effect = ValueError("auth expired")
        mock_get_client.return_value = client
        mock_load.return_value = MagicMock()  # cached tokens exist

        result = repair_connection()

        assert result["success"] is True
        assert result["layer"] == 2

    @patch("notebooklm_tools.utils.cdp.run_headless_auth")
    @patch("notebooklm_tools.core.auth.load_cached_tokens", return_value=None)
    @patch("notebooklm_tools.mcp.tools._utils.reset_client")
    @patch("notebooklm_tools.mcp.tools._utils.get_client")
    def test_layer3_headless_success(
        self, mock_get_client, _mock_reset, _mock_load, mock_headless
    ):
        """Layers 1+2 fail, Layer 3 headless auth succeeds."""
        client = MagicMock()
        client._refresh_auth_tokens.side_effect = ValueError("auth expired")
        mock_get_client.return_value = client
        mock_headless.return_value = MagicMock()  # headless returns tokens

        result = repair_connection()

        assert result["success"] is True
        assert result["layer"] == 3

    @patch("notebooklm_tools.utils.cdp.run_headless_auth", return_value=None)
    @patch("notebooklm_tools.core.auth.load_cached_tokens", return_value=None)
    @patch("notebooklm_tools.mcp.tools._utils.reset_client")
    @patch("notebooklm_tools.mcp.tools._utils.get_client")
    def test_all_layers_fail(
        self, mock_get_client, _mock_reset, _mock_load, _mock_headless
    ):
        """All three layers fail — returns failure with next_step."""
        client = MagicMock()
        client._refresh_auth_tokens.side_effect = ValueError("auth expired")
        mock_get_client.return_value = client

        result = repair_connection()

        assert result["success"] is False
        assert result["layer"] == 0
        assert "next_step" in result
        assert "auth expired" in result["message"]

    @patch("notebooklm_tools.core.auth.AuthManager")
    @patch("notebooklm_tools.core.auth.load_cached_tokens")
    @patch("notebooklm_tools.mcp.tools._utils.reset_client")
    @patch("notebooklm_tools.mcp.tools._utils.get_client")
    def test_layer2_uses_named_profile(
        self, mock_get_client, _mock_reset, _mock_load, mock_auth_manager
    ):
        """When a profile is given, Layer 2 loads that specific profile."""
        client = MagicMock()
        client._refresh_auth_tokens.side_effect = ValueError("auth expired")
        mock_get_client.return_value = client

        auth_instance = MagicMock()
        auth_instance.profile_exists.return_value = True
        auth_instance.load_profile.return_value = MagicMock()
        mock_auth_manager.return_value = auth_instance

        result = repair_connection(profile="work")

        assert result["success"] is True
        assert result["layer"] == 2
        mock_auth_manager.assert_called_once_with("work")


class TestVerifyConnection:
    """Test connection verification."""

    @patch("notebooklm_tools.mcp.tools._utils.get_client")
    def test_verify_success(self, mock_get_client):
        client = MagicMock()
        client.list_notebooks.return_value = [{"id": "nb-1"}, {"id": "nb-2"}]
        mock_get_client.return_value = client

        result = verify_connection()

        assert result["success"] is True
        assert result["notebook_count"] == 2

    @patch("notebooklm_tools.mcp.tools._utils.get_client")
    def test_verify_failure(self, mock_get_client):
        client = MagicMock()
        client.list_notebooks.side_effect = ValueError("No authentication found")
        mock_get_client.return_value = client

        result = verify_connection()

        assert result["success"] is False
        assert "No authentication found" in result["error"]
