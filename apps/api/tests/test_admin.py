from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

BAD_DOC = {"documents": [{"name": "broken.md", "content": "no frontmatter here"}]}


@pytest.fixture
def admin_token(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("ADMIN_TOKEN", "test-token")
    get_settings.cache_clear()
    yield "test-token"
    get_settings.cache_clear()


def test_admin_is_hidden_when_no_token_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "")  # env beats any local .env file
    get_settings.cache_clear()
    response = TestClient(app).post("/admin/ingest", json=BAD_DOC)
    get_settings.cache_clear()
    assert response.status_code == 404


def test_wrong_token_is_rejected(admin_token: str) -> None:
    response = TestClient(app).post("/admin/ingest", json=BAD_DOC, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_invalid_document_is_rejected_before_any_database_work(admin_token: str) -> None:
    response = TestClient(app).post(
        "/admin/ingest", json=BAD_DOC, headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 422
    assert "frontmatter" in response.text


def test_document_names_are_validated(admin_token: str) -> None:
    body = {"documents": [{"name": "../etc/passwd", "content": "x"}]}
    response = TestClient(app).post(
        "/admin/ingest", json=body, headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 422
