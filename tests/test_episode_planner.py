from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    AuditEvent,
    Base,
    Claim,
    ClaimEvidence,
    Episode,
    EpisodeRevision,
    ResearchRecord,
    Source,
    Topic,
    User,
)
from universal_video_maker.episode_planner import (
    EPISODE_DURATION_SECONDS,
    EpisodeBeat,
    EpisodePlanDraft,
    EpisodePlannerService,
    ShortConcept,
)
from universal_video_maker.errors import ConflictError, ForbiddenError, NotFoundError
from universal_video_maker.rag import (
    EvidenceCitation,
    GroundedStatement,
    RetrievedEvidence,
)
from universal_video_maker.security import Principal, Role


@pytest.fixture
def database_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def setup_evidence(session: Session, *, status: str = "verified"):
    planner_id = uuid4()
    topic = Topic(title="Aditya-L1", category="mission")
    planner = User(
        id=planner_id,
        email=f"{planner_id}@example.org",
        role=Role.EDITOR.value,
    )
    session.add_all([topic, planner])
    session.flush()
    research = ResearchRecord(topic_id=topic.id, revision=1)
    session.add(research)
    session.flush()
    claim = Claim(
        research_record_id=research.id,
        claim_text="Aditya-L1 launched on 2 September 2023.",
        category="historical_record",
        verification_status=status,
    )
    source = Source(
        canonical_url="https://isro.gov.in/aditya-l1",
        title="Aditya-L1 mission",
        publisher="ISRO",
        content_hash="sha256:mission",
        source_type="official",
    )
    session.add_all([claim, source])
    session.flush()
    excerpt = "Aditya-L1 launched on 2 September 2023. It studies the Sun."
    evidence = ClaimEvidence(
        claim_id=claim.id,
        source_id=source.id,
        excerpt=excerpt,
        support_type="supports",
    )
    session.add(evidence)
    session.flush()
    context = RetrievedEvidence(
        chunk_id="evidence-aditya-l1",
        claim_id=claim.id,
        source_id=source.id,
        text=excerpt,
        claim_text=claim.claim_text,
        category=claim.category,
        support_type=evidence.support_type,
        source_title=source.title,
        publisher=source.publisher,
        canonical_url=source.canonical_url,
        distance=0.05,
    )
    principal = Principal(subject=str(planner_id), roles=frozenset({Role.EDITOR}))
    return topic, planner, principal, claim, source, evidence, context


def grounded_statement(context: RetrievedEvidence, text: str | None = None) -> GroundedStatement:
    return GroundedStatement(
        text=text or context.claim_text,
        citations=(
            EvidenceCitation(
                chunk_id=context.chunk_id,
                quote="Aditya-L1 launched on 2 September 2023.",
            ),
        ),
    )


def draft_plan(
    topic_id,
    context: RetrievedEvidence,
    *,
    episode_id=None,
    beats: tuple[EpisodeBeat, ...] | None = None,
    shorts: tuple[ShortConcept, ...] | None = None,
) -> EpisodePlanDraft:
    if beats is None:
        beats = (
            EpisodeBeat(
                "The question",
                "Set up the solar mystery",
                0,
                30,
                grounded_statement(context),
            ),
            EpisodeBeat(
                "The journey",
                "Introduce the mission launch",
                30,
                60,
                grounded_statement(context),
            ),
            EpisodeBeat(
                "The observatory",
                "Explain the mission purpose",
                90,
                60,
                grounded_statement(context),
            ),
            EpisodeBeat(
                "The takeaway",
                "Recap what the mission studies",
                150,
                30,
                grounded_statement(context),
            ),
        )
    if shorts is None:
        shorts = (
            ShortConcept(
                "A solar observatory",
                "How do we study the Sun?",
                30,
                grounded_statement(context),
                "Explore the mission.",
            ),
            ShortConcept(
                "Mission launch",
                "When did Aditya-L1 launch?",
                45,
                grounded_statement(context),
                "Discover more space stories.",
            ),
            ShortConcept(
                "The Sun, up close",
                "What can a space observatory reveal?",
                60,
                grounded_statement(context),
                "Learn with Universal Video Maker.",
            ),
        )
    return EpisodePlanDraft(
        topic_id=topic_id,
        title="Aditya-L1: studying our Sun",
        logline="Follow India's solar observatory from launch to its scientific mission.",
        learning_objectives=(
            "Identify Aditya-L1 as a solar observatory.",
            "Recall its launch date.",
        ),
        story_arc=("Question", "Journey", "Discovery and recap"),
        beats=beats,
        shorts=shorts,
        evidence_context=(context,),
        episode_id=episode_id,
    )


