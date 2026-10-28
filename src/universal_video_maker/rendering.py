"""Render-output verification and persistence for final episode deliverables."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    Asset,
    AuditEvent,
    Episode,
    EpisodeRevision,
    RenderJob,
    Scene,
)
from universal_video_maker.episode_planner import (
    EPISODE_DURATION_SECONDS,
    SHORT_MAX_DURATION_SECONDS,
    SHORT_MIN_DURATION_SECONDS,
)
from universal_video_maker.errors import ConflictError, NotFoundError
from universal_video_maker.providers.minio_storage import (
    MinioObjectStore,
    MinioStorageError,
)

SHA256_PATTERN = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")
SHORT_VARIANTS = ("short_1", "short_2", "short_3")
VIDEO_CONTENT_TYPE = "video/mp4"
CAPTIONS_CONTENT_TYPE = "text/vtt"


class ArtifactInspectionError(Exception):
    """A local or provider-specific artifact could not be verified."""


@dataclass(frozen=True)
class ArtifactFacts:
    checksum: str
    content_type: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    has_audio: bool = False

    def __post_init__(self) -> None:
        if not SHA256_PATTERN.fullmatch(self.checksum):
            raise ArtifactInspectionError("Inspector returned an invalid SHA-256 checksum.")
        if not self.content_type.strip():
            raise ArtifactInspectionError("Inspector returned an empty media type.")
        if any(
            dimension is not None
            and (not isinstance(dimension, int) or isinstance(dimension, bool) or dimension < 1)
            for dimension in (self.width, self.height)
        ):
            raise ArtifactInspectionError("Inspector returned invalid media dimensions.")
        if self.duration_seconds is not None and (
            not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0
        ):
            raise ArtifactInspectionError("Inspector returned an invalid duration.")


class ArtifactInspector(Protocol):
    def inspect(self, storage_uri: str) -> ArtifactFacts: ...


class LocalFileArtifactInspector:
    """Inspect local artifacts using SHA-256 and the system ffprobe binary."""

    def __init__(self, *, ffprobe_path: str = "ffprobe", timeout_seconds: int = 30) -> None:
        if timeout_seconds < 1:
            raise ValueError("Artifact inspection timeout must be positive.")
        self._ffprobe_path = ffprobe_path
        self._timeout_seconds = timeout_seconds

    def inspect(self, storage_uri: str) -> ArtifactFacts:
        parsed_uri = urlsplit(storage_uri)
        if parsed_uri.scheme != "file" or parsed_uri.query or parsed_uri.fragment:
            raise ArtifactInspectionError("Local inspector accepts only file:// URIs.")
        if parsed_uri.netloc not in {"", "localhost"}:
            raise ArtifactInspectionError("Local file URI must not reference a remote host.")
        path = Path(url2pathname(unquote(parsed_uri.path)))
        try:
            if not path.is_file():
                raise ArtifactInspectionError("Rendered artifact does not exist as a file.")
            checksum = self._checksum(path)
        except OSError as exc:
            raise ArtifactInspectionError("Rendered artifact could not be read.") from exc
        if path.suffix.casefold() in {".vtt"}:
            return self._inspect_captions(path, checksum)
        return self._inspect_media(path, checksum)

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as media_file:
            for block in iter(lambda: media_file.read(1024 * 1024), b""):
                digest.update(block)
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def _inspect_captions(path: Path, checksum: str) -> ArtifactFacts:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ArtifactInspectionError("Caption file must be UTF-8 text.") from exc
        except OSError as exc:
            raise ArtifactInspectionError("Caption file could not be read.") from exc
        if not content.startswith("WEBVTT"):
            raise ArtifactInspectionError("Caption file must use the WebVTT format.")
        return ArtifactFacts(checksum=checksum, content_type=CAPTIONS_CONTENT_TYPE)

    def _inspect_media(self, path: Path, checksum: str) -> ArtifactFacts:
        command = [
            self._ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                timeout=self._timeout_seconds,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise ArtifactInspectionError("ffprobe is required to inspect rendered media.") from exc
        except subprocess.TimeoutExpired as exc:
            raise ArtifactInspectionError("ffprobe timed out while inspecting media.") from exc
        except OSError as exc:
            raise ArtifactInspectionError("ffprobe could not be started.") from exc
        if result.returncode != 0:
            raise ArtifactInspectionError("ffprobe could not inspect the rendered media.")
        try:
            metadata = json.loads(result.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactInspectionError("ffprobe returned invalid media metadata.") from exc
        if not isinstance(metadata, dict):
            raise ArtifactInspectionError("ffprobe returned invalid media metadata.")
        streams = metadata.get("streams", [])
        if not isinstance(streams, list):
            raise ArtifactInspectionError("ffprobe returned invalid media streams.")
        if any(not isinstance(stream, dict) for stream in streams):
            raise ArtifactInspectionError("ffprobe returned invalid media stream entries.")
        video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
        audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if not video_streams:
            raise ArtifactInspectionError("Rendered video must contain a video stream.")
        video = video_streams[0]
        format_metadata = metadata.get("format")
        if not isinstance(format_metadata, dict):
            raise ArtifactInspectionError("ffprobe returned invalid media format metadata.")
        try:
            width = int(video["width"])
            height = int(video["height"])
            format_name = str(format_metadata["format_name"]).casefold()
            codec_name = str(video["codec_name"]).casefold()
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactInspectionError(
                "ffprobe metadata is missing image/video dimensions."
            ) from exc
        if width < 1 or height < 1:
            raise ArtifactInspectionError("Rendered media has invalid dimensions.")
        content_type = self._content_type(path, format_name, codec_name)
        duration_value = format_metadata.get("duration")
        try:
            duration = float(duration_value) if duration_value is not None else None
        except (TypeError, ValueError) as exc:
            raise ArtifactInspectionError("ffprobe returned an invalid duration.") from exc
        if content_type == VIDEO_CONTENT_TYPE and (
            duration is None or not math.isfinite(duration) or duration <= 0
        ):
            raise ArtifactInspectionError("Rendered video has invalid duration metadata.")
        return ArtifactFacts(
            checksum=checksum,
            content_type=content_type,
            width=width,
            height=height,
            duration_seconds=duration,
            has_audio=bool(audio_streams),
        )

    @staticmethod
    def _content_type(path: Path, format_name: str, codec_name: str) -> str:
        if any(
            name in format_name.split(",") for name in ("mp4", "mov", "m4a", "3gp", "3g2", "mj2")
        ):
            return VIDEO_CONTENT_TYPE
        image_types = {
            "png": (".png", "image/png"),
            "mjpeg": (".jpg", "image/jpeg"),
            "webp": (".webp", "image/webp"),
        }
        if codec_name == "mjpeg":
            image_extensions = {".jpg", ".jpeg"}
        else:
            image_extensions = {image_types.get(codec_name, ("", ""))[0]}
        image_type = image_types.get(codec_name)
        if (
            image_type is not None
            and path.suffix.casefold() in image_extensions
            and any(
                supported in format_name
                for supported in ("image2", "png_pipe", "jpeg_pipe", "webp_pipe", "apng")
            )
        ):
            return image_type[1]
        raise ArtifactInspectionError("Rendered media format is not a supported MP4 or image.")


class MinioArtifactInspector:
    """Download MinIO artifacts temporarily and validate them with ffprobe."""

    def __init__(
        self,
        object_store: MinioObjectStore,
        local_inspector: ArtifactInspector | None = None,
    ) -> None:
        self._object_store = object_store
        self._local_inspector = local_inspector or LocalFileArtifactInspector()

    def inspect(self, storage_uri: str) -> ArtifactFacts:
        try:
            key = self._object_store.key_from_uri(storage_uri)
        except ValueError as error:
            raise ArtifactInspectionError(
                "Artifact URI must reference the configured MinIO bucket."
            ) from error
        suffix = Path(key).suffix
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix):
            suffix = ".bin"
        filename = f"artifact{suffix}"
        with tempfile.TemporaryDirectory(prefix="uvm-minio-artifact-") as directory:
            local_path = Path(directory) / filename
            try:
                self._object_store.download_to_file(key, local_path)
            except MinioStorageError as error:
                raise ArtifactInspectionError(
                    "Rendered artifact could not be downloaded from MinIO."
                ) from error
            return self._local_inspector.inspect(local_path.resolve().as_uri())


@dataclass(frozen=True)
class RenderArtifactDraft:
    kind: str
    variant: str
    storage_uri: str
    checksum: str

    def __post_init__(self) -> None:
        if self.kind not in {"episode_video", "short_video", "captions", "thumbnail"}:
            raise ValueError(f"Unsupported render artifact kind: {self.kind}")
        if not self.variant.strip() or len(self.variant) > 40:
            raise ValueError("Artifact variant must contain between 1 and 40 characters.")
        if not self.storage_uri.strip():
            raise ValueError("Artifact storage URI cannot be empty.")
        if not SHA256_PATTERN.fullmatch(self.checksum):
            raise ValueError("Artifact checksum must be a SHA-256 hex digest.")
        parsed_uri = urlsplit(self.storage_uri)
        if parsed_uri.scheme not in {"file", "s3", "gs", "https"}:
            raise ValueError("Artifact URI must use file, s3, gs, or https.")
        if (
            parsed_uri.query
            or parsed_uri.fragment
            or parsed_uri.username
            or parsed_uri.password
            or (parsed_uri.scheme != "file" and not parsed_uri.netloc)
        ):
            raise ValueError(
                "Artifact URI must not contain credentials, query strings, or fragments."
            )


@dataclass(frozen=True)
class RenderOutputBundle:
    job_id: UUID
    artifacts: tuple[RenderArtifactDraft, ...]


class RenderingService:
    """Verify a completed render bundle and register its assets exactly once."""

    def __init__(self, session: Session, inspector: ArtifactInspector) -> None:
        self._session = session
        self._inspector = inspector

    def register_outputs(self, bundle: RenderOutputBundle) -> tuple[Asset, ...]:
        job = self._session.get(RenderJob, bundle.job_id)
        if job is None:
            raise NotFoundError(f"Render job {bundle.job_id} does not exist.")
        if job.status != "completed":
            raise ConflictError("Only completed render jobs can register output assets.")
        if (
            self._session.scalar(select(Asset.id).where(Asset.render_job_id == job.id).limit(1))
            is not None
        ):
            raise ConflictError("Render outputs have already been registered for this job.")
        storyboard_revision_id = job.parameters.get("storyboard_revision_id")
        try:
            revision_id = UUID(storyboard_revision_id)
        except (TypeError, ValueError) as exc:
            raise ConflictError("Render job is missing its storyboard revision identity.") from exc
        revision = self._session.get(EpisodeRevision, revision_id)
        episode = self._session.get(Episode, job.episode_id)
        if (
            revision is None
            or episode is None
            or revision.episode_id != episode.id
            or revision.content.get("kind") != "storyboard"
        ):
            raise ConflictError("Render job storyboard does not belong to its episode.")
        scene_count = self._session.scalar(
            select(func.count()).select_from(Scene).where(Scene.revision_id == revision.id)
        )
        if scene_count != revision.content.get("scene_count"):
            raise ConflictError("Storyboard scene records are incomplete.")

        self._validate_bundle_shape(bundle.artifacts)
        facts_by_key: dict[tuple[str, str], ArtifactFacts] = {}
        for artifact in bundle.artifacts:
            facts = self._inspector.inspect(artifact.storage_uri)
            if facts.checksum.casefold().removeprefix("sha256:") != (
                artifact.checksum.casefold().removeprefix("sha256:")
            ):
                raise ArtifactInspectionError(
                    f"SHA-256 checksum does not match artifact {artifact.variant}."
                )
            facts_by_key[(artifact.kind, artifact.variant)] = facts
        self._validate_media_profiles(bundle.artifacts, facts_by_key)

        assets: list[Asset] = []
        for artifact in bundle.artifacts:
            facts = facts_by_key[(artifact.kind, artifact.variant)]
            asset = Asset(
                episode_id=episode.id,
                render_job_id=job.id,
                kind=artifact.kind,
                storage_uri=artifact.storage_uri,
                checksum=artifact.checksum.removeprefix("sha256:"),
                provider=job.provider,
                validation_status="validated",
                provenance={
                    "storyboard_revision_id": str(revision.id),
                    "variant": artifact.variant,
                    "content_type": facts.content_type,
                    "width": facts.width,
                    "height": facts.height,
                    "duration_seconds": facts.duration_seconds,
                    "has_audio": facts.has_audio,
                },
            )
            self._session.add(asset)
            assets.append(asset)
        actor_id = self._actor_id(job)
        self._session.add(
            AuditEvent(
                actor_id=actor_id,
                object_type="render_job",
                object_id=job.id,
                action="render.outputs_registered",
                details={
                    "storyboard_revision_id": str(revision.id),
                    "asset_count": len(assets),
                    "artifact_kinds": dict(Counter(asset.kind for asset in assets)),
                },
            )
        )
        self._session.flush()
        return tuple(assets)

    @staticmethod
    def _validate_bundle_shape(artifacts: tuple[RenderArtifactDraft, ...]) -> None:
        expected = {
            ("episode_video", "episode"),
            ("thumbnail", "thumbnail"),
            *(("short_video", variant) for variant in SHORT_VARIANTS),
            *(("captions", variant) for variant in ("episode", *SHORT_VARIANTS)),
        }
        actual = {(artifact.kind, artifact.variant) for artifact in artifacts}
        if len(actual) != len(artifacts) or actual != expected:
            raise ValueError(
                "Render output bundle must contain one episode video, three Shorts, "
                "four caption files, and one thumbnail with unique variants."
            )

    @staticmethod
    def _validate_media_profiles(
        artifacts: tuple[RenderArtifactDraft, ...],
        facts_by_key: dict[tuple[str, str], ArtifactFacts],
    ) -> None:
        for artifact in artifacts:
            facts = facts_by_key[(artifact.kind, artifact.variant)]
            if artifact.kind in {"episode_video", "short_video"}:
                if facts.content_type != VIDEO_CONTENT_TYPE or not facts.has_audio:
                    raise ValueError(f"{artifact.variant} must be an MP4 video with audio.")
                if facts.width is None or facts.height is None:
                    raise ValueError(f"{artifact.variant} is missing video dimensions.")
                if facts.duration_seconds is None:
                    raise ValueError(f"{artifact.variant} is missing measured duration.")
            elif artifact.kind == "captions":
                if facts.content_type != CAPTIONS_CONTENT_TYPE:
                    raise ValueError(f"{artifact.variant} captions must be WebVTT.")
            elif artifact.kind == "thumbnail":
                if (
                    not facts.content_type.startswith("image/")
                    or facts.width is None
                    or facts.height is None
                    or facts.width < 1
                    or facts.height < 1
                ):
                    raise ValueError("Thumbnail must be a readable image with dimensions.")

        episode_facts = facts_by_key[("episode_video", "episode")]
        if episode_facts.duration_seconds is None:
            raise ValueError("Episode video is missing measured duration.")
        if abs(episode_facts.duration_seconds - EPISODE_DURATION_SECONDS) > 1.0:
            raise ValueError("Episode video must be exactly three minutes (±1 second).")
        if not RenderingService._matches_aspect_ratio(
            episode_facts.width,
            episode_facts.height,
            16 / 9,
        ):
            raise ValueError("Episode video must use a 16:9 landscape aspect ratio.")
        for variant in SHORT_VARIANTS:
            facts = facts_by_key[("short_video", variant)]
            if facts.duration_seconds is None:
                raise ValueError(f"{variant} is missing measured duration.")
            if not (
                SHORT_MIN_DURATION_SECONDS <= facts.duration_seconds <= SHORT_MAX_DURATION_SECONDS
            ):
                raise ValueError(f"{variant} duration must be between 30 and 60 seconds.")
            if not RenderingService._matches_aspect_ratio(
                facts.width,
                facts.height,
                9 / 16,
            ):
                raise ValueError(f"{variant} must use a 9:16 portrait aspect ratio.")

    @staticmethod
    def _matches_aspect_ratio(
        width: int | None,
        height: int | None,
        target_ratio: float,
    ) -> bool:
        return (
            width is not None
            and height is not None
            and height > 0
            and abs(width / height - target_ratio) <= 0.02
        )

    @staticmethod
    def _actor_id(job: RenderJob) -> UUID | None:
        actor_id = job.parameters.get("submitted_by")
        try:
            return UUID(actor_id) if actor_id is not None else None
        except (TypeError, ValueError):
            return None
