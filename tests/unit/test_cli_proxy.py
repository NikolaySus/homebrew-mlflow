from __future__ import annotations

import httpx
import pytest
from homebrew_mlflow.cli.main import (
    _PROXY_ENVIRONMENT_VARIABLES,
    _http_client,
    app,
)
from typer.testing import CliRunner


def _clear_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _PROXY_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("scheme", ["socks", "socks5", "socks5h", "ftp"])
def test_login_rejects_unsupported_proxy_without_exposing_its_value(
    monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    _clear_proxy_environment(monkeypatch)
    monkeypatch.setenv(
        "ALL_PROXY", f"{scheme}://proxy-user:proxy-secret@127.0.0.1:12334/"
    )

    result = CliRunner().invoke(
        app,
        ["login", "--server", "http://localhost:3000", "--no-browser"],
    )

    assert result.exit_code == 2
    assert "unsupported proxy configuration in ALL_PROXY" in result.output
    assert "HTTP/HTTPS" in result.output
    assert "Traceback" not in result.output
    assert "proxy-user" not in result.output
    assert "proxy-secret" not in result.output
    assert "127.0.0.1" not in result.output
    assert "12334" not in result.output


def test_login_sanitizes_proxy_constructor_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_proxy_environment(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example")

    def fail_client(*, timeout: float | httpx.Timeout) -> httpx.Client:
        del timeout
        raise ValueError("secret-bearing proxy parser failure")

    monkeypatch.setattr("homebrew_mlflow.cli.main.httpx.Client", fail_client)

    result = CliRunner().invoke(
        app,
        ["login", "--server", "http://localhost:3000", "--no-browser"],
    )

    assert result.exit_code == 2
    assert "invalid proxy configuration in HTTPS_PROXY" in result.output
    assert "secret-bearing" not in result.output
    assert "Traceback" not in result.output


def test_http_proxy_is_passed_to_httpx_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_proxy_environment(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    expected = object()
    observed: list[float | httpx.Timeout] = []

    def client(*, timeout: float | httpx.Timeout) -> object:
        observed.append(timeout)
        return expected

    monkeypatch.setattr("homebrew_mlflow.cli.main.httpx.Client", client)

    assert _http_client(timeout=15) is expected
    assert observed == [15]
