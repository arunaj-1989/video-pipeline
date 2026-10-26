from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    AuditEvent,
    Base,
    Claim,
    ClaimEvidence,
    Episode,
    EpisodeRevision,
    ResearchRecord,
    Scene,
    Source,
    Topic,
    User,
)
from universal_video_maker.errors import ConflictError, ForbiddenError
from universal_video_maker.security import Principal, Role
from universal_video_maker.storyboard import (
    StoryboardDraft,
    StoryboardSceneDraft,
    StoryboardService,
)


@pytest.fixture
def database_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def create_episode_plan(session: Session):
    editor_id = uuid4()
    user = User(
        id=editor_id,
        email=f"{editor_id}@example.org",
        role=Role.EDITOR.value,
    )
    topic = Topic(title="Aditya-L1", category="mission")
    session.add_all([user, topic])
    session.flush()

    episode = Episode(topic_id=topic.id, title="A solar observatory", status="draft")
    session.add(episode)
    session.flush()
    research = ResearchRecord(topic_id=topic.id, revision=1)
    session.add(research)
    session.flush()
    claim = Claim(
        research_record_id=research.id,
        claim_text="Aditya-L1 launched on 2 September 2023.",
        category="historical_record",
        verification_status="verified",
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

    chunk_id = "evidence-aditya-l1"
    plan = EpisodeRevision(
        episode_id=episode.id,
        revision=1,
        created_by=editor_id,
        content={
            "kind": "episode_plan",
            "target_duration_seconds": 180,
            "beats": [
                {
                    "title": "The mission",
                    "purpose": "Introduce the solar observatory.",
                    "start_seconds": 0,
                    "duration_seconds": 180,
                    "narration": {
                        "text": claim.claim_text,
                        "citations": [
                            {
                                "chunk_id": chunk_id,
                                "quote": claim.claim_text,
                            }
                        ],
                    },
                }
            ],
            "evidence": [
                {
                    "chunk_id": chunk_id,
                    "claim_id": str(claim.id),
                    "source_id": str(source.id),
                    "text": excerpt,
                    "claim_text": claim.claim_text,
                    "category": claim.category,
                    "source_title": source.title,
                    "publisher": source.publisher,
                    "canonical_url": source.canonical_url,
                    "support_type": evidence.support_type,
                }
            ],
        },
    )
    session.add(plan)
    session.flush()
    principal = Principal(
        subject=str(editor_id),
        roles=frozenset({Role.EDITOR}),
    )
    return editor_id, principal, episode, plan, claim


def sample_draft(plan: EpisodeRevision) -> StoryboardDraft:
    return StoryboardDraft(
        plan_revision_id=plan.id,
        scenes=(
            StoryboardSceneDraft(
                visual_description="A spacecraft above Earth at sunrise.",
                camera_direction="Slow orbital push-in.",
                visual_prompt="Educational 2D illustration, clean scientific style.",
                required_assets=("spacecraft reference", "Earth background"),
            ),
        ),
    )


def test_create_storyboard_inherits_plan_timing_and_narration_and_audits(
    database_engine,
) -> None:
    with Session(database_engine) as session:
        editor_id, principal, episode, plan, _ = create_episode_plan(session)

        revision = StoryboardService(session).create_storyboard(
            principal=principal,
            editor_id=editor_id,
            draft=sample_draft(plan),
        )
        session.flush()
        scene = session.scalar(select(Scene).where(Scene.revision_id == revision.id))
        audit = session.scalar(
            select(AuditEvent).where(AuditEvent.object_id == revision.id)
        )

        assert revision.episode_id == episode.id
        assert revision.revision == 2
        assert revision.content["kind"] == "storyboard"
        assert revision.content["source_plan_revision_id"] == str(plan.id)
        assert scene is not None
        assert scene.scene_number == 1
        assert scene.content["start_seconds"] == 0
        assert scene.content["duration_seconds"] == 180
        assert scene.content["narration"] == plan.content["beats"][0]["narration"]
        assert scene.content["required_assets"] == [
            "spacecraft reference",
            "Earth background",
        ]
        assert audit is not None
        assert audit.action == "episode.storyboard_created"


def test_storyboard_rejects_stale_or_unverified_plan_evidence(database_engine) -> None:
    with Session(database_engine) as session:
        editor_id, principal, _, plan, claim = create_episode_plan(session)
        claim.verification_status = "rejected"
        session.flush()

        with pytest.raises(ConflictError, match="no longer verified"):
            StoryboardService(session).create_storyboard(
                principal=principal,
                editor_id=editor_id,
                draft=sample_draft(plan),
            )


def test_storyboard_requires_latest_episode_revision(database_engine) -> None:
    with Session(database_engine) as session:
        editor_id, principal, episode, plan, _ = create_episode_plan(session)
        session.add(
            EpisodeRevision(
                episode_id=episode.id,
                revision=2,
                content={"kind": "other_revision"},
            )
        )
        session.flush()

        with pytest.raises(ConflictError, match="latest episode revision"):
            StoryboardService(session).create_storyboard(
                principal=principal,
                editor_id=editor_id,
                draft=sample_draft(plan),
            )


def test_storyboard_checks_editor_permission_and_identity(database_engine) -> None:
    with Session(database_engine) as session:
        editor_id, principal, _, plan, _ = create_episode_plan(session)
        service = StoryboardService(session)
        draft = sample_draft(plan)

        with pytest.raises(ForbiddenError):
            service.create_storyboard(
                principal=Principal(
                    subject=principal.subject,
                    roles=frozenset({Role.VIEWER}),
                ),
                editor_id=editor_id,
                draft=draft,
            )
        with pytest.raises(ForbiddenError, match="identity"):
            service.create_storyboard(
                principal=principal,
                editor_id=uuid4(),
                draft=draft,
            )


def test_storyboard_requires_one_scene_per_plan_beat(database_engine) -> None:
    with Session(database_engine) as session:
        _, _, _, plan, _ = create_episode_plan(session)

        with pytest.raises(ValueError, match="at least one scene"):
            StoryboardDraft(
                plan_revision_id=plan.id,
                scenes=(),
            )


def test_scene_schema_rejects_empty_or_duplicate_required_assets() -> None:
    with pytest.raises(ValueError, match="Visual prompt"):
        StoryboardSceneDraft(
            visual_description="A spacecraft.",
            camera_direction="Static shot.",
            visual_prompt=" ",
        )
    with pytest.raises(ValueError, match="duplicates"):
        StoryboardSceneDraft(
            visual_description="A spacecraft.",
            camera_direction="Static shot.",
            visual_prompt="An educational illustration.",
            required_assets=("Earth background", "earth BACKGROUND"),
        )
