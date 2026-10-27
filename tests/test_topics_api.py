from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from universal_video_maker.api import create_app
from universal_video_maker.config import Settings
from universal_video_maker.db.models import Base
from universal_video_maker.identity import AccessTokenError
from universal_video_maker.security import Principal, Role


class StaticTokenVerifier:
    def __init__(self, roles: frozenset[Role]) -> None:
        self.roles = roles

    def verify(self, token: str) -> Principal:
        if token != "valid-token":
            raise AccessTokenError("****** is invalid or expired.")
        return Principal("keycloak-user-1", self.roles)


@pytest.fixture
def topic_client(tmp_path: Path):
    database_path = tmp_path / "topics.sqlite"
    setup_engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(setup_engine)
    setup_engine.dispose()

    app = create_app(
        Settings(_env_file=None, database_url=f"sqlite:///{database_path}"),
        token_verifier=StaticTokenVerifier(frozenset({Role.EDITOR})),
    )
    with TestClient(app) as client:
        yield client


def test_topic_endpoints_require_bearer_authentication(topic_client: TestClient) -> None:
    response = topic_client.get("/api/v1/topics")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_editor_can_create_and_list_persisted_topics(topic_client: TestClient) -> None:
    headers = {"Authorization": "Bearer valid-token"}
    created = topic_client.post(
        "/api/v1/topics",
        headers=headers,
        json={"title": "How solar panels work", "category": "Science"},
    )

    assert created.status_code == 201
    assert created.json()["title"] == "How solar panels work"
    assert created.json()["category"] == "Science"
    assert created.json()["status"] == "proposed"

    listed = topic_client.get("/api/v1/topics", headers=headers)
    assert listed.status_code == 200
    assert [topic["id"] for topic in listed.json()] == [created.json()["id"]]


def test_viewer_can_read_but_cannot_create_topics(tmp_path: Path) -> None:
    database_path = tmp_path / "viewer-topics.sqlite"
    setup_engine = create_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(setup_engine)
    setup_engine.dispose()
    app = create_app(
        Settings(_env_file=None, database_url=f"sqlite:///{database_path}"),
        token_verifier=StaticTokenVerifier(frozenset({Role.VIEWER})),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/topics",
            headers={"Authorization": "Bearer valid-token"},
            json={"title": "Not permitted"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_topic_creation_rejects_blank_title(topic_client: TestClient) -> None:
    response = topic_client.post(
        "/api/v1/topics",
        headers={"Authorization": "Bearer valid-token"},
        json={"title": "   "},
    )

    assert response.status_code == 422
