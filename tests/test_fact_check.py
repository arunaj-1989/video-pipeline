from datetime import datetime
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
    Source,
    Topic,
    User,
)
from universal_video_maker.errors import ConflictError, ForbiddenError, NotFoundError
from universal_video_maker.fact_check import (
    ClaimCategory,
    FactCheckService,
    ReviewDecision,
)
from universal_video_maker.security import Principal, Role


@pytest.fixture
def database_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def create_claim(
    session: Session,
    evidence_specs: tuple[tuple[str, str, str, str | None], ...],
) -> tuple[Claim, User, Principal]:
    user_id = uuid4()
    topic = Topic(title="Aditya-L1", category="mission")
    user = User(id=user_id, email=f"{user_id}@example.org", role=Role.FACT_REVIEWER.value)
    session.add_all([topic, user])
    session.flush()
    research = ResearchRecord(topic_id=topic.id, revision=1)
    session.add(research)
    session.flush()
    claim = Claim(
        research_record_id=research.id,
        claim_text="A candidate fact about the mission.",
        category="historical_record",
    )
    session.add(claim)
    session.flush()

    for url, publisher, support_type, lineage_key in evidence_specs:
        source = Source(
            canonical_url=url,
            title=f"Source from {publisher}",
            publisher=publisher,
            content_hash=f"hash-{url}",
            source_type="official",
        )
        session.add(source)
        session.flush()
        session.add(
            ClaimEvidence(
                claim_id=claim.id,
                source_id=source.id,
                excerpt="Captured excerpt",
                support_type=support_type,
                source_lineage_key=lineage_key,
            )
        )
    session.flush()
    principal = Principal(
        subject=str(user_id),
        roles=frozenset({Role.FACT_REVIEWER}),
    )
    return claim, user, principal


def test_assessment_groups_same_publisher_or_host_and_flags_unsupported(
    database_engine,
) -> None:
    with Session(database_engine) as session:
        claim, _, _ = create_claim(
            session,
            (
                ("https://isro.gov.in/a", "ISRO", "supports", None),
                ("https://isro.gov.in/b", "ISRO", "supports", None),
                ("https://example.edu/paper", "University", "context", None),
            ),
        )

        assessment = FactCheckService(session).assess_claim(claim.id)

        assert assessment.evidence_count == 3
        assert assessment.supporting_source_count == 2
        assert assessment.independent_supporting_source_count == 1
        assert assessment.context_source_count == 1
        assert assessment.is_unsupported is False
        assert assessment.evidence_sources[0].independence_group == (
            assessment.evidence_sources[1].independence_group
        )


def test_assessment_flags_conflicts_and_claim_with_only_contradiction_is_unsupported(
    database_engine,
) -> None:
    with Session(database_engine) as session:
        claim, _, _ = create_claim(
            session,
            (
                ("https://isro.gov.in/a", "ISRO", "supports", None),
                ("https://research.edu/paper", "Research Institute", "contradicts", None),
            ),
        )
        assessment = FactCheckService(session).assess_claim(claim.id)
        assert assessment.has_conflicting_evidence is True
        assert assessment.is_unsupported is False

        contradiction_only, _, _ = create_claim(
            session,
            (("https://archive.org/record", "Archive", "contradicts", None),),
        )
        contradiction_assessment = FactCheckService(session).assess_claim(
            contradiction_only.id
        )
        assert contradiction_assessment.has_conflicting_evidence is False
        assert contradiction_assessment.is_unsupported is True


def test_declared_lineage_collapses_different_urls_into_one_source_group(
    database_engine,
) -> None:
    with Session(database_engine) as session:
        claim, _, _ = create_claim(
            session,
            (
                ("https://isro.gov.in/release", "ISRO", "supports", "shared-release-1"),
                (
                    "https://news.example.org/syndicated-release",
                    "News Publisher",
                    "supports",
                    "shared-release-1",
                ),
            ),
        )

        assessment = FactCheckService(session).assess_claim(claim.id)

        assert assessment.independent_supporting_source_count == 1
        assert (
            assessment.evidence_sources[0].independence_group
            == assessment.evidence_sources[1].independence_group
        )


def test_human_review_verifies_with_two_independent_sources_and_audits(
    database_engine,
) -> None:
    with Session(database_engine) as session:
        claim, reviewer, principal = create_claim(
            session,
            (
                ("https://isro.gov.in/mission", "ISRO", "supports", "isro-primary"),
                ("https://research.example.edu/paper", "Research Institute", "supports", None),
            ),
        )
        previous_status = claim.verification_status

        reviewed = FactCheckService(session).review_claim(
            principal=principal,
            reviewer_id=reviewer.id,
            claim_id=claim.id,
            decision=ReviewDecision.VERIFIED,
            category=ClaimCategory.HISTORICAL_RECORD,
            note="Both primary records independently support the date.",
        )
        reviewed_at = reviewed.reviewed_at
        assert reviewed_at is not None
        assert reviewed_at.tzinfo is not None
        session.commit()

        assert reviewed.verification_status == "verified"
        assert reviewed.category == "historical_record"
        assert reviewed.reviewer_id == reviewer.id
        assert isinstance(reviewed_at, datetime)
        assert reviewed.review_metadata["evidence_assessment"][
            "independent_supporting_source_count"
        ] == 2
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.object_id == claim.id)
        )
        assert audit is not None
        assert audit.action == "claim.fact_checked"
        assert audit.actor_id == reviewer.id
        assert audit.details["previous_status"] == previous_status
        assert audit.details["evidence_assessment"]["sources"][0]["source_id"]


