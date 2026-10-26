import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    Asset,
    AuditEvent,
    Base,
    Episode,
    EpisodeRevision,
    RenderJob,
    Scene,
    Topic,
)
from universal_video_maker.errors import ConflictError
from universal_video_maker.rendering import (
    ArtifactFacts,
    ArtifactInspectionError,
    LocalFileArtifactInspector,
    RenderArtifactDraft,
    RenderingService,
    RenderOutputBundle,
)


class FakeInspector:
    def __init__(self, facts: dict[str, ArtifactFacts]) -> None:
        self.facts = facts

    def inspect(self, storage_uri: str) -> ArtifactFacts:
        return self.facts[storage_uri]


@pytest.fixture
def database_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def create_completed_job(session: Session):
    editor_id = uuid4()
    topic = Topic(title="Aditya-L1", category="mission")
    session.add(topic)
    session.flush()
    episode = Episode(topic_id=topic.id, title="A solar observatory", status="draft")
    session.add(episode)
    session.flush()
    revision = EpisodeRevision(
        episode_id=episode.id,
        revision=1,
        created_by=editor_id,
        content={"kind": "storyboard", "scene_count": 1},
    )
    session.add(revision)
    session.flush()
    session.add(
        Scene(
            revision_id=revision.id,
            scene_number=1,
            content={"visual_description": "Solar observatory"},
        )
    )
    job = RenderJob(
        episode_id=episode.id,
        provider="runpod_serverless",
        status="completed",
        parameters={
            "storyboard_revision_id": str(revision.id),
            "submitted_by": str(editor_id),
        },
    )
    session.add(job)
    session.flush()
    return job


def output_bundle() -> tuple[RenderOutputBundle, dict[str, ArtifactFacts]]:
    checksum = "a" * 64
    artifacts: list[RenderArtifactDraft] = []
    facts: dict[str, ArtifactFacts] = {}

    outputs = [
        ("episode_video", "episode", "video/mp4", 1920, 1080, 180.0, True),
        ("short_video", "short_1", "video/mp4", 1080, 1920, 30.0, True),
        ("short_video", "short_2", "video/mp4", 1080, 1920, 45.0, True),
        ("short_video", "short_3", "video/mp4", 1080, 1920, 60.0, True),
        ("captions", "episode", "text/vtt", None, None, None, False),
        ("captions", "short_1", "text/vtt", None, None, None, False),
        ("captions", "short_2", "text/vtt", None, None, None, False),
        ("captions", "short_3", "text/vtt", None, None, None, False),
        ("thumbnail", "thumbnail", "image/png", 1280, 720, None, False),
    ]
    for index, (kind, variant, content_type, width, height, duration, has_audio) in enumerate(
        outputs,
    ):
        uri = f"s3://universal-video/output-{index}"
        artifacts.append(
            RenderArtifactDraft(
                kind=kind,
                variant=variant,
                storage_uri=uri,
                checksum=checksum,
            )
        )
        facts[uri] = ArtifactFacts(
            checksum=f"sha256:{checksum}",
            content_type=content_type,
            width=width,
            height=height,
            duration_seconds=duration,
            has_audio=has_audio,
        )
    return (
        RenderOutputBundle(job_id=uuid4(), artifacts=tuple(artifacts)),
        facts,
    )


def test_registers_complete_verified_episode_bundle_and_audits(database_engine) -> None:
    with Session(database_engine) as session:
        job = create_completed_job(session)
        bundle, facts = output_bundle()
        bundle = RenderOutputBundle(job_id=job.id, artifacts=bundle.artifacts)

        assets = RenderingService(session, FakeInspector(facts)).register_outputs(bundle)
        audit = session.scalar(
            select(AuditEvent).where(
                AuditEvent.object_id == job.id,
                AuditEvent.action == "render.outputs_registered",
            )
        )

        assert len(assets) == 9
        assert {asset.kind for asset in assets} == {
            "episode_video",
            "short_video",
            "captions",
            "thumbnail",
        }
        assert all(asset.validation_status == "validated" for asset in assets)
        assert all(asset.render_job_id == job.id for asset in assets)
        assert audit is not None
        assert audit.details["asset_count"] == 9
        assert session.scalar(
            select(func.count()).select_from(Asset).where(Asset.render_job_id == job.id)
        ) == 9


def test_rejects_wrong_duration_or_aspect_ratio(database_engine) -> None:
    with Session(database_engine) as session:
        job = create_completed_job(session)
        bundle, facts = output_bundle()
        bundle = RenderOutputBundle(job_id=job.id, artifacts=bundle.artifacts)
        facts["s3://universal-video/output-1"] = ArtifactFacts(
            checksum="sha256:" + "a" * 64,
            content_type="video/mp4",
            width=1920,
            height=1080,
            duration_seconds=75,
            has_audio=True,
        )

        with pytest.raises(ValueError, match="short_1 duration"):
            RenderingService(session, FakeInspector(facts)).register_outputs(bundle)

        assert session.scalar(
            select(func.count()).select_from(Asset).where(Asset.render_job_id == job.id)
        ) == 0


def test_rejects_checksum_mismatch_before_persisting_any_assets(database_engine) -> None:
    with Session(database_engine) as session:
        job = create_completed_job(session)
        bundle, facts = output_bundle()
        bundle = RenderOutputBundle(job_id=job.id, artifacts=bundle.artifacts)
        facts["s3://universal-video/output-0"] = ArtifactFacts(
            checksum="sha256:" + "b" * 64,
            content_type="video/mp4",
            width=1920,
            height=1080,
            duration_seconds=180,
            has_audio=True,
        )

        with pytest.raises(ArtifactInspectionError, match="checksum"):
            RenderingService(session, FakeInspector(facts)).register_outputs(bundle)

        assert session.scalar(
            select(func.count()).select_from(Asset).where(Asset.render_job_id == job.id)
        ) == 0


def test_requires_exact_bundle_and_completed_job(database_engine) -> None:
    with Session(database_engine) as session:
        job = create_completed_job(session)
        bundle, facts = output_bundle()
        incomplete = RenderOutputBundle(job_id=job.id, artifacts=bundle.artifacts[:-1])

        with pytest.raises(ValueError, match="must contain"):
            RenderingService(session, FakeInspector(facts)).register_outputs(incomplete)

        job.status = "failed"
        with pytest.raises(ConflictError, match="completed render jobs"):
            RenderingService(session, FakeInspector(facts)).register_outputs(
                RenderOutputBundle(job_id=job.id, artifacts=bundle.artifacts)
            )


def test_rejects_untrusted_or_signed_storage_uri() -> None:
    with pytest.raises(ValueError, match="credentials"):
        RenderArtifactDraft(
            kind="episode_video",
            variant="episode",
            storage_uri="https://storage.example/video.mp4?token=secret",
            checksum="a" * 64,
        )


def test_local_inspector_checks_caption_bytes_and_checksum(tmp_path) -> None:
    captions = tmp_path / "episode.vtt"
    contents = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nSolar mission\n"
    captions.write_text(contents, encoding="utf-8")

    facts = LocalFileArtifactInspector().inspect(captions.as_uri())

    assert facts.content_type == "text/vtt"
    assert facts.checksum == f"sha256:{hashlib.sha256(captions.read_bytes()).hexdigest()}"


def test_local_inspector_rejects_invalid_webvtt(tmp_path) -> None:
    captions = tmp_path / "episode.vtt"
    captions.write_text("not a caption file", encoding="utf-8")

    with pytest.raises(ArtifactInspectionError, match="WebVTT"):
        LocalFileArtifactInspector().inspect(captions.as_uri())
