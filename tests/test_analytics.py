from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from universal_video_maker.analytics import AnalyticsService, AnalyticsSnapshot
from universal_video_maker.db.models import (
    Asset,
    Base,
    Episode,
    EpisodeRevision,
    PublishingPackage,
    RenderJob,
    Scene,
    Topic,
    User,
)
from universal_video_maker.errors import ConflictError, ForbiddenError
from universal_video_maker.security import Principal, Role


@pytest.fixture
def database_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def create_published_package(session: Session):
    publisher_id = uuid4()
    publisher = User(
        id=publisher_id,
        email=f"{publisher_id}@example.org",
        role=Role.PUBLISHER.value,
    )
    topic = Topic(title="Aditya-L1", category="mission")
    session.add_all([publisher, topic])
    session.flush()
    episode = Episode(topic_id=topic.id, title="A solar observatory", status="published")
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
            content={"narration": {"citations": [{"chunk_id": "evidence-1"}]}},
        )
    )
    job = RenderJob(
        episode_id=episode.id,
        provider="runpod_serverless",
        status="completed",
        estimated_cost=Decimal("4.00"),
        actual_cost=Decimal("3.00"),
        parameters={"storyboard_revision_id": str(storyboard.id)},
    )
    session.add(job)
    session.flush()
    variants = [
        ("episode_video", "episode", 180.0),
        ("short_video", "short_1", 30.0),
        ("short_video", "short_2", 45.0),
        ("short_video", "short_3", 60.0),
        ("captions", "episode", None),
        ("captions", "short_1", None),
        ("captions", "short_2", None),
        ("captions", "short_3", None),
        ("thumbnail", "thumbnail", None),
    ]
    manifest = []
    for index, (kind, variant, duration) in enumerate(variants):
        asset = Asset(
            episode_id=episode.id,
            render_job_id=job.id,
            kind=kind,
            storage_uri=f"s3://universal-video/output-{index}",
            checksum=f"{index + 1:064x}",
            provider=job.provider,
            provenance={"variant": variant, "duration_seconds": duration},
            validation_status="validated",
        )
        session.add(asset)
        session.flush()
        manifest.append(
            {
                "asset_id": str(asset.id),
                "kind": kind,
                "variant": variant,
                "checksum": asset.checksum,
                "storage_uri": asset.storage_uri,
            }
        )
    published_at = datetime.now(UTC) - timedelta(days=2)
    package = PublishingPackage(
        episode_id=episode.id,
        status="published",
        publishing_metadata={
            "schema_version": 1,
            "metadata": {"platform": "youtube"},
            "manifest": manifest,
            "render_job_id": str(job.id),
            "storyboard_revision_id": str(storyboard.id),
            "release": {
                "state": "published",
                "external_id": "video-123",
                "published_at": published_at.isoformat(),
            },
        },
    )
    session.add(package)
    session.flush()
    principal = Principal(str(publisher_id), frozenset({Role.PUBLISHER}))
    return publisher_id, principal, package, published_at


def make_snapshot(
    captured_at: datetime,
    *,
    impressions: int = 1000,
    clicks: int = 100,
    views: int = 200,
    likes: int = 30,
    comments: int = 10,
    shares: int = 5,
    watch_time_seconds: float = 7200,
) -> AnalyticsSnapshot:
    return AnalyticsSnapshot(
        captured_at=captured_at,
        impressions=impressions,
        clicks=clicks,
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
        watch_time_seconds=watch_time_seconds,
        source_name="youtube-studio-manual",
    )


def test_records_cumulative_snapshots_and_computes_measured_indicators(database_engine) -> None:
    with Session(database_engine) as session:
        publisher_id, principal, package, published_at = create_published_package(session)
        service = AnalyticsService(session)
        service.record_snapshot(
            package.id,
            principal=principal,
            actor_id=publisher_id,
            snapshot=make_snapshot(published_at + timedelta(hours=1)),
        )
        later = make_snapshot(
            published_at + timedelta(days=1),
            impressions=2000,
            clicks=240,
            views=400,
            likes=70,
            comments=20,
            shares=10,
            watch_time_seconds=16000,
        )
        service.record_snapshot(
            package.id,
            principal=principal,
            actor_id=publisher_id,
            snapshot=later,
        )

        summary = service.get_package_summary(
            package.id,
            principal=principal,
            actor_id=publisher_id,
        )
        channel = service.get_channel_summary(
            "youtube",
            principal=principal,
            actor_id=publisher_id,
        )

        assert summary.snapshot_count == 2
        assert summary.impressions == 2000
        assert summary.views == 400
        assert summary.click_through_rate == pytest.approx(0.12)
        assert summary.average_view_duration_seconds == pytest.approx(40)
        assert summary.engagement_rate == pytest.approx(0.25)
        assert summary.render_cost_usd == Decimal("3.00")
        assert summary.render_cost_basis == "actual"
        assert summary.cost_per_video_minute_usd == Decimal("1.00")
        assert summary.cost_per_1000_views_usd == Decimal("7.50")
        assert summary.validated_asset_ratio == 1
        assert summary.citation_coverage == 1
        assert channel.package_count == 1
        assert channel.packages_with_data == 1
        assert channel.views == 400
        assert channel.actual_cost_packages == 1
        assert channel.estimated_cost_packages == 0
        assert channel.predictions_available is False


def test_rejects_decreasing_counters_and_duplicate_capture_timestamp(database_engine) -> None:
    with Session(database_engine) as session:
        publisher_id, principal, package, published_at = create_published_package(session)
        service = AnalyticsService(session)
        captured_at = published_at + timedelta(hours=1)
        service.record_snapshot(
            package.id,
            principal=principal,
            actor_id=publisher_id,
            snapshot=make_snapshot(captured_at),
        )

        with pytest.raises(ConflictError, match="cannot decrease"):
            service.record_snapshot(
                package.id,
                principal=principal,
                actor_id=publisher_id,
                snapshot=make_snapshot(
                    captured_at + timedelta(hours=1),
                    views=100,
                ),
            )
        with pytest.raises(ConflictError, match="already exists"):
            service.record_snapshot(
                package.id,
                principal=principal,
                actor_id=publisher_id,
                snapshot=make_snapshot(captured_at),
            )


def test_requires_authorized_publisher_to_write_analytics(database_engine) -> None:
    with Session(database_engine) as session:
        _, _, package, published_at = create_published_package(session)
        viewer_id = uuid4()
        viewer = User(
            id=viewer_id,
            email=f"{viewer_id}@example.org",
            role=Role.VIEWER.value,
        )
        session.add(viewer)
        session.flush()
        service = AnalyticsService(session)
        with pytest.raises(ForbiddenError):
            service.record_snapshot(
                package.id,
                principal=Principal(str(viewer_id), frozenset({Role.VIEWER})),
                actor_id=viewer_id,
                snapshot=make_snapshot(published_at + timedelta(hours=1)),
            )

        summary = service.get_package_summary(
            package.id,
            principal=Principal(str(viewer_id), frozenset({Role.VIEWER})),
            actor_id=viewer_id,
        )
        assert summary.snapshot_count == 0


def test_snapshot_rejects_invalid_counter_relationships_and_timestamps() -> None:
    with pytest.raises(ValueError, match="Clicks cannot exceed impressions"):
        make_snapshot(datetime.now(UTC), impressions=10, clicks=11)
    with pytest.raises(ValueError, match="timezone"):
        make_snapshot(datetime.now())
