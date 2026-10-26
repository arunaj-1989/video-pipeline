"""Measured publishing, rendering-cost, and content-quality analytics."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    Asset,
    AuditEvent,
    EpisodeRevision,
    PublishingPackage,
    RenderJob,
    Scene,
    User,
)
from universal_video_maker.errors import ConflictError, ForbiddenError, NotFoundError
from universal_video_maker.security import Permission, Principal, require_permission

COUNTER_FIELDS = (
    "impressions",
    "clicks",
    "views",
    "likes",
    "comments",
    "shares",
)


@dataclass(frozen=True)
class AnalyticsSnapshot:
    captured_at: datetime
    impressions: int
    clicks: int
    views: int
    likes: int
    comments: int
    shares: int
    watch_time_seconds: float
    source_name: str

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("Analytics capture timestamp must include a timezone.")
        for field in COUNTER_FIELDS:
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer.")
        if self.clicks > self.impressions:
            raise ValueError("Clicks cannot exceed impressions.")
        if (
            not isinstance(self.watch_time_seconds, (int, float))
            or isinstance(self.watch_time_seconds, bool)
            or not math.isfinite(self.watch_time_seconds)
            or self.watch_time_seconds < 0
        ):
            raise ValueError("Watch time must be finite and non-negative.")
        if (
            not isinstance(self.source_name, str)
            or not self.source_name.strip()
            or len(self.source_name.strip()) > 80
        ):
            raise ValueError("Analytics source name must contain 1–80 characters.")

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "captured_at": self.captured_at.astimezone(UTC).isoformat(),
            "source_name": self.source_name.strip(),
        }


@dataclass(frozen=True)
class PackageAnalyticsSummary:
    package_id: UUID
    platform: str
    snapshot_count: int
    latest_captured_at: datetime | None
    impressions: int | None
    clicks: int | None
    views: int | None
    likes: int | None
    comments: int | None
    shares: int | None
    click_through_rate: float | None
    average_view_duration_seconds: float | None
    engagement_rate: float | None
    render_cost_usd: Decimal | None
    render_cost_basis: str | None
    cost_per_video_minute_usd: Decimal | None
    cost_per_1000_views_usd: Decimal | None
    validated_asset_ratio: float
    citation_coverage: float


@dataclass(frozen=True)
class ChannelAnalyticsSummary:
    platform: str
    package_count: int
    packages_with_data: int
    impressions: int
    clicks: int
    views: int
    likes: int
    comments: int
    shares: int
    click_through_rate: float | None
    average_view_duration_seconds: float | None
    engagement_rate: float | None
    render_cost_usd: Decimal
    actual_cost_packages: int
    estimated_cost_packages: int
    cost_per_1000_views_usd: Decimal | None
    predictions_available: bool = False


class AnalyticsService:
    """Store cumulative platform measurements and report observed indicators."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_snapshot(
        self,
        package_id: UUID,
        *,
        principal: Principal,
        actor_id: UUID,
        snapshot: AnalyticsSnapshot,
    ) -> dict[str, Any]:
        self._require_access(principal, actor_id, Permission.ANALYTICS_WRITE)
        package = self._get_package_for_update(package_id)
        if package.status != "published":
            raise ConflictError("Analytics can only be recorded for published packages.")
        metadata = package.publishing_metadata
        release = metadata.get("release")
        package_metadata = metadata.get("metadata")
        if not isinstance(release, dict) or not isinstance(package_metadata, dict):
            raise ConflictError("Published package is missing release metadata.")
        if snapshot.captured_at.astimezone(UTC) < self._published_at(release):
            raise ValueError("Analytics snapshot cannot predate publication.")
        if snapshot.captured_at > datetime.now(UTC):
            raise ValueError("Analytics capture timestamp cannot be in the future.")
        if snapshot.source_name.strip() == "":
            raise ValueError("Analytics source name cannot be empty.")

        snapshots = self._read_snapshots(
            metadata,
            expected_platform=package_metadata.get("platform"),
        )
        encoded = snapshot.to_dict()
        encoded["snapshot_id"] = str(uuid4())
        encoded["platform"] = package_metadata["platform"]
        if any(
            item.get("captured_at") == encoded["captured_at"]
            for item in snapshots
        ):
            raise ConflictError("A snapshot already exists at that capture timestamp.")
        ordered = sorted(
            [*snapshots, encoded],
            key=lambda item: datetime.fromisoformat(item["captured_at"]),
        )
        self._validate_cumulative_series(ordered)
        package.publishing_metadata = {
            **metadata,
            "analytics_snapshots": ordered,
        }
        self._session.add(
            AuditEvent(
                actor_id=actor_id,
                object_type="publishing_package",
                object_id=package.id,
                action="analytics.snapshot_recorded",
                details={
                    "platform": package_metadata["platform"],
                    "captured_at": encoded["captured_at"],
                    "source_name": snapshot.source_name.strip(),
                },
            )
        )
        self._session.flush()
        return encoded

    def get_package_summary(
        self,
        package_id: UUID,
        *,
        principal: Principal,
        actor_id: UUID,
    ) -> PackageAnalyticsSummary:
        self._require_access(principal, actor_id, Permission.ANALYTICS_READ)
        package = self._get_package(package_id)
        if package.status != "published":
            raise ConflictError("Analytics summaries require a published package.")
        metadata = package.publishing_metadata
        package_metadata = metadata.get("metadata")
        if not isinstance(package_metadata, dict):
            raise ConflictError("Publishing package metadata is invalid.")
        snapshots = self._read_snapshots(
            metadata,
            expected_platform=package_metadata.get("platform"),
        )
        latest = snapshots[-1] if snapshots else None
        return self._package_summary(package, package_metadata["platform"], snapshots, latest)

    def get_channel_summary(
        self,
        platform: str,
        *,
        principal: Principal,
        actor_id: UUID,
    ) -> ChannelAnalyticsSummary:
        self._require_access(principal, actor_id, Permission.ANALYTICS_READ)
        if not platform.strip():
            raise ValueError("Platform cannot be empty.")
        packages = list(
            self._session.scalars(
                select(PublishingPackage).where(PublishingPackage.status == "published")
            )
        )
        selected: list[tuple[PublishingPackage, list[dict[str, Any]]]] = []
        for package in packages:
            metadata = package.publishing_metadata
            package_metadata = metadata.get("metadata")
            if isinstance(package_metadata, dict) and package_metadata.get("platform") == platform:
                snapshots = self._read_snapshots(metadata, expected_platform=platform)
                selected.append((package, snapshots))
        latest_snapshots = [snapshots[-1] for _, snapshots in selected if snapshots]
        totals = {
            field: sum(int(snapshot[field]) for snapshot in latest_snapshots)
            for field in COUNTER_FIELDS
        }
        total_watch_time = sum(
            float(snapshot["watch_time_seconds"]) for snapshot in latest_snapshots
        )
        views = totals["views"]
        impressions = totals["impressions"]
        clicks = totals["clicks"]
        render_jobs = [
            job
            for package, _ in selected
            if (job := self._render_job(package)) is not None
        ]
        costs = [
            job.actual_cost if job.actual_cost is not None else job.estimated_cost
            for job in render_jobs
            if job.actual_cost is not None or job.estimated_cost is not None
        ]
        render_cost = sum(costs, Decimal("0"))
        return ChannelAnalyticsSummary(
            platform=platform,
            package_count=len(selected),
            packages_with_data=len(latest_snapshots),
            impressions=impressions,
            clicks=clicks,
            views=views,
            likes=totals["likes"],
            comments=totals["comments"],
            shares=totals["shares"],
            click_through_rate=clicks / impressions if impressions else None,
            average_view_duration_seconds=total_watch_time / views if views else None,
            engagement_rate=(
                (totals["likes"] + totals["comments"] + totals["shares"]) / views
                if views
                else None
            ),
            render_cost_usd=render_cost,
            actual_cost_packages=sum(job.actual_cost is not None for job in render_jobs),
            estimated_cost_packages=sum(
                job.actual_cost is None and job.estimated_cost is not None
                for job in render_jobs
            ),
            cost_per_1000_views_usd=render_cost * 1000 / views if views else None,
        )

    def _package_summary(
        self,
        package: PublishingPackage,
        platform: str,
        snapshots: list[dict[str, Any]],
        latest: dict[str, Any] | None,
    ) -> PackageAnalyticsSummary:
        impressions = int(latest["impressions"]) if latest else None
        clicks = int(latest["clicks"]) if latest else None
        views = int(latest["views"]) if latest else None
        cost = self._render_cost(package)
        cost_basis = self._render_cost_basis(package)
        runtime = self._video_runtime_minutes(package)
        return PackageAnalyticsSummary(
            package_id=package.id,
            platform=platform,
            snapshot_count=len(snapshots),
            latest_captured_at=(
                datetime.fromisoformat(latest["captured_at"]) if latest else None
            ),
            impressions=impressions,
            clicks=clicks,
            views=views,
            likes=int(latest["likes"]) if latest else None,
            comments=int(latest["comments"]) if latest else None,
            shares=int(latest["shares"]) if latest else None,
            click_through_rate=clicks / impressions if clicks is not None and impressions else None,
            average_view_duration_seconds=(
                float(latest["watch_time_seconds"]) / views
                if latest and views
                else None
            ),
            engagement_rate=(
                (int(latest["likes"]) + int(latest["comments"]) + int(latest["shares"]))
                / views
                if latest and views
                else None
            ),
            render_cost_usd=cost,
            render_cost_basis=cost_basis,
            cost_per_video_minute_usd=(
                cost / Decimal(str(runtime))
                if cost is not None and runtime
                else None
            ),
            cost_per_1000_views_usd=cost * 1000 / views if cost is not None and views else None,
            validated_asset_ratio=self._validated_asset_ratio(package),
            citation_coverage=self._citation_coverage(package),
        )

    def _validated_asset_ratio(self, package: PublishingPackage) -> float:
        manifest = package.publishing_metadata.get("manifest")
        if not isinstance(manifest, list) or not manifest:
            return 0.0
        asset_ids = []
        expected_checksums: dict[UUID, str] = {}
        for item in manifest:
            if not isinstance(item, dict):
                continue
            try:
                asset_id = UUID(item["asset_id"])
            except (KeyError, TypeError, ValueError):
                continue
            asset_ids.append(asset_id)
            expected_checksums[asset_id] = str(item.get("checksum", ""))
        assets = self._session.scalars(select(Asset).where(Asset.id.in_(asset_ids)))
        valid_ids = {
            asset.id
            for asset in assets
            if asset.validation_status == "validated"
            and expected_checksums.get(asset.id) == asset.checksum
        }
        return len(valid_ids) / len(manifest)

    def _citation_coverage(self, package: PublishingPackage) -> float:
        try:
            revision_id = UUID(package.publishing_metadata["storyboard_revision_id"])
        except (KeyError, TypeError, ValueError):
            return 0.0
        revision = self._session.get(EpisodeRevision, revision_id)
        if revision is None:
            return 0.0
        scenes = list(
            self._session.scalars(select(Scene).where(Scene.revision_id == revision.id))
        )
        if not scenes:
            return 0.0
        cited = 0
        for scene in scenes:
            narration = scene.content.get("narration")
            citations = narration.get("citations") if isinstance(narration, dict) else None
            if isinstance(citations, list) and citations:
                cited += 1
        return cited / len(scenes)

    def _render_cost(self, package: PublishingPackage) -> Decimal | None:
        job = self._render_job(package)
        return (
            job.actual_cost if job.actual_cost is not None else job.estimated_cost
        ) if job is not None else None

    def _render_cost_basis(self, package: PublishingPackage) -> str | None:
        job = self._render_job(package)
        if job is None:
            return None
        if job.actual_cost is not None:
            return "actual"
        return "estimated" if job.estimated_cost is not None else None

    def _render_job(self, package: PublishingPackage) -> RenderJob | None:
        try:
            job_id = UUID(package.publishing_metadata["render_job_id"])
        except (KeyError, TypeError, ValueError):
            return None
        return self._session.get(RenderJob, job_id)

    def _video_runtime_minutes(self, package: PublishingPackage) -> float | None:
        manifest = package.publishing_metadata.get("manifest")
        if not isinstance(manifest, list):
            return None
        for item in manifest:
            if (
                isinstance(item, dict)
                and item.get("kind") == "episode_video"
                and item.get("variant") == "episode"
            ):
                try:
                    asset_id = UUID(item["asset_id"])
                except (KeyError, TypeError, ValueError):
                    return None
                asset = self._session.get(Asset, asset_id)
                if asset is None:
                    return None
                duration = asset.provenance.get("duration_seconds")
                if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                    return float(duration) / 60 if duration > 0 else None
        return None

    def _get_package_for_update(self, package_id: UUID) -> PublishingPackage:
        package = self._session.scalar(
            select(PublishingPackage)
            .where(PublishingPackage.id == package_id)
            .with_for_update()
        )
        if package is None:
            raise NotFoundError(f"Publishing package {package_id} does not exist.")
        return package

    def _require_access(
        self,
        principal: Principal,
        actor_id: UUID,
        permission: Permission,
    ) -> None:
        require_permission(principal, permission)
        if principal.subject != str(actor_id):
            raise ForbiddenError("Analytics actor identity must match the authenticated principal.")
        user = self._session.get(User, actor_id)
        if user is None:
            raise NotFoundError(f"Analytics actor {actor_id} does not exist.")
        if user.status != "active" or user.role not in {
            role.value for role in principal.roles
        }:
            raise ForbiddenError("Analytics actor account is inactive or roles do not match.")

    def _get_package(self, package_id: UUID) -> PublishingPackage:
        package = self._session.get(PublishingPackage, package_id)
        if package is None:
            raise NotFoundError(f"Publishing package {package_id} does not exist.")
        return package

    @staticmethod
    def _published_at(release: dict[str, Any]) -> datetime:
        timestamp = release.get("published_at")
        if not isinstance(timestamp, str):
            raise ConflictError("Published package is missing its publication timestamp.")
        try:
            published_at = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise ConflictError("Package publication timestamp is invalid.") from exc
        if published_at.tzinfo is None:
            raise ConflictError("Package publication timestamp must include a timezone.")
        return published_at.astimezone(UTC)

    @staticmethod
    def _read_snapshots(
        metadata: dict[str, Any],
        *,
        expected_platform: str | None = None,
    ) -> list[dict[str, Any]]:
        snapshots = metadata.get("analytics_snapshots", [])
        if not isinstance(snapshots, list):
            raise ConflictError("Stored analytics snapshots are invalid.")
        normalized: list[dict[str, Any]] = []
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                raise ConflictError("Stored analytics snapshot is invalid.")
            try:
                captured_at = datetime.fromisoformat(snapshot["captured_at"])
                values = {field: snapshot[field] for field in COUNTER_FIELDS}
                values["watch_time_seconds"] = float(snapshot["watch_time_seconds"])
                source_name = snapshot["source_name"]
            except (KeyError, TypeError, ValueError) as exc:
                raise ConflictError("Stored analytics snapshot is incomplete.") from exc
            if (
                captured_at.tzinfo is None
                or any(
                    not isinstance(values[field], int)
                    or isinstance(values[field], bool)
                    or values[field] < 0
                    for field in COUNTER_FIELDS
                )
                or not math.isfinite(values["watch_time_seconds"])
                or values["watch_time_seconds"] < 0
                or values["clicks"] > values["impressions"]
                or not isinstance(source_name, str)
                or not source_name.strip()
                or (
                    expected_platform is not None
                    and snapshot.get("platform") != expected_platform
                )
            ):
                raise ConflictError("Stored analytics snapshot contains invalid measurements.")
            normalized.append(
                {
                    **snapshot,
                    "captured_at": captured_at.astimezone(UTC).isoformat(),
                }
            )
        ordered = sorted(
            normalized,
            key=lambda item: datetime.fromisoformat(item["captured_at"]),
        )
        captured_times = [item["captured_at"] for item in ordered]
        if len(set(captured_times)) != len(captured_times):
            raise ConflictError("Stored analytics snapshots contain duplicate timestamps.")
        return ordered

    @staticmethod
    def _validate_cumulative_series(snapshots: list[dict[str, Any]]) -> None:
        for previous, current in zip(snapshots, snapshots[1:], strict=False):
            for field in COUNTER_FIELDS:
                if current[field] < previous[field]:
                    raise ConflictError(
                        f"Cumulative analytics counter {field} cannot decrease."
                    )
            if current["watch_time_seconds"] < previous["watch_time_seconds"]:
                raise ConflictError(
                    "Cumulative analytics counter watch_time_seconds cannot decrease."
                )
