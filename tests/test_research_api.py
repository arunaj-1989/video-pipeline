from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tests.test_claims_episodes_api import HEADERS, SubjectVerifier
from universal_video_maker.api import create_app
from universal_video_maker.config import Settings
from universal_video_maker.db.models import Base, Claim, ResearchRecord, Topic
from universal_video_maker.providers.tavily import TavilyClientError
from universal_video_maker.research import (
    CandidateClaim,
    CapturedSource,
    EvidenceDraft,
    ResearchDraft,
    ResearchService,
)
from universal_video_maker.security import Role

EXCERPT = "The Sun is a star at the center of the Solar System."


class FakeWorkflow:
    def __init__(self, session: Session, error: Exception | None) -> None:
        self._session = session
        self._error = error

    def create_research_draft(self, topic_id: UUID) -> ResearchRecord:
        if self._error is not None:
            raise self._error
        source = CapturedSource(
            canonical_url="https://example.org/sun",
            title="The Sun",
            publisher="Example",
            source_type="article",
            snapshot_bytes=EXCERPT.encode(),
            storage_uri="file:///tmp/sun.txt",
        )
        draft = ResearchDraft(
            topic_id=topic_id,
            sources=(source,),
            findings={"provider": "fake"},
            open_questions=("Is there a second source?",),
            claims=(
                CandidateClaim(
                    claim_text="The Sun is a star.",
                    category="verified",
                    evidence=(EvidenceDraft("https://example.org/sun", EXCERPT, "supports"),),
                ),
            ),
        )
        return ResearchService(self._session).create_research(draft)


def make_client(
    tmp_path: Path,
    role: Role = Role.EDITOR,
    *,
    topic_status: str = "ready_to_plan",
    error: Exception | None = None,
) -> tuple[TestClient, str]:
    url = f"sqlite:///{tmp_path / 'research.sqlite'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        topic = Topic(title="Solar", category="Science", status=topic_status)
        session.add(topic)
        session.commit()
        topic_id = str(topic.id)
    engine.dispose()
    app = create_app(
        Settings(_env_file=None, database_url=url),
        token_verifier=SubjectVerifier(frozenset({role})),
        research_workflow_factory=lambda session: FakeWorkflow(session, error),
    )
    return TestClient(app), topic_id


def test_research_creates_unverified_claims_visible_to_fact_check(tmp_path: Path) -> None:
    client, topic_id = make_client(tmp_path)
    with client:
        response = client.post("/api/v1/research", headers=HEADERS, json={"topic_id": topic_id})
        assert response.status_code == 201
        body = response.json()
        assert body["claim_count"] == 1
        assert body["source_count"] == 1
        assert body["open_questions"] == ["Is there a second source?"]
        claims = client.get("/api/v1/claims", headers=HEADERS).json()
        assert claims[0]["verification_status"] == "unverified"

    engine = create_engine(f"sqlite:///{tmp_path / 'research.sqlite'}")
    with Session(engine) as session:
        assert session.scalars(select(Claim.verification_status)).one() == "unverified"
    engine.dispose()


def test_topic_must_be_approved_first(tmp_path: Path) -> None:
    client, topic_id = make_client(tmp_path, topic_status="proposed")
    with client:
        response = client.post("/api/v1/research", headers=HEADERS, json={"topic_id": topic_id})
    assert response.status_code == 409


def test_provider_failure_is_service_unavailable(tmp_path: Path) -> None:
    client, topic_id = make_client(tmp_path, error=TavilyClientError("down"))
    with client:
        response = client.post("/api/v1/research", headers=HEADERS, json={"topic_id": topic_id})
    assert response.status_code == 503


def test_viewer_forbidden(tmp_path: Path) -> None:
    client, topic_id = make_client(tmp_path, Role.VIEWER)
    with client:
        response = client.post("/api/v1/research", headers=HEADERS, json={"topic_id": topic_id})
    assert response.status_code == 403


def test_missing_search_key_gives_clear_message(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'nokey.sqlite'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        topic = Topic(title="Solar", category="Science", status="ready_to_plan")
        session.add(topic)
        session.commit()
        topic_id = str(topic.id)
    engine.dispose()
    app = create_app(
        Settings(_env_file=None, database_url=url),
        token_verifier=SubjectVerifier(frozenset({Role.EDITOR})),
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/research", headers=HEADERS, json={"topic_id": topic_id})
    assert response.status_code == 503
    assert "UVM_TAVILY_API_KEY" in response.json()["error"]["message"]
