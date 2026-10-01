"""Tests for the bearer-token auth layer added for remote (EC2) hosting.

Exercises BearerTokenMiddleware directly against a minimal dummy ASGI app,
rather than the real mcp.sse_app(), so these tests don't need a database and
stay fast -- the middleware only inspects headers, it has no idea what's
behind it.
"""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import mcp_server.server as mcp_server_module
from mcp_server.server import BearerTokenMiddleware, build_asgi_app

TOKEN = "demo-secret-token"


async def _ok_endpoint(request):
    return PlainTextResponse("ok")


@pytest.fixture
def protected_client():
    inner_app = Starlette(routes=[Route("/sse", _ok_endpoint)])
    app = BearerTokenMiddleware(inner_app, token=TOKEN)
    return TestClient(app)


def test_missing_auth_header_returns_401(protected_client):
    response = protected_client.get("/sse")
    assert response.status_code == 401


def test_wrong_token_returns_401(protected_client):
    response = protected_client.get("/sse", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_malformed_header_returns_401(protected_client):
    response = protected_client.get("/sse", headers={"Authorization": TOKEN})  # missing "Bearer " prefix
    assert response.status_code == 401


def test_correct_token_passes_through(protected_client):
    response = protected_client.get("/sse", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.text == "ok"


def test_build_asgi_app_wraps_with_middleware_when_token_set(monkeypatch):
    monkeypatch.setattr(mcp_server_module, "BEARER_TOKEN", TOKEN)
    app = build_asgi_app()
    assert isinstance(app, BearerTokenMiddleware)


def test_build_asgi_app_skips_middleware_when_token_unset(monkeypatch):
    monkeypatch.setattr(mcp_server_module, "BEARER_TOKEN", None)
    app = build_asgi_app()
    assert not isinstance(app, BearerTokenMiddleware)
