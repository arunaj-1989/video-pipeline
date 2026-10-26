"""Human-controlled release packages and external-publication records."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    Approval,
    Asset,
    AuditEvent,
    Episode,
    EpisodeRevision,
    PublishingPackage,
    RenderJob,
    User,
)
from universal_video_maker.errors import ConflictError, ForbiddenError, NotFoundError
from universal_video_maker.security import Permission, Principal, require_permission

PLATFORM_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
HASHTAG_PATTERN = re.compile(r"^#[\w]+$", re.UNICODE)
REQUIRED_ASSET_VARIANTS = {
    ("episode_video", "episode"),
    ("thumbnail", "thumbnail"),
    *(("short_video", f"short_{index}") for index in range(1, 4)),
    *(("captions", variant) for variant in ("episode", "short_1", "short_2", "short_3")),
}


@dataclass(frozen=True)
class PublishingMetadata:
    title: str
    description: str
    language: str
    tags: tuple[str, ...]
    hashtags: tuple[str, ...]
    short_titles: tuple[str, str, str]
    platform: str = "youtube"
    visibility: str = "private"
    content_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.title.strip() or len(self.title.strip()) > 100:
            raise ValueError("Release title must contain between 1 and 100 characters.")
        if not self.description.strip() or len(self.description.strip()) > 5000:
            raise ValueError("Release description must contain between 1 and 5000 characters.")
        if not LANGUAGE_PATTERN.fullmatch(self.language):
            raise ValueError("Language must be a BCP-47 language tag such as en or hi-IN.")
        if not PLATFORM_PATTERN.fullmatch(self.platform):
            raise ValueError("Platform must be a lowercase platform identifier.")
        if self.visibility not in {"private", "unlisted", "public"}:
            raise ValueError("Visibility must be private, unlisted, or public.")
        if len(self.tags) > 30 or any(
            not tag.strip() or len(tag.strip()) > 100 for tag in self.tags
        ):
            raise ValueError("Provide at most 30 non-empty tags of at most 100 characters.")
        normalized_tags = tuple(tag.strip().casefold() for tag in self.tags)
        if len(set(normalized_tags)) != len(normalized_tags):
            raise ValueError("Tags must be unique.")
        if len(self.hashtags) > 30 or any(
            not HASHTAG_PATTERN.fullmatch(tag.strip()) for tag in self.hashtags
        ):
            raise ValueError("Hashtags must be valid #tags, with at most 30 entries.")
        normalized_hashtags = tuple(tag.strip().casefold() for tag in self.hashtags)
        if len(set(normalized_hashtags)) != len(normalized_hashtags):
            raise ValueError("Hashtags must be unique.")
        if len(self.short_titles) != 3 or any(
            not title.strip() or len(title.strip()) > 100 for title in self.short_titles
        ):
            raise ValueError("Provide three Short titles of 1–100 characters.")
        if any(
            not warning.strip() or len(warning.strip()) > 200
            for warning in self.content_warnings
        ):
            raise ValueError("Content warnings must contain 1–200 characters.")

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "title": self.title.strip(),
            "description": self.description.strip(),
            "language": self.language,
            "tags": [tag.strip() for tag in self.tags],
            "hashtags": [tag.strip() for tag in self.hashtags],
            "short_titles": [title.strip() for title in self.short_titles],
            "content_warnings": [warning.strip() for warning in self.content_warnings],
        }


class PublishingService:
    """Prepare immutable release metadata and record explicit human release."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_package(
        self,
        *,
        principal: Principal,
        publisher_id: UUID,
        episode_id: UUID,
        render_job_id: UUID,
        storyboard_revision_id: UUID,
        metadata: PublishingMetadata,
    ) -> PublishingPackage:
        self._require_publisher(principal, publisher_id)
        episode = self._session.get(Episode, episode_id)
        if episode is None:
            raise NotFoundError(f"Episode {episode_id} does not exist.")
        render_job = self._session.get(RenderJob, render_job_id)
        if render_job is None:
            raise NotFoundError(f"Render job {render_job_id} does not exist.")
        storyboard = self._session.get(EpisodeRevision, storyboard_revision_id)
        if storyboard is None:
            raise NotFoundError(f"Storyboard revision {storyboard_revision_id} does not exist.")
        if (
            render_job.episode_id != episode.id
            or render_job.status != "completed"
            or render_job.parameters.get("storyboard_revision_id") != str(storyboard.id)
        ):
            raise ConflictError("Render job does not match the episode and storyboard revision.")
        if storyboard.episode_id != episode.id or storyboard.content.get("kind") != "storyboard":
            raise ConflictError("Release package must reference the episode's storyboard revision.")

        assets = list(
            self._session.scalars(
                select(Asset).where(
                    Asset.render_job_id == render_job.id,
                    Asset.episode_id == episode.id,
                    Asset.validation_status == "validated",
                )
            )
        )
        self._validate_assets(assets)
        manifest = [
            {
                "asset_id": str(asset.id),
                "kind": asset.kind,
                "checksum": asset.checksum,
                "variant": asset.provenance.get("variant"),
                "storage_uri": asset.storage_uri,
            }
            for asset in sorted(assets, key=lambda item: (item.kind, str(item.id)))
        ]
        publishing_metadata = {
            "schema_version": 1,
            "metadata": metadata.to_dict(),
            "manifest": manifest,
            "render_job_id": str(render_job.id),
            "storyboard_revision_id": str(storyboard.id),
            "release": {"state": "draft"},
        }
        package = PublishingPackage(
            episode_id=episode.id,
            publishing_metadata=publishing_metadata,
            status="draft",
        )
        self._session.add(package)
        self._session.flush()
        self._audit(
            publisher_id,
            package,
            "publishing.package_created",
            {"render_job_id": str(render_job.id), "asset_count": str(len(manifest))},
        )
        self._session.flush()
        return package

    def approve_package(
        self,
        package_id: UUID,
        *,
        principal: Principal,
        publisher_id: UUID,
        note: str = "",
    ) -> PublishingPackage:
        self._require_publisher(principal, publisher_id)
        package = self._get_package(package_id)
        if package.status != "draft":
            raise ConflictError("Only draft publishing packages can be approved.")
        if len(note) > 1000:
            raise ValueError("Approval note cannot exceed 1000 characters.")
        storyboard_revision_id = self._metadata_uuid(
            package.publishing_metadata,
            "storyboard_revision_id",
        )
        fingerprint = self._fingerprint(package.publishing_metadata)
        approval_note = f"package:{package.id};sha256:{fingerprint}"
        approval = Approval(
            revision_id=storyboard_revision_id,
            reviewer_id=publisher_id,
            stage="release",
            decision="approved",
            note=approval_note + (f";note:{note.strip()}" if note.strip() else ""),
        )
        self._session.add(approval)
        package.publishing_metadata = {
            **package.publishing_metadata,
            "release": {
                "state": "approved",
                "approval_fingerprint": fingerprint,
                "approved_by": str(publisher_id),
                "approved_at": datetime.now(UTC).isoformat(),
                "approval_note": note.strip(),
            },
        }
        package.status = "approved"
        self._session.flush()
        self._audit(
            publisher_id,
            package,
            "publishing.package_approved",
            {"storyboard_revision_id": str(storyboard_revision_id)},
        )
        self._session.flush()
        return package

    def record_publication(
        self,
        package_id: UUID,
        *,
        principal: Principal,
        publisher_id: UUID,
        external_id: str,
        publication_url: str,
        published_at: datetime,
    ) -> PublishingPackage:
        self._require_publisher(principal, publisher_id)
        package = self._get_package(package_id)
        if package.status != "approved":
            raise ConflictError("Only approved packages can be recorded as published.")
        if not external_id.strip() or len(external_id.strip()) > 250:
            raise ValueError("External publication ID must contain 1–250 characters.")
        parsed_url = urlsplit(publication_url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise ValueError("Publication URL must be a public HTTPS URL.")
        if published_at.tzinfo is None or published_at.utcoffset() is None:
            raise ValueError("Publication timestamp must include a timezone.")
        if published_at > datetime.now(UTC):
            raise ValueError("Publication timestamp cannot be in the future.")
        package_metadata = package.publishing_metadata.get("metadata")
        if not isinstance(package_metadata, dict):
            raise ConflictError("Publishing package metadata is invalid.")
        for published_package in self._session.scalars(
            select(PublishingPackage).where(
                PublishingPackage.status == "published",
                PublishingPackage.id != package.id,
            )
        ):
            existing_metadata = published_package.publishing_metadata
            existing_release = existing_metadata.get("release")
            existing_package_metadata = existing_metadata.get("metadata")
            if (
                isinstance(existing_release, dict)
                and isinstance(existing_package_metadata, dict)
                and existing_release.get("external_id") == external_id.strip()
                and existing_package_metadata.get("platform") == package_metadata.get("platform")
            ):
                raise ConflictError("This external publication ID is already recorded.")

        fingerprint = self._fingerprint(package.publishing_metadata)
        try:
            approver_id = UUID(package.publishing_metadata["release"]["approved_by"])
            storyboard_revision_id = self._metadata_uuid(
                package.publishing_metadata,
                "storyboard_revision_id",
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConflictError("Package release approval metadata is invalid.") from exc
        approvals = self._session.scalars(
            select(Approval).where(
                Approval.revision_id == storyboard_revision_id,
                Approval.stage == "release",
                Approval.decision == "approved",
                Approval.reviewer_id == approver_id,
            )
        )
        matching_approval = any(
            approval.note
            and approval.note.startswith(f"package:{package.id};sha256:{fingerprint}")
            for approval in approvals
        )
        if (
            not matching_approval
            or package.publishing_metadata["release"].get("approval_fingerprint") != fingerprint
        ):
            raise ForbiddenError("A current release approval for this package is required.")
        package.publishing_metadata = {
            **package.publishing_metadata,
            "release": {
                **package.publishing_metadata["release"],
                "state": "published",
                "recorded_by": str(publisher_id),
                "published_at": published_at.astimezone(UTC).isoformat(),
                "external_id": external_id.strip(),
                "publication_url": publication_url,
            },
        }
        package.status = "published"
        self._session.flush()
        self._audit(
            publisher_id,
            package,
            "publishing.publication_recorded",
            {
                "external_id": external_id.strip(),
                "platform": package.publishing_metadata["metadata"]["platform"],
            },
        )
        self._session.flush()
        return package

    def _require_publisher(self, principal: Principal, publisher_id: UUID) -> None:
        require_permission(principal, Permission.PUBLISH_RELEASE)
        if principal.subject != str(publisher_id):
            raise ForbiddenError("Publisher identity must match the authenticated principal.")
        publisher = self._session.get(User, publisher_id)
        if publisher is None:
            raise NotFoundError(f"Publisher {publisher_id} does not exist.")
        if publisher.status != "active" or publisher.role not in {
            role.value for role in principal.roles
        }:
            raise ForbiddenError("Publisher account is inactive or roles do not match.")

    def _get_package(self, package_id: UUID) -> PublishingPackage:
        package = self._session.get(PublishingPackage, package_id)
        if package is None:
            raise NotFoundError(f"Publishing package {package_id} does not exist.")
        return package

    @staticmethod
    def _validate_assets(assets: list[Asset]) -> None:
        asset_variants = {
            (asset.kind, asset.provenance.get("variant")) for asset in assets
        }
        if len(assets) != len(REQUIRED_ASSET_VARIANTS) or asset_variants != REQUIRED_ASSET_VARIANTS:
            raise ConflictError("Release requires a complete, validated render manifest.")

    @staticmethod
    def _metadata_uuid(metadata: dict[str, Any], key: str) -> UUID:
        try:
            return UUID(metadata[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConflictError(f"Package is missing its {key} reference.") from exc

    @staticmethod
    def _fingerprint(publishing_metadata: dict[str, Any]) -> str:
        fingerprint_content = {
            "schema_version": publishing_metadata["schema_version"],
            "metadata": publishing_metadata["metadata"],
            "manifest": publishing_metadata["manifest"],
            "render_job_id": publishing_metadata["render_job_id"],
            "storyboard_revision_id": publishing_metadata["storyboard_revision_id"],
        }
        canonical = json.dumps(
            fingerprint_content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _audit(
        self,
        actor_id: UUID,
        package: PublishingPackage,
        action: str,
        details: dict[str, str],
    ) -> None:
        self._session.add(
            AuditEvent(
                actor_id=actor_id,
                object_type="publishing_package",
                object_id=package.id,
                action=action,
                details=details,
            )
        )
