from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tests.test_topics_api import StaticTokenVerifier
from universal_video_maker.api import create_app
from universal_video_maker.config import Settings
from universal_video_maker.db.models import (
    AuditEvent,
    Base,
    Claim,
    ClaimEvidence,
    Episode,
    EpisodeRevision,
    RenderJob,
    ResearchRecord,
    Source,
    Topic,
    User,
)
from universal_video_maker.security import Principal, Role

HEADERS = {"Authorization": "Bearer valid-token"}
REVIEWER_ID = str(uuid4())


class SubjectVerifier(StaticTokenVerifier):
    def __init__(self, roles: frozenset[Role], subject: str = REVIEWER_ID) -> None:
        super().__init__(roles)
        self.subject = subject

    def verify(self, token: str) -> Principal:
        principal = super().verify(token)
        return Principal(self.subject, principal.roles, "reviewer@example.org")


def make_client(tmp_path: Path, role: Role, independent: bool = True) -> tuple[TestClient, str]:
    url = f"sqlite:///{tmp_path / 'claims.sqlite'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        topic = Topic(title="Solar", category="Science", status="proposed")
        session.add(topic)
        session.flush()
        record = ResearchRecord(topic_id=topic.id)
        session.add(record)
        session.flush()
        claim = Claim(
            research_record_id=record.id, claim_text="The Sun is a star.", category="verified"
        )
        session.add(claim)
        session.flush()
        for index, publisher in enumerate(("NASA", "ESA" if independent else "NASA")):
            source = Source(
                canonical_url=f"https://{'a' if index == 0 else 'b'}.example/{index}"
                if independent
                else f"https://same.example/{index}",
                title=f"Source {index}",
                publisher=publisher,
                content_hash=f"hash{index}",
                source_type="article",
            )
            session.add(source)
            session.flush()
            session.add(
                ClaimEvidence(
                    claim_id=claim.id, source_id=source.id, excerpt="x", support_type="supports"
                )
            )
        episode = Episode(topic_id=topic.id, title="Solar episode")
        session.add(episode)
        session.flush()
        session.add(EpisodeRevision(episode_id=episode.id, revision=2, content={}))
        session.add(RenderJob(episode_id=episode.id, provider="runpod", status="running"))
        session.commit()
        claim_id = str(claim.id)
    engine.dispose()
    app = create_app(
        Settings(_env_file=None, database_url=url),
        token_verifier=SubjectVerifier(frozenset({role})),
    )
    return TestClient(app), claim_id


REVIEW = {"decision": "verified", "category": "verified", "note": "Checked both sources."}


def test_claims_list_includes_evidence_assessment(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, Role.VIEWER)
    with client:
        claims = client.get("/api/v1/claims", headers=HEADERS).json()

    assert claims[0]["topic_title"] == "Solar"
    assert claims[0]["independent_supporting_source_count"] == 2
    assert len(claims[0]["sources"]) == 2


def test_fact_reviewer_verifies_claim_and_user_row_is_created(tmp_path: Path) -> None:
    client, claim_id = make_client(tmp_path, Role.FACT_REVIEWER)
    with client:
        response = client.post(f"/api/v1/claims/{claim_id}/review", headers=HEADERS, json=REVIEW)
        assert response.status_code == 200
        assert response.json()["verification_status"] == "verified"
        assert response.json()["review_note"] == "Checked both sources."

    engine = create_engine(f"sqlite:///{tmp_path / 'claims.sqlite'}")
    with Session(engine) as session:
        user = session.scalars(select(User)).one()
        assert str(user.id) == REVIEWER_ID
        assert user.role == "fact_reviewer"
        assert user.email == "reviewer@example.org"
        assert session.scalars(select(AuditEvent)).one().action == "claim.fact_checked"
    engine.dispose()


def test_verification_accepts_a_single_supporting_source(tmp_path: Path) -> None:
    client, claim_id = make_client(tmp_path, Role.FACT_REVIEWER, independent=False)
    with client:
        response = client.post(f"/api/v1/claims/{claim_id}/review", headers=HEADERS, json=REVIEW)

    assert response.status_code == 200


