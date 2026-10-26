from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    Approval,
    Asset,
    AuditEvent,
    Base,
    Episode,
    EpisodeRevision,
    RenderJob,
    Scene,
    Topic,
    User,
)
from universal_video_maker.errors import ConflictError, ForbiddenError
from universal_video_maker.publishing import PublishingMetadata, PublishingService
from universal_video_maker.security import Principal, Role


@pytest.fixture
def database_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def create_completed_render(session: Session):
    publisher_id = uuid4()
    publisher = User(
        id=publisher_id,
        email=f"{publisher_id}@example.org",
        role=Role.PUBLISHER.value,
    )
    topic = Topic(title="Aditya-L1", category="mission")
    session.add_all([publisher, topic])
    session.flush()
    episode = Episode(topic_id=topic.id, title="A solar observatory", status="draft")
    session.add(episode)
    session.flush()
    storyboard = EpisodeRevision(
        episode_id=episode.id,
        revision=1,
        created_by=publisher_id,
        content={"kind": "storyboard", "scene_count": 1},
    )
    session.add(storyboard)
    session.flush()
    session.add(
        Scene(
            revision_id=storyboard.id,
            scene_number=1,
            content={"visual_description": "Solar observatory"},
        )
    )
    job = RenderJob(
        episode_id=episode.id,
        provider="runpod_serverless",
        status="completed",
        parameters={"storyboard_revision_id": str(storyboard.id)},
    )
    session.add(job)
    session.flush()
    output_variants = [
        ("episode_video", "episode"),
        ("short_video", "short_1"),
        ("short_video", "short_2"),
        ("short_video", "short_3"),
        ("captions", "episode"),
        ("captions", "short_1"),
        ("captions", "short_2"),
        ("captions", "short_3"),
        ("thumbnail", "thumbnail"),
    ]
    for index, (kind, variant) in enumerate(output_variants):
        session.add(
            Asset(
                episode_id=episode.id,
                render_job_id=job.id,
                kind=kind,
                storage_uri=f"s3://universal-video/output-{index}",
                checksum=f"{index + 1:064x}",
                provider=job.provider,
                provenance={"variant": variant},
                validation_status="validated",
            )
        )
    session.flush()
    principal = Principal(str(publisher_id), frozenset({Role.PUBLISHER}))
    return publisher_id, principal, episode, storyboard, job


def metadata() -> PublishingMetadata:
    return PublishingMetadata(
        title="Aditya-L1: Studying the Sun",
        description="Explore India's solar observatory and its mission.",
        language="en",
        tags=("compound interest", "personal finance"),
        hashtags=("#AdityaL1", "#SpaceScience"),
        short_titles=("A solar observatory", "Mission launch", "Studying the Sun"),
        content_warnings=(),
    )


def test_creates_approves_and_records_manual_publication(database_engine) -> None:
    with Session(database_engine) as session:
        publisher_id, principal, episode, storyboard, job = create_completed_render(session)
        service = PublishingService(session)
        package = service.create_package(
            principal=principal,
            publisher_id=publisher_id,
            episode_id=episode.id,
            render_job_id=job.id,
            storyboard_revision_id=storyboard.id,
            metadata=metadata(),
        )
        assert package.status == "draft"
        assert len(package.publishing_metadata["manifest"]) == 9

        package = service.approve_package(
            package.id,
            principal=principal,
            publisher_id=publisher_id,
            note="Reviewed for release.",
        )
        approval = session.scalar(
            select(Approval).where(
                Approval.revision_id == storyboard.id,
                Approval.stage == "release",
            )
        )
        assert package.status == "approved"
        assert approval is not None
        assert approval.reviewer_id == publisher_id

        published_at = datetime.now(UTC)
        package = service.record_publication(
            package.id,
            principal=principal,
            publisher_id=publisher_id,
            external_id="yt-video-123",
            publication_url="https://www.youtube.com/watch?v=yt-video-123",
            published_at=published_at,
        )

        assert package.status == "published"
        assert package.publishing_metadata["release"]["external_id"] == "yt-video-123"
        assert package.publishing_metadata["release"]["state"] == "published"
        assert session.scalar(
            select(AuditEvent).where(
                AuditEvent.object_id == package.id,
                AuditEvent.action == "publishing.publication_recorded",
            )
        ) is not None


def test_release_approval_is_bound_to_immutable_package_fingerprint(database_engine) -> None:
    with Session(database_engine) as session:
        publisher_id, principal, episode, storyboard, job = create_completed_render(session)
        service = PublishingService(session)
        package = service.create_package(
            principal=principal,
            publisher_id=publisher_id,
            episode_id=episode.id,
            render_job_id=job.id,
            storyboard_revision_id=storyboard.id,
            metadata=metadata(),
        )
        service.approve_package(
            package.id,
            principal=principal,
            publisher_id=publisher_id,
        )
        package.publishing_metadata = {
            **package.publishing_metadata,
            "metadata": {**package.publishing_metadata["metadata"], "title": "Changed"},
        }
        session.flush()

        with pytest.raises(ForbiddenError, match="current release approval"):
            service.record_publication(
                package.id,
                principal=principal,
                publisher_id=publisher_id,
                external_id="yt-video-123",
                publication_url="https://www.youtube.com/watch?v=yt-video-123",
                published_at=datetime.now(UTC),
            )


def test_package_requires_complete_validated_assets(database_engine) -> None:
    with Session(database_engine) as session:
        publisher_id, principal, episode, storyboard, job = create_completed_render(session)
        session.query(Asset).filter(Asset.render_job_id == job.id).delete()
        session.flush()

        with pytest.raises(ConflictError, match="complete, validated render manifest"):
            PublishingService(session).create_package(
                principal=principal,
                publisher_id=publisher_id,
                episode_id=episode.id,
                render_job_id=job.id,
                storyboard_revision_id=storyboard.id,
                metadata=metadata(),
            )


def test_publisher_permission_and_metadata_validation(database_engine) -> None:
    with Session(database_engine) as session:
        publisher_id, _, episode, storyboard, job = create_completed_render(session)
        viewer = Principal(str(publisher_id), frozenset({Role.VIEWER}))
        with pytest.raises(ForbiddenError):
            PublishingService(session).create_package(
                principal=viewer,
                publisher_id=publisher_id,
                episode_id=episode.id,
                render_job_id=job.id,
                storyboard_revision_id=storyboard.id,
                metadata=metadata(),
            )

    with pytest.raises(ValueError, match="Hashtags"):
        PublishingMetadata(
            title="Launch",
            description="Description",
            language="en",
            tags=(),
            hashtags=("not-a-hashtag",),
            short_titles=("One", "Two", "Three"),
        )
