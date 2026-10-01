"""Tests for the REST API's bearer-token auth layer, added for remote (EC2)
hosting -- mirrors tests/test_mcp_server_auth.py for the MCP server.

Uses a real route (/card-products, no DB seeding needed since an empty
result is still a 200) rather than mocking anything -- the auth dependency
runs before the route body either way.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import api.main as api_main_module
from api.dependencies import get_db
from api.main import app
from db.models import Base

TOKEN = "demo-secret-token"


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api_auth_test.db'}")
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_no_auth_configured_allows_requests_through(client, monkeypatch):
    monkeypatch.setattr(api_main_module, "API_BEARER_TOKEN", None)
    response = client.get("/card-products")
    assert response.status_code == 200


def test_missing_auth_header_returns_401_when_token_configured(client, monkeypatch):
    monkeypatch.setattr(api_main_module, "API_BEARER_TOKEN", TOKEN)
    response = client.get("/card-products")
    assert response.status_code == 401


def test_wrong_token_returns_401(client, monkeypatch):
    monkeypatch.setattr(api_main_module, "API_BEARER_TOKEN", TOKEN)
    response = client.get("/card-products", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_correct_token_passes_through(client, monkeypatch):
    monkeypatch.setattr(api_main_module, "API_BEARER_TOKEN", TOKEN)
    response = client.get("/card-products", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
