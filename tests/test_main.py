"""Tests for CLI entrypoint and HTTP bootstrap helpers."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import smartschool_mcp.__main__ as entry


def test_parse_auth_header_returns_authorization_value() -> None:
    scope = {
        "headers": [
            (b"host", b"localhost"),
            (b"Authorization", b"Bearer abc123"),
        ]
    }
    assert entry._parse_auth_header(scope) == b"Bearer abc123"


def test_parse_auth_header_lowercase_header_returns_value() -> None:
    scope = {
        "headers": [
            (b"host", b"localhost"),
            (b"authorization", b"Bearer abc123"),
        ]
    }
    assert entry._parse_auth_header(scope) == b"Bearer abc123"


def test_parse_auth_header_missing_returns_empty_bytes() -> None:
    assert entry._parse_auth_header({"headers": []}) == b""


@pytest.mark.asyncio
async def test_send_401_sends_start_and_body_messages() -> None:
    messages: list[dict] = []

    async def send(msg: dict) -> None:
        messages.append(msg)

    await entry._send_401(send, b'Bearer realm="Smartschool MCP"', "Unauthorized")

    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 401
    assert messages[1]["type"] == "http.response.body"
    assert b"Unauthorized" in messages[1]["body"]


def test_main_defaults_to_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    run_http = MagicMock()
    run_stdio = MagicMock()

    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    monkeypatch.setattr(sys, "argv", ["smartschool-mcp"])
    monkeypatch.setattr(entry, "_run_http", run_http)
    monkeypatch.setattr(entry.mcp, "run", run_stdio)

    entry.main()

    run_stdio.assert_called_once()
    run_http.assert_not_called()


def test_main_dispatches_http_with_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_http = MagicMock()
    run_stdio = MagicMock()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "smartschool-mcp",
            "--transport",
            "streamable-http",
            "--host",
            "127.0.0.1",
            "--port",
            "9000",
            "--universal",
            "--issuer-url",
            "https://mcp.example.com",
        ],
    )
    monkeypatch.setattr(entry, "_run_http", run_http)
    monkeypatch.setattr(entry.mcp, "run", run_stdio)

    entry.main()

    run_http.assert_called_once_with(
        "127.0.0.1",
        9000,
        universal=True,
        issuer_url="https://mcp.example.com",
    )
    run_stdio.assert_not_called()


def test_run_http_requires_issuer_url_in_universal_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("uvicorn.run", MagicMock())
    monkeypatch.setattr(
        "starlette.middleware.cors.CORSMiddleware",
        lambda app, **kwargs: ("cors", app, kwargs),
    )

    with pytest.raises(SystemExit, match="Universal mode requires --issuer-url"):
        entry._run_http("0.0.0.0", 8000, universal=True, issuer_url=None)


def test_run_http_non_universal_wraps_bearer_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamable_mock = MagicMock(return_value="asgi-app")
    fake_mcp = SimpleNamespace(
        settings=SimpleNamespace(),
        _custom_starlette_routes=[],
        streamable_http_app=streamable_mock,
    )
    run_mock = MagicMock()

    monkeypatch.setenv("MCP_API_KEY", "secret-token")
    monkeypatch.setattr(entry, "mcp", fake_mcp)
    monkeypatch.setattr("uvicorn.run", run_mock)
    monkeypatch.setattr(
        "starlette.middleware.cors.CORSMiddleware",
        lambda app, **kwargs: ("cors", app, kwargs),
    )

    entry._run_http("127.0.0.1", 8001, universal=False)

    streamable_mock.assert_called_once_with(
        stateless_http=True,
        json_response=True,
        transport_security=None,
        host="127.0.0.1",
    )
    wrapped_app = run_mock.call_args.args[0]
    assert wrapped_app[0] == "cors"
    assert isinstance(wrapped_app[1], entry._BearerAuthMiddleware)
    assert run_mock.call_args.kwargs["host"] == "127.0.0.1"
    assert run_mock.call_args.kwargs["port"] == 8001


def test_run_http_universal_configures_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamable_mock = MagicMock(return_value="asgi-app")
    fake_mcp = SimpleNamespace(
        settings=SimpleNamespace(
            auth=None,
        ),
        _custom_starlette_routes=[],
        streamable_http_app=streamable_mock,
    )
    run_mock = MagicMock()
    fake_provider = SimpleNamespace(issuer_url="https://mcp.example.com")

    monkeypatch.setattr(entry, "mcp", fake_mcp)
    monkeypatch.setattr("uvicorn.run", run_mock)
    monkeypatch.setattr(
        "starlette.middleware.cors.CORSMiddleware",
        lambda app, **kwargs: ("cors", app, kwargs),
    )
    monkeypatch.setattr(
        "mcp.server.auth.provider.ProviderTokenVerifier",
        lambda provider: ("verifier", provider),
    )
    monkeypatch.setattr(
        "mcp.server.auth.settings.AuthSettings",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        "mcp.server.auth.settings.ClientRegistrationOptions",
        lambda enabled: {"enabled": enabled},
    )
    monkeypatch.setattr(
        "mcp.server.auth.settings.RevocationOptions",
        lambda enabled: {"enabled": enabled},
    )
    monkeypatch.setattr(
        "mcp.server.transport_security.TransportSecuritySettings",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        "smartschool_mcp.auth.SmartschoolOAuthProvider",
        lambda issuer_url: fake_provider,
    )
    monkeypatch.setattr(
        "smartschool_mcp.auth.login_routes",
        lambda provider: ["login-route"],
    )

    entry._run_http(
        "0.0.0.0",
        8000,
        universal=True,
        issuer_url="https://mcp.example.com",
    )

    assert fake_mcp._token_verifier == ("verifier", fake_provider)  # type: ignore[attr-defined]
    assert fake_mcp._custom_starlette_routes == ["login-route"]
    assert str(fake_mcp.settings.auth["issuer_url"]) == "https://mcp.example.com/"
    assert (
        str(fake_mcp.settings.auth["resource_server_url"])
        == "https://mcp.example.com/mcp"
    )
    streamable_mock.assert_called_once()
    ts = streamable_mock.call_args.kwargs["transport_security"]
    allowed_hosts = ts["allowed_hosts"]
    assert "mcp.example.com" in allowed_hosts[-1]
    assert streamable_mock.call_args.kwargs["stateless_http"] is True
    assert streamable_mock.call_args.kwargs["json_response"] is True
    assert streamable_mock.call_args.kwargs["host"] == "0.0.0.0"
    run_mock.assert_called_once()