def test_verification_rejects_dependent_sources(database_engine) -> None:
    with Session(database_engine) as session:
        claim, reviewer, principal = create_claim(
            session,
            (
                ("https://isro.gov.in/a", "ISRO", "supports", None),
                ("https://isro.gov.in/b", "ISRO", "supports", None),
            ),
        )

        with pytest.raises(ConflictError, match="two independent sources"):
            FactCheckService(session).review_claim(
                principal=principal,
                reviewer_id=reviewer.id,
                claim_id=claim.id,
                decision=ReviewDecision.VERIFIED,
                category=ClaimCategory.HISTORICAL_RECORD,
                note="Reviewed.",
            )
        assert claim.verification_status == "unverified"
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_conflicting_evidence_requires_follow_up(database_engine) -> None:
    with Session(database_engine) as session:
        claim, reviewer, principal = create_claim(
            session,
            (
                ("https://isro.gov.in/a", "ISRO", "supports", None),
                ("https://research.example.edu/a", "Research Institute", "supports", None),
                ("https://archive.example.net/a", "Archive", "contradicts", None),
            ),
        )

        with pytest.raises(ConflictError, match="requires follow-up"):
            FactCheckService(session).review_claim(
                principal=principal,
                reviewer_id=reviewer.id,
                claim_id=claim.id,
                decision=ReviewDecision.VERIFIED,
                category=ClaimCategory.HISTORICAL_RECORD,
                note="Reviewed.",
            )


def test_rejection_requires_contradicting_evidence(database_engine) -> None:
    with Session(database_engine) as session:
        claim, reviewer, principal = create_claim(
            session,
            (("https://isro.gov.in/a", "ISRO", "supports", None),),
        )

        with pytest.raises(ConflictError, match="contradicting evidence"):
            FactCheckService(session).review_claim(
                principal=principal,
                reviewer_id=reviewer.id,
                claim_id=claim.id,
                decision=ReviewDecision.REJECTED,
                category=ClaimCategory.REJECTED,
                note="Reviewed.",
            )


def test_review_requires_fact_reviewer_permission_and_matching_identity(database_engine) -> None:
    with Session(database_engine) as session:
        claim, reviewer, _ = create_claim(
            session,
            (("https://research.example.edu/a", "Research Institute", "supports", None),),
        )
        unauthorized = Principal(subject=str(reviewer.id), roles=frozenset({Role.VIEWER}))
        with pytest.raises(ForbiddenError):
            FactCheckService(session).review_claim(
                principal=unauthorized,
                reviewer_id=reviewer.id,
                claim_id=claim.id,
                decision=ReviewDecision.NEEDS_FOLLOW_UP,
                category=ClaimCategory.SCIENTIFIC_THEORY,
                note="Need another source.",
            )

        mismatched_identity = Principal(
            subject=str(uuid4()),
            roles=frozenset({Role.FACT_REVIEWER}),
        )
        with pytest.raises(ForbiddenError, match="identity"):
            FactCheckService(session).review_claim(
                principal=mismatched_identity,
                reviewer_id=reviewer.id,
                claim_id=claim.id,
                decision=ReviewDecision.NEEDS_FOLLOW_UP,
                category=ClaimCategory.SCIENTIFIC_THEORY,
                note="Need another source.",
            )


def test_review_rejects_inactive_reviewer(database_engine) -> None:
    with Session(database_engine) as session:
        claim, reviewer, principal = create_claim(
            session,
            (("https://research.example.edu/a", "Research Institute", "supports", None),),
        )
        reviewer.status = "inactive"

        with pytest.raises(ForbiddenError, match="Inactive"):
            FactCheckService(session).review_claim(
                principal=principal,
                reviewer_id=reviewer.id,
                claim_id=claim.id,
                decision=ReviewDecision.NEEDS_FOLLOW_UP,
                category=ClaimCategory.SCIENTIFIC_THEORY,
                note="Need another source.",
            )


def test_review_requires_note_and_existing_claim(database_engine) -> None:
    with Session(database_engine) as session:
        _, reviewer, principal = create_claim(session, ())
        service = FactCheckService(session)
        with pytest.raises(NotFoundError, match="Claim"):
            service.review_claim(
                principal=principal,
                reviewer_id=reviewer.id,
                claim_id=uuid4(),
                decision=ReviewDecision.NEEDS_FOLLOW_UP,
                category=ClaimCategory.SCIENTIFIC_THEORY,
                note="Need another source.",
            )

        claim = session.scalar(select(Claim))
        assert claim is not None
        with pytest.raises(ValueError, match="note"):
            service.review_claim(
                principal=principal,
                reviewer_id=reviewer.id,
                claim_id=claim.id,
                decision=ReviewDecision.NEEDS_FOLLOW_UP,
                category=ClaimCategory.SCIENTIFIC_THEORY,
                note=" ",
            )


def test_review_rejects_unknown_claim_category() -> None:
    with pytest.raises(ValueError):
        ClaimCategory("made_up")
