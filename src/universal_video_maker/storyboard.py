"""Validated visual storyboards tied to immutable episode-plan revisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    AuditEvent,
    Claim,
    ClaimEvidence,
    Episode,
    EpisodeRevision,
    Scene,
    Source,
    User,
)
from universal_video_maker.episode_planner import EPISODE_DURATION_SECONDS
from universal_video_maker.errors import ConflictError, ForbiddenError, NotFoundError
from universal_video_maker.security import Permission, Principal, require_permission

MAX_SCENE_TEXT_LENGTH = 4000


@dataclass(frozen=True)
class StoryboardSceneDraft:
    visual_description: str
    camera_direction: str
    visual_prompt: str
    required_assets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for label, value in (
            ("Visual description", self.visual_description),
            ("Camera direction", self.camera_direction),
            ("Visual prompt", self.visual_prompt),
        ):
            if not value.strip():
                raise ValueError(f"{label} cannot be empty.")
            if len(value.strip()) > MAX_SCENE_TEXT_LENGTH:
                raise ValueError(
                    f"{label} cannot exceed {MAX_SCENE_TEXT_LENGTH} characters."
                )
        if any(not asset.strip() for asset in self.required_assets):
            raise ValueError("Required asset names cannot be empty.")
        normalized_assets = tuple(asset.strip().casefold() for asset in self.required_assets)
        if len(set(normalized_assets)) != len(normalized_assets):
            raise ValueError("Required assets cannot contain duplicates.")


@dataclass(frozen=True)
class StoryboardDraft:
    plan_revision_id: UUID
    scenes: tuple[StoryboardSceneDraft, ...]

    def __post_init__(self) -> None:
        if not self.scenes:
            raise ValueError("Storyboard must contain at least one scene.")


class StoryboardService:
    """Validate and persist a scene revision without generating media assets."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_storyboard(
        self,
        *,
        principal: Principal,
        editor_id: UUID,
        draft: StoryboardDraft,
    ) -> EpisodeRevision:
        require_permission(principal, Permission.EPISODES_WRITE)
        if principal.subject != str(editor_id):
            raise ForbiddenError("Editor identity must match the authenticated principal.")
        editor = self._session.get(User, editor_id)
        if editor is None:
            raise NotFoundError(f"Editor {editor_id} does not exist.")
        if editor.status != "active":
            raise ForbiddenError("Inactive users cannot create storyboards.")
        if editor.role not in {role.value for role in principal.roles}:
            raise ForbiddenError("Editor roles do not match the authenticated principal.")

        plan_revision = self._session.get(EpisodeRevision, draft.plan_revision_id)
        if plan_revision is None:
            raise NotFoundError(
                f"Episode plan revision {draft.plan_revision_id} does not exist."
            )
        episode = self._session.get(Episode, plan_revision.episode_id)
        if episode is None:
            raise NotFoundError(f"Episode {plan_revision.episode_id} does not exist.")
        if episode.status != "draft":
            raise ConflictError("Only draft episodes can receive a storyboard revision.")
        if plan_revision.content.get("kind") != "episode_plan":
            raise ConflictError("Storyboard must be based on an episode plan revision.")
        latest_revision = self._session.scalar(
            select(func.max(EpisodeRevision.revision)).where(
                EpisodeRevision.episode_id == episode.id
            )
        )
        if latest_revision != plan_revision.revision:
            raise ConflictError("Storyboard must use the latest episode revision.")

        beats = plan_revision.content.get("beats")
        if not isinstance(beats, list) or len(draft.scenes) != len(beats):
            raise ValueError("Provide exactly one storyboard scene for each plan beat.")
        self._validate_plan_evidence(plan_revision.content, beats)

        revision = EpisodeRevision(
            episode_id=episode.id,
            revision=plan_revision.revision + 1,
            created_by=editor_id,
            content={
                "kind": "storyboard",
                "source_plan_revision_id": str(plan_revision.id),
                "target_duration_seconds": plan_revision.content.get(
                    "target_duration_seconds"
                ),
                "scene_count": len(draft.scenes),
                "limitations": [
                    "Visual directions only; no images, audio, or video are generated.",
                    "Factual narration and citations are inherited from the plan.",
                ],
            },
        )
        self._session.add(revision)
        self._session.flush()

        for scene_number, (scene_draft, beat) in enumerate(
            zip(draft.scenes, beats, strict=True),
            start=1,
        ):
            self._session.add(
                Scene(
                    revision_id=revision.id,
                    scene_number=scene_number,
                    content=self._serialize_scene(scene_draft, beat, scene_number),
                )
            )

        self._session.add(
            AuditEvent(
                actor_id=editor_id,
                object_type="episode_revision",
                object_id=revision.id,
                action="episode.storyboard_created",
                details={
                    "episode_id": str(episode.id),
                    "source_plan_revision_id": str(plan_revision.id),
                    "revision": revision.revision,
                    "scene_count": len(draft.scenes),
                },
            )
        )
        self._session.flush()
        return revision

    def _validate_plan_evidence(
        self,
        plan_content: dict[str, Any],
        beats: list[Any],
    ) -> None:
        references = plan_content.get("evidence")
        if not isinstance(references, list):
            raise ConflictError("Episode plan is missing its evidence references.")
        if plan_content.get("target_duration_seconds") != EPISODE_DURATION_SECONDS:
            raise ConflictError("Episode plan has an invalid target duration.")

        evidence_by_chunk: dict[str, dict[str, Any]] = {}
        for reference in references:
            if not isinstance(reference, dict):
                raise ConflictError("Episode plan contains an invalid evidence reference.")
            chunk_id = reference.get("chunk_id")
            if not isinstance(chunk_id, str) or chunk_id in evidence_by_chunk:
                raise ConflictError("Episode plan contains duplicate or invalid evidence IDs.")
            if any(
                not isinstance(reference.get(field), str)
                or not reference[field].strip()
                for field in (
                    "text",
                    "claim_text",
                    "category",
                    "source_title",
                    "publisher",
                    "canonical_url",
                    "support_type",
                )
            ):
                raise ConflictError("Episode plan contains incomplete evidence metadata.")
            evidence_by_chunk[chunk_id] = reference

        current_start = 0
        for beat in beats:
            if not isinstance(beat, dict):
                raise ConflictError("Episode plan contains an invalid beat.")
            start_seconds = beat.get("start_seconds")
            duration_seconds = beat.get("duration_seconds")
            if (
                not isinstance(start_seconds, int)
                or isinstance(start_seconds, bool)
                or start_seconds != current_start
                or not isinstance(duration_seconds, int)
                or isinstance(duration_seconds, bool)
                or duration_seconds < 1
            ):
                raise ConflictError("Episode plan contains invalid or discontinuous beat timing.")
            current_start += duration_seconds
            if any(
                not isinstance(beat.get(field), str) or not beat[field].strip()
                for field in ("title", "purpose")
            ):
                raise ConflictError("Episode plan contains an incomplete beat.")
            narration = beat.get("narration")
            if not isinstance(narration, dict):
                raise ConflictError("Episode plan beat is missing grounded narration.")
            if not isinstance(narration.get("text"), str) or not narration["text"].strip():
                raise ConflictError("Episode plan beat is missing narration text.")
            citations = narration.get("citations")
            if not isinstance(citations, list) or not citations:
                raise ConflictError("Episode plan beat is missing evidence citations.")
            for citation in citations:
                if not isinstance(citation, dict):
                    raise ConflictError("Episode plan contains an invalid citation.")
                self._validate_current_citation(citation, evidence_by_chunk)
        if current_start != EPISODE_DURATION_SECONDS:
            raise ConflictError("Episode plan beats do not cover the target duration.")

    def _validate_current_citation(
        self,
        citation: dict[str, Any],
        evidence_by_chunk: dict[str, dict[str, Any]],
    ) -> None:
        chunk_id = citation.get("chunk_id")
        quote = citation.get("quote")
        reference = evidence_by_chunk.get(chunk_id) if isinstance(chunk_id, str) else None
        if reference is None or not isinstance(quote, str) or not quote.strip():
            raise ConflictError("Episode plan contains a citation without valid evidence.")

        try:
            claim_id = UUID(reference["claim_id"])
            source_id = UUID(reference["source_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConflictError("Episode plan contains invalid evidence identifiers.") from exc

        current_rows = self._session.execute(
            select(
                ClaimEvidence.excerpt,
                Claim.claim_text,
                Claim.category,
                ClaimEvidence.support_type,
                Source.title,
                Source.publisher,
                Source.canonical_url,
            )
            .join(Claim, Claim.id == ClaimEvidence.claim_id)
            .join(Source, Source.id == ClaimEvidence.source_id)
            .where(
                Claim.id == claim_id,
                Claim.verification_status == "verified",
                ClaimEvidence.source_id == source_id,
                ClaimEvidence.support_type.in_(("supports", "context")),
            )
        ).all()
        if not current_rows:
            raise ConflictError(f"Plan evidence is no longer verified: {chunk_id}")
        evidence_text = reference["text"]
        if not any(
            claim_text == reference["claim_text"]
            and category == reference["category"]
            and support_type == reference["support_type"]
            and title == reference["source_title"]
            and publisher == reference["publisher"]
            and canonical_url == reference["canonical_url"]
            and evidence_text in excerpt
            and quote in evidence_text
            and quote in excerpt
            for (
                excerpt,
                claim_text,
                category,
                support_type,
                title,
                publisher,
                canonical_url,
            ) in current_rows
        ):
            raise ConflictError(f"Plan evidence has changed since planning: {chunk_id}")

    @staticmethod
    def _serialize_scene(
        scene: StoryboardSceneDraft,
        beat: dict[str, Any],
        scene_number: int,
    ) -> dict[str, Any]:
        return {
            "kind": "storyboard_scene",
            "scene_number": scene_number,
            "beat_title": beat["title"],
            "purpose": beat["purpose"],
            "start_seconds": beat["start_seconds"],
            "duration_seconds": beat["duration_seconds"],
            "narration": beat["narration"],
            "visual_description": scene.visual_description.strip(),
            "camera_direction": scene.camera_direction.strip(),
            "visual_prompt": scene.visual_prompt.strip(),
            "required_assets": [asset.strip() for asset in scene.required_assets],
        }
