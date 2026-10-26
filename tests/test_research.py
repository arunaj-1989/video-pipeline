import hashlib
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    AuditEvent,
    Base,
    Claim,
    ClaimEvidence,
    ResearchRecord,
    ResearchSource,
    Source,
    Topic,
)
from universal_video_maker.errors import ConflictError, NotFoundError
from universal_video_maker.research import (
    CandidateClaim,
    CapturedSource,
    EvidenceDraft,
    ResearchDraft,
    ResearchService,
)

SOURCE_URL = "https://www.isro.gov.in/mission"
SOURCE_CONTENT = b"Aditya-L1 launched on 2 September 2023."
EXCERPT = "Aditya-L1 launched on 2 September 2023."


@pytest.fixture
def database_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def source(
    *,
    url: str = SOURCE_URL,
    content: bytes = SOURCE_CONTENT,
    title: str = "Aditya-L1 mission",
) -> CapturedSource:
    return CapturedSource(
        canonical_url=url,
        title=title,
        publisher="ISRO",
        source_type="official",
        snapshot_bytes=content,
        storage_uri="file:///research-snapshots/aditya-l1.txt",
        published_at=date(2023, 9, 2),
    )


def research_draft(topic_id, *, captured=None, claim=True) -> ResearchDraft:
    source_capture = captured or source()
    candidate_claims = (
        (
            CandidateClaim(
                claim_text="Aditya-L1 launched on 2 September 2023.",
                category="historical_record",
                evidence=(
                    EvidenceDraft(
                        source_url=source_capture.canonical_url,
                        excerpt=EXCERPT,
                        support_type="supports",
                        source_lineage_key="isro-mission-release",
                    ),
                ),
            ),
        )
        if claim
        else ()
    )
    return ResearchDraft(
        topic_id=topic_id,
        sources=(source_capture,),
        findings={"mission": {"name": "Aditya-L1"}, "timeline": ["2023-09-02"]},
        open_questions=("Which instrument observations are appropriate for this episode?",),
        claims=candidate_claims,
    )


def test_research_persists_sources_findings_claims_evidence_and_audit(database_engine) -> None:
    with Session(database_engine) as session:
        topic = Topic(title="Aditya-L1", category="mission", ranking_score=Decimal("80.00"))
        session.add(topic)
        session.flush()

        record = ResearchService(session).create_research(research_draft(topic.id))
        session.commit()

        stored_record = session.get(ResearchRecord, record.id)
        assert stored_record is not None
        assert stored_record.revision == 1
        assert stored_record.status == "draft"
        assert stored_record.findings["mission"]["name"] == "Aditya-L1"
        assert len(stored_record.open_questions) == 1

        stored_source = session.scalar(select(Source))
        assert stored_source is not None
        assert stored_source.content_hash == hashlib.sha256(SOURCE_CONTENT).hexdigest()
        assert stored_source.storage_uri == "file:///research-snapshots/aditya-l1.txt"

        claim = session.scalar(select(Claim))
        assert claim is not None
        assert claim.verification_status == "unverified"
        evidence = session.scalar(select(ClaimEvidence))
        assert evidence is not None
        assert evidence.excerpt == EXCERPT
        assert evidence.support_type == "supports"
        assert evidence.source_lineage_key == "isro-mission-release"
        assert session.scalar(select(func.count()).select_from(ResearchSource)) == 1

        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.object_id == record.id)
        )
        assert audit is not None
        assert audit.action == "research.created"


def test_research_creates_incrementing_revisions_and_reuses_matching_source(
    database_engine,
) -> None:
    with Session(database_engine) as session:
        topic = Topic(title="Aditya-L1", category="mission")
        session.add(topic)
        session.flush()

        first = ResearchService(session).create_research(research_draft(topic.id))
        second = ResearchService(session).create_research(
            research_draft(topic.id, claim=False)
        )
        session.commit()

        assert (first.revision, second.revision) == (1, 2)
        assert session.scalar(select(func.count()).select_from(Source)) == 1
        assert session.scalar(select(func.count()).select_from(ResearchRecord)) == 2


def test_research_rejects_changed_snapshot_for_existing_source_url(database_engine) -> None:
    with Session(database_engine) as session:
        topic = Topic(title="Aditya-L1", category="mission")
        session.add(topic)
        session.flush()
        ResearchService(session).create_research(research_draft(topic.id, claim=False))
        session.commit()

        with pytest.raises(ConflictError, match="content changed"):
            ResearchService(session).create_research(
                research_draft(
                    topic.id,
                    captured=source(content=b"Updated mission page."),
                    claim=False,
                )
            )
        session.rollback()


def test_research_requires_existing_topic(database_engine) -> None:
    with Session(database_engine) as session:
        with pytest.raises(NotFoundError, match="does not exist"):
            ResearchService(session).create_research(research_draft(uuid4()))


def test_research_rejects_evidence_not_found_in_captured_source() -> None:
    with pytest.raises(ValueError, match="not present"):
        ResearchDraft(
            topic_id=uuid4(),
            sources=(source(),),
            findings={},
            claims=(
                CandidateClaim(
                    claim_text="A claim",
                    category="historical_record",
                    evidence=(
                        EvidenceDraft(
                            source_url=SOURCE_URL,
                            excerpt="This text is not in the captured page.",
                            support_type="supports",
                        ),
                    ),
                ),
            ),
        )


def test_research_rejects_evidence_from_unlinked_source() -> None:
    with pytest.raises(ValueError, match="reference a source"):
        ResearchDraft(
            topic_id=uuid4(),
            sources=(source(),),
            findings={},
            claims=(
                CandidateClaim(
                    claim_text="A claim",
                    category="historical_record",
                    evidence=(
                        EvidenceDraft(
                            source_url="https://example.org/source",
                            excerpt=EXCERPT,
                            support_type="context",
                        ),
                    ),
                ),
            ),
        )


@pytest.mark.parametrize(
    ("url", "content", "storage_uri"),
    [
        ("http://example.org/page", SOURCE_CONTENT, "file:///snapshot.txt"),
        ("https://user:secret@example.org/page", SOURCE_CONTENT, "file:///snapshot.txt"),
        (SOURCE_URL, b"", "file:///snapshot.txt"),
        (SOURCE_URL, SOURCE_CONTENT, ""),
        (SOURCE_URL, b"\xff\xfe", "file:///snapshot.txt"),
    ],
)
def test_captured_source_validates_url_snapshot_and_storage(url, content, storage_uri) -> None:
    with pytest.raises(ValueError):
        CapturedSource(
            canonical_url=url,
            title="Source title",
            publisher="Publisher",
            source_type="official",
            snapshot_bytes=content,
            storage_uri=storage_uri,
        )
