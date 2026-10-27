from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tests.test_claims_episodes_api import HEADERS, SubjectVerifier
from tests.test_episode_planner import draft_plan, setup_evidence
from universal_video_maker.api import create_app
from universal_video_maker.config import Settings
from universal_video_maker.db.models import AuditEvent, Base, Episode, EpisodeRevision
from universal_video_maker.episode_generation import NoGroundedEvidenceError
from universal_video_maker.episode_planner import EpisodePlanDraft
from universal_video_maker.providers.ollama import OllamaClientError
from universal_video_maker.security import Role


class FakeGenerator:
    def __init__(self, draft: EpisodePlanDraft | None, error: Exception | None) -> None:
        self._draft = draft
        self._error = error

    def generate(self, topic_id: UUID, *, query: str | None = None) -> EpisodePlanDraft:
        if self._error is not None:
            raise self._error
        assert self._draft is not None
        return self._draft


def make_client(
    tmp_path: Path,
    role: Role = Role.EDITOR,
    *,
    topic_status: str = "ready_to_plan",
    error: Exception | None = None,
) -> tuple[TestClient, str]:
    url = f"sqlite:///{tmp_path / 'plans.sqlite'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        topic, planner, _, _, _, _, context = setup_evidence(session)
        topic.status = topic_status
        draft = draft_plan(topic.id, context)
        session.commit()
        topic_id, planner_id = str(topic.id), str(planner.id)
    engine.dispose()
    app = create_app(
        Settings(_env_file=None, database_url=url),
        token_verifier=SubjectVerifier(frozenset({role}), subject=planner_id),
        plan_generator_factory=lambda session: FakeGenerator(draft, error),
    )
    return TestClient(app), topic_id


def test_editor_generates_plan_and_can_read_it_back(tmp_path: Path) -> None:
    client, topic_id = make_client(tmp_path)
    with client:
        created = client.post("/api/v1/episodes/plan", headers=HEADERS, json={"topic_id": topic_id})
        assert created.status_code == 201
        body = created.json()
        assert body["revision"] == 1
        assert len(body["plan"]["beats"]) == 4
        assert len(body["plan"]["shorts"]) == 3

        fetched = client.get(f"/api/v1/episodes/{body['episode_id']}/plan", headers=HEADERS)
        assert fetched.json()["plan"]["title"] == "Aditya-L1: studying our Sun"
        assert client.get("/api/v1/episodes", headers=HEADERS).json()[0]["latest_revision"] == 1

    engine = create_engine(f"sqlite:///{tmp_path / 'plans.sqlite'}")
    with Session(engine) as session:
        actions = set(session.scalars(select(AuditEvent.action)))
        assert "episode.plan_created" in actions
    engine.dispose()


def test_replanning_adds_revision_to_the_same_draft_episode(tmp_path: Path) -> None:
    client, topic_id = make_client(tmp_path)
    with client:
        first = client.post("/api/v1/episodes/plan", headers=HEADERS, json={"topic_id": topic_id})
        second = client.post("/api/v1/episodes/plan", headers=HEADERS, json={"topic_id": topic_id})
    assert second.json()["episode_id"] == first.json()["episode_id"]
    assert second.json()["revision"] == 2

    engine = create_engine(f"sqlite:///{tmp_path / 'plans.sqlite'}")
    with Session(engine) as session:
        assert len(session.scalars(select(Episode)).all()) == 1
        assert len(session.scalars(select(EpisodeRevision)).all()) == 2
    engine.dispose()


def test_topic_must_be_approved_first(tmp_path: Path) -> None:
    client, topic_id = make_client(tmp_path, topic_status="proposed")
    with client:
        response = client.post(
            "/api/v1/episodes/plan", headers=HEADERS, json={"topic_id": topic_id}
        )
    assert response.status_code == 409


def test_missing_evidence_is_a_conflict(tmp_path: Path) -> None:
    client, topic_id = make_client(tmp_path, error=NoGroundedEvidenceError("none"))
    with client:
        response = client.post(
            "/api/v1/episodes/plan", headers=HEADERS, json={"topic_id": topic_id}
        )
    assert response.status_code == 409
    assert "verified evidence" in response.json()["error"]["message"]


def test_ollama_failure_is_service_unavailable(tmp_path: Path) -> None:
    client, topic_id = make_client(tmp_path, error=OllamaClientError("down"))
    with client:
        response = client.post(
            "/api/v1/episodes/plan", headers=HEADERS, json={"topic_id": topic_id}
        )
    assert response.status_code == 503
    assert "Ollama" in response.json()["error"]["message"]


def test_viewer_cannot_plan_and_unknown_plan_is_404(tmp_path: Path) -> None:
    client, topic_id = make_client(tmp_path, Role.VIEWER)
    with client:
        forbidden = client.post(
            "/api/v1/episodes/plan", headers=HEADERS, json={"topic_id": topic_id}
        )
        missing = client.get(f"/api/v1/episodes/{topic_id}/plan", headers=HEADERS)
    assert forbidden.status_code == 403
    assert missing.status_code == 404