def test_create_plan_persists_grounded_outline_revision_and_audit(database_engine) -> None:
    with Session(database_engine) as session:
        topic, planner, principal, _, _, _, context = setup_evidence(session)

        revision = EpisodePlannerService(session).create_plan(
            principal=principal,
            planner_id=planner.id,
            draft=draft_plan(topic.id, context),
        )
        session.commit()

        episode = session.get(Episode, revision.episode_id)
        assert episode is not None
        assert episode.topic_id == topic.id
        assert episode.status == "draft"
        assert episode.title == "Aditya-L1: studying our Sun"
        assert revision.revision == 1
        assert revision.created_by == planner.id
        assert revision.content["kind"] == "episode_plan"
        assert revision.content["target_duration_seconds"] == EPISODE_DURATION_SECONDS
        assert sum(beat["duration_seconds"] for beat in revision.content["beats"]) == 180
        assert len(revision.content["shorts"]) == 3
        assert revision.content["beats"][0]["narration"]["citations"][0]["chunk_id"] == (
            context.chunk_id
        )
        assert revision.content["evidence"][0]["claim_id"] == str(context.claim_id)
        assert "no generated script" in revision.content["limitations"][0]

        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.object_id == revision.id)
        )
        assert audit is not None
        assert audit.actor_id == planner.id
        assert audit.action == "episode.plan_created"


def test_create_plan_appends_revision_to_existing_draft_episode(database_engine) -> None:
    with Session(database_engine) as session:
        topic, planner, principal, _, _, _, context = setup_evidence(session)
        episode = Episode(topic_id=topic.id, title="First title", status="draft")
        session.add(episode)
        session.flush()
        session.add(
            EpisodeRevision(
                episode_id=episode.id,
                revision=1,
                content={"kind": "episode_plan"},
            )
        )
        session.commit()

        revision = EpisodePlannerService(session).create_plan(
            principal=principal,
            planner_id=planner.id,
            draft=draft_plan(topic.id, context, episode_id=episode.id),
        )
        session.commit()

        assert revision.revision == 2
        assert revision.episode_id == episode.id
        assert session.scalar(select(func.count()).select_from(Episode)) == 1


def test_plan_rejects_noncontiguous_or_non_three_minute_beats(database_engine) -> None:
    with Session(database_engine) as session:
        topic, _, _, _, _, _, context = setup_evidence(session)
        invalid_beats = (
            EpisodeBeat("Beat one", "Purpose", 0, 100, grounded_statement(context)),
            EpisodeBeat("Beat two", "Purpose", 101, 80, grounded_statement(context)),
        )
        with pytest.raises(ValueError, match="contiguous"):
            draft_plan(topic.id, context, beats=invalid_beats)

        too_short = (
            EpisodeBeat("One beat", "Purpose", 0, 120, grounded_statement(context)),
        )
        with pytest.raises(ValueError, match="exactly 180"):
            draft_plan(topic.id, context, beats=too_short)


def test_plan_requires_three_shorts_in_the_duration_range(database_engine) -> None:
    with Session(database_engine) as session:
        topic, _, _, _, _, _, context = setup_evidence(session)
        with pytest.raises(ValueError, match="exactly 3"):
            draft_plan(topic.id, context, shorts=())
        invalid_short = ShortConcept(
            "Too short",
            "Hook",
            20,
            grounded_statement(context),
            "Call to action",
        )
        with pytest.raises(ValueError, match="between 30 and 60"):
            draft_plan(topic.id, context, shorts=(invalid_short,) * 3)


