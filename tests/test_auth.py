from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.auth import REDACTED, redact
from server.main import AuthMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/value")
    def value() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_loopback_does_not_require_auth(monkeypatch) -> None:
    monkeypatch.setenv("QUILL_AUTH_HOST", "127.0.0.1")
    monkeypatch.delenv("QUILL_ACCESS_TOKEN", raising=False)
    assert TestClient(_app()).get("/api/value").status_code == 200


def test_non_loopback_requires_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("QUILL_AUTH_HOST", "0.0.0.0")
    monkeypatch.setenv("QUILL_ACCESS_TOKEN", "secret")
    client = TestClient(_app())
    assert client.get("/api/value").status_code == 401
    assert client.get("/api/value", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_redact_never_returns_secret() -> None:
    assert redact("secret") == REDACTED
    assert redact("") == ""
