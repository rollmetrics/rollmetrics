"""Tests for legacy /pitch and /pitch-deck.html redirects to the root landing page."""

from __future__ import annotations

from fastapi.testclient import TestClient
from rollmetrics.auth import get_app_settings
from rollmetrics.config import Settings
from rollmetrics.main import create_app


def _make_client() -> TestClient:
    app = create_app()
    test_settings = Settings(environment="cloud", allow_local_dev_bypass=False)
    app.dependency_overrides[get_app_settings] = lambda: test_settings
    return TestClient(app)


def test_legacy_pitch_routes_redirect_to_landing_page() -> None:
    client = _make_client()

    for route in ("/pitch", "/pitch-deck.html"):
        redirect = client.get(route, follow_redirects=False)
        assert redirect.status_code == 302
        assert redirect.headers["location"] == "/"

        followed = client.get(route, follow_redirects=True)
        assert followed.status_code == 200
        assert "text/html" in followed.headers.get("content-type", "")