def test_plan_requires_citations_and_verbatim_quotes(database_engine) -> None:
    with Session(database_engine) as session:
        topic, _, _, _, _, _, context = setup_evidence(session)
        uncited = GroundedStatement(text="A factual statement.", citations=())
        beats = (
            EpisodeBeat("Beat one", "Purpose", 0, 180, uncited),
        )
        with pytest.raises(ValueError, match="not grounded"):
            draft_plan(topic.id, context, beats=beats)

        fabricated = GroundedStatement(
            text="Aditya-L1 launched in 2024.",
            citations=(EvidenceCitation(context.chunk_id, "Aditya-L1 launched in 2024."),),
        )
        with pytest.raises(ValueError, match="not grounded"):
            draft_plan(
                topic.id,
                context,
                beats=(EpisodeBeat("Beat", "Purpose", 0, 180, fabricated),),
            )


def test_plan_rejects_evidence_that_is_no_longer_verified_or_current(database_engine) -> None:
    with Session(database_engine) as session:
        topic, planner, principal, claim, _, evidence, context = setup_evidence(session)
        claim.verification_status = "needs_follow_up"
        session.commit()

        with pytest.raises(ConflictError, match="no longer current or verified"):
            EpisodePlannerService(session).create_plan(
                principal=principal,
                planner_id=planner.id,
                draft=draft_plan(topic.id, context),
            )
        assert session.scalar(select(func.count()).select_from(Episode)) == 0

        claim.verification_status = "verified"
        evidence.excerpt = "Revised evidence with no original quotation."
        session.commit()
        with pytest.raises(ConflictError, match="no longer current or verified"):
            EpisodePlannerService(session).create_plan(
                principal=principal,
                planner_id=planner.id,
                draft=draft_plan(topic.id, context),
            )
        assert session.scalar(select(func.count()).select_from(Episode)) == 0


def test_plan_requires_editor_permission_active_matching_planner_and_existing_topic(
    database_engine,
) -> None:
    with Session(database_engine) as session:
        topic, planner, principal, _, _, _, context = setup_evidence(session)
        service = EpisodePlannerService(session)
        draft = draft_plan(topic.id, context)

        viewer = Principal(subject=str(planner.id), roles=frozenset({Role.VIEWER}))
        with pytest.raises(ForbiddenError):
            service.create_plan(principal=viewer, planner_id=planner.id, draft=draft)

        mismatched = Principal(subject=str(uuid4()), roles=frozenset({Role.EDITOR}))
        with pytest.raises(ForbiddenError, match="identity"):
            service.create_plan(principal=mismatched, planner_id=planner.id, draft=draft)

        planner.status = "inactive"
        with pytest.raises(ForbiddenError, match="Inactive"):
            service.create_plan(principal=principal, planner_id=planner.id, draft=draft)
        planner.status = "active"

        with pytest.raises(NotFoundError, match="Topic"):
            service.create_plan(
                principal=principal,
                planner_id=planner.id,
                draft=draft_plan(uuid4(), context),
            )


def test_plan_cannot_change_topic_or_replace_non_draft_episode(database_engine) -> None:
    with Session(database_engine) as session:
        topic, planner, principal, _, _, _, context = setup_evidence(session)
        other_topic = Topic(title="Other mission", category="mission")
        session.add(other_topic)
        session.flush()
        episode = Episode(topic_id=topic.id, title="Aditya-L1", status="approved")
        session.add(episode)
        session.flush()
        service = EpisodePlannerService(session)

        with pytest.raises(ConflictError, match="topic cannot be changed"):
            service.create_plan(
                principal=principal,
                planner_id=planner.id,
                draft=draft_plan(other_topic.id, context, episode_id=episode.id),
            )
        with pytest.raises(ConflictError, match="Only draft"):
            service.create_plan(
                principal=principal,
                planner_id=planner.id,
                draft=draft_plan(topic.id, context, episode_id=episode.id),
            )