def test_editor_cannot_review_claims(tmp_path: Path) -> None:
    client, claim_id = make_client(tmp_path, Role.EDITOR)
    with client:
        response = client.post(f"/api/v1/claims/{claim_id}/review", headers=HEADERS, json=REVIEW)

    assert response.status_code == 403


def test_review_rejects_blank_note_and_unknown_claim(tmp_path: Path) -> None:
    client, claim_id = make_client(tmp_path, Role.FACT_REVIEWER)
    with client:
        blank = client.post(
            f"/api/v1/claims/{claim_id}/review", headers=HEADERS, json={**REVIEW, "note": "  "}
        )
        missing = client.post(f"/api/v1/claims/{uuid4()}/review", headers=HEADERS, json=REVIEW)

    assert blank.status_code == 422
    assert missing.status_code == 404


def test_non_uuid_subject_cannot_review(tmp_path: Path) -> None:
    client, claim_id = make_client(tmp_path, Role.FACT_REVIEWER)
    client.app.state.token_verifier = SubjectVerifier(frozenset({Role.FACT_REVIEWER}), "not-a-uuid")
    with client:
        response = client.post(f"/api/v1/claims/{claim_id}/review", headers=HEADERS, json=REVIEW)

    assert response.status_code == 403


def test_episode_list_summarises_revisions_and_renders(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, Role.VIEWER)
    with client:
        episodes = client.get("/api/v1/episodes", headers=HEADERS).json()

    assert episodes[0]["title"] == "Solar episode"
    assert episodes[0]["topic_title"] == "Solar"
    assert episodes[0]["latest_revision"] == 2
    assert episodes[0]["latest_render_status"] == "running"


def test_episodes_and_claims_require_authentication(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, Role.VIEWER)
    with client:
        assert client.get("/api/v1/claims").status_code == 401
        assert client.get("/api/v1/episodes").status_code == 401


def _episode_id(client: TestClient) -> str:
    return client.get("/api/v1/episodes", headers=HEADERS).json()[0]["id"]


def test_editor_records_plan_approval_on_latest_revision(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, Role.EDITOR)
    with client:
        episode_id = _episode_id(client)
        response = client.post(
            f"/api/v1/episodes/{episode_id}/approvals",
            headers=HEADERS,
            json={"decision": "approved", "note": "  Plan is grounded.  "},
        )
        assert response.status_code == 201
        body = response.json()
        assert (body["revision"], body["stage"], body["note"]) == (2, "plan", "Plan is grounded.")

        approvals = client.get("/api/v1/governance/approvals", headers=HEADERS).json()
        assert [item["decision"] for item in approvals] == ["approved"]
        assert client.get("/api/v1/episodes", headers=HEADERS).json()[0]["approvals"] == 1

    engine = create_engine(f"sqlite:///{tmp_path / 'claims.sqlite'}")
    with Session(engine) as session:
        assert "episode.plan_approved" in set(session.scalars(select(AuditEvent.action)))
    engine.dispose()


def test_approval_requires_permission_note_and_known_episode(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, Role.EDITOR)
    with client:
        episode_id = _episode_id(client)
        blank = client.post(
            f"/api/v1/episodes/{episode_id}/approvals",
            headers=HEADERS,
            json={"decision": "approved", "note": "   "},
        )
        bad_decision = client.post(
            f"/api/v1/episodes/{episode_id}/approvals",
            headers=HEADERS,
            json={"decision": "maybe", "note": "ok"},
        )
        missing = client.post(
            f"/api/v1/episodes/{uuid4()}/approvals",
            headers=HEADERS,
            json={"decision": "approved", "note": "ok"},
        )
    assert blank.status_code == 422
    assert bad_decision.status_code == 422
    assert missing.status_code == 404

    (tmp_path / "v").mkdir()
    viewer, _ = make_client(tmp_path / "v", Role.VIEWER)
    with viewer:
        forbidden = viewer.post(
            f"/api/v1/episodes/{uuid4()}/approvals",
            headers=HEADERS,
            json={"decision": "approved", "note": "ok"},
        )
    assert forbidden.status_code == 403
