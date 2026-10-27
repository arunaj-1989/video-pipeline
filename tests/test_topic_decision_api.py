from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tests.test_claims_episodes_api import HEADERS, SubjectVerifier
from universal_video_maker.api import create_app
from universal_video_maker.config import Settings
from universal_video_maker.db.models import AuditEvent, Base, Topic
from universal_video_maker.security import Role


def make_client(tmp_path: Path, role: Role) -> tuple[TestClient, str, str]:
    url = f"sqlite:///{tmp_path / 'topics.sqlite'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        proposed = Topic(title="Solar", category="Science", status="proposed")
        done = Topic(title="Tides", category="Science", status="ready_to_plan")
        session.add_all([proposed, done])
        session.commit()
        ids = str(proposed.id), str(done.id)
    engine.dispose()
    app = create_app(
        Settings(_env_file=None, database_url=url),
        token_verifier=SubjectVerifier(frozenset({role})),
    )
    return TestClient(app), *ids


def test_editor_approves_topic_and_audit_is_written(tmp_path: Path) -> None:
    client, topic_id, _ = make_client(tmp_path, Role.EDITOR)
    with client:
        response = client.post(
            f"/api/v1/topics/{topic_id}/decision",
            headers=HEADERS,
            json={"decision": "approve", "note": "Good fit."},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "ready_to_plan"

    engine = create_engine(f"sqlite:///{tmp_path / 'topics.sqlite'}")
    with Session(engine) as session:
        event = session.scalars(select(AuditEvent)).one()
        assert event.action == "topic.approved"
        assert event.details["note"] == "Good fit."
    engine.dispose()


def test_reject_sets_rejected(tmp_path: Path) -> None:
    client, topic_id, _ = make_client(tmp_path, Role.EDITOR)
    with client:
        response = client.post(
            f"/api/v1/topics/{topic_id}/decision",
            headers=HEADERS,
            json={"decision": "reject", "note": "Off topic."},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_already_decided_topic_conflicts(tmp_path: Path) -> None:
    client, _, decided_id = make_client(tmp_path, Role.EDITOR)
    with client:
        response = client.post(
            f"/api/v1/topics/{decided_id}/decision",
            headers=HEADERS,
            json={"decision": "approve", "note": "Again."},
        )
    assert response.status_code == 409


def test_viewer_forbidden(tmp_path: Path) -> None:
    client, topic_id, _ = make_client(tmp_path, Role.VIEWER)
    with client:
        forbidden = client.post(
            f"/api/v1/topics/{topic_id}/decision",
            headers=HEADERS,
            json={"decision": "approve", "note": "x"},
        )
    assert forbidden.status_code == 403


def test_blank_note_rejected(tmp_path: Path) -> None:
    client, topic_id, _ = make_client(tmp_path, Role.EDITOR)
    with client:
        blank = client.post(
            f"/api/v1/topics/{topic_id}/decision",
            headers=HEADERS,
            json={"decision": "approve", "note": "  "},
        )
    assert blank.status_code == 422
