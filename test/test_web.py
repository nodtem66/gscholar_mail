"""Tests for dashboard authentication and login throttling."""

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from gscholar_mail.database import PUBLICATIONS_SCHEMA


def make_database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(PUBLICATIONS_SCHEMA)
    return path


def test_dashboard_requires_login_and_allows_logout(
    tmp_path: Path, monkeypatch
) -> None:
    from gscholar_mail.web import create_app

    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    client = TestClient(create_app(make_database(tmp_path / "publications.db")))

    response = client.get("/publications", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    response = client.post("/login", data={"password": "test-password"})
    assert response.status_code == 200
    assert "Publications" in response.text
    assert "dashboard_session" in client.cookies

    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert "dashboard_session" not in client.cookies
    assert (
        client.get("/publications", follow_redirects=False).headers["location"]
        == "/login"
    )


def test_login_cooldown_after_five_failures(tmp_path: Path, monkeypatch) -> None:
    from gscholar_mail.web import create_app

    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password")
    client = TestClient(create_app(make_database(tmp_path / "publications.db")))

    for _ in range(4):
        response = client.post("/login", data={"password": "wrong"})
        assert response.status_code == 200

    response = client.post("/login", data={"password": "wrong"})
    assert response.status_code == 429
    assert response.headers["retry-after"]

    response = client.post("/login", data={"password": "test-password"})
    assert response.status_code == 429
