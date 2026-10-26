"""Evidence-grounded episode outline and short-form concept planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    AuditEvent,
    Claim,
    ClaimEvidence,
    Episode,
    EpisodeRevision,
    Source,
    Topic,
    User,
)
from universal_video_maker.errors import ConflictError, ForbiddenError, NotFoundError
from universal_video_maker.rag import (
    EvidenceCitation,
    GroundedAnswer,
    GroundedStatement,
    RetrievedEvidence,
    validate_grounded_answer,
)
from universal_video_maker.security import Permission, Principal, require_permission

EPISODE_DURATION_SECONDS = 180
SHORT_COUNT = 3
SHORT_MIN_DURATION_SECONDS = 30
SHORT_MAX_DURATION_SECONDS = 60


@dataclass(frozen=True)
class EpisodeBeat:
    title: str
    purpose: str
    start_seconds: int
    duration_seconds: int
    narration: GroundedStatement


@dataclass(frozen=True)
class ShortConcept:
    title: str
    hook: str
    duration_seconds: int
    narration: GroundedStatement
    call_to_action: str


@dataclass(frozen=True)
class EpisodePlanDraft:
    topic_id: UUID
    title: str
    logline: str
    learning_objectives: tuple[str, ...]
    story_arc: tuple[str, ...]
    beats: tuple[EpisodeBeat, ...]
    shorts: tuple[ShortConcept, ...]
    evidence_context: tuple[RetrievedEvidence, ...]
    episode_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.title.strip() or len(self.title.strip()) > 300:
            raise ValueError("Episode title must contain between 1 and 300 characters.")
        if not self.logline.strip():
            raise ValueError("Episode logline cannot be empty.")
        if not 1 <= len(self.learning_objectives) <= 5:
            raise ValueError("Provide between one and five learning objectives.")
        if any(not objective.strip() for objective in self.learning_objectives):
            raise ValueError("Learning objectives cannot be empty.")
        if len(self.story_arc) < 3 or any(not step.strip() for step in self.story_arc):
            raise ValueError("Story arc requires at least three non-empty story stages.")
        if not self.beats:
            raise ValueError("Episode plan must contain at least one timed beat.")
        if len(self.shorts) != SHORT_COUNT:
            raise ValueError(f"Episode plan must contain exactly {SHORT_COUNT} Shorts concepts.")
        if not self.evidence_context:
            raise ValueError("Episode plan requires retrieved verified evidence context.")

        current_start = 0
        for beat in self.beats:
            if not beat.title.strip() or not beat.purpose.strip():
                raise ValueError("Every episode beat needs a title and purpose.")
            if beat.start_seconds != current_start:
                raise ValueError("Episode beats must be contiguous and start at zero.")
            if beat.duration_seconds < 1:
                raise ValueError("Episode beat durations must be positive.")
            current_start += beat.duration_seconds
        if current_start != EPISODE_DURATION_SECONDS:
            raise ValueError(
                f"Episode beats must total exactly {EPISODE_DURATION_SECONDS} seconds."
            )

        for short in self.shorts:
            if (
                not short.title.strip()
                or not short.hook.strip()
                or not short.call_to_action.strip()
            ):
                raise ValueError("Every Short needs a title, hook, and call to action.")
            if not (
                SHORT_MIN_DURATION_SECONDS
                <= short.duration_seconds
                <= SHORT_MAX_DURATION_SECONDS
            ):
                raise ValueError(
                    f"Shorts must be between {SHORT_MIN_DURATION_SECONDS} and "
                    f"{SHORT_MAX_DURATION_SECONDS} seconds."
                )

        grounded_statements = tuple(beat.narration for beat in self.beats) + tuple(
            short.narration for short in self.shorts
        )
        grounding = validate_grounded_answer(
            GroundedAnswer(grounded_statements),
            context=self.evidence_context,
        )
        if not grounding.valid:
            raise ValueError("Episode plan is not grounded: " + "; ".join(grounding.issues))


class EpisodePlannerService:
    """Validate and persist plan revisions without generating or approving scripts."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_plan(
        self,
        *,
        principal: Principal,
        planner_id: UUID,
        draft: EpisodePlanDraft,
    ) -> EpisodeRevision:
        require_permission(principal, Permission.EPISODES_WRITE)
        if principal.subject != str(planner_id):
            raise ForbiddenError("Planner identity must match the authenticated principal.")
        planner = self._session.get(User, planner_id)
        if planner is None:
            raise NotFoundError(f"Planner {planner_id} does not exist.")
        if planner.status != "active":
            raise ForbiddenError("Inactive users cannot create episode plans.")
        if planner.role not in {role.value for role in principal.roles}:
            raise ForbiddenError("Planner roles do not match the authenticated principal.")
        topic = self._session.get(Topic, draft.topic_id)
        if topic is None:
            raise NotFoundError(f"Topic {draft.topic_id} does not exist.")

        self._validate_current_evidence(draft.evidence_context)
        self._validate_unique_citations(draft)

        if draft.episode_id is None:
            episode = Episode(
                topic_id=draft.topic_id,
                title=draft.title.strip(),
                status="draft",
            )
            self._session.add(episode)
            self._session.flush()
        else:
            episode = self._session.get(Episode, draft.episode_id)
            if episode is None:
                raise NotFoundError(f"Episode {draft.episode_id} does not exist.")
            if episode.topic_id != draft.topic_id:
                raise ConflictError("Episode topic cannot be changed by a plan revision.")
            if episode.status != "draft":
                raise ConflictError("Only draft episodes can receive a planning revision.")
            episode.title = draft.title.strip()

        current_revision = self._session.scalar(
            select(func.coalesce(func.max(EpisodeRevision.revision), 0)).where(
                EpisodeRevision.episode_id == episode.id
            )
        )
        revision = EpisodeRevision(
            episode_id=episode.id,
            revision=int(current_revision or 0) + 1,
            created_by=planner_id,
            content=self._serialize_plan(draft),
        )
        self._session.add(revision)
        self._session.flush()
        self._session.add(
            AuditEvent(
                actor_id=planner_id,
                object_type="episode_revision",
                object_id=revision.id,
                action="episode.plan_created",
                details={
                    "episode_id": str(episode.id),
                    "topic_id": str(draft.topic_id),
                    "revision": revision.revision,
                    "beat_count": len(draft.beats),
                    "short_count": len(draft.shorts),
                    "evidence_claim_ids": self._cited_claim_ids(draft),
                },
            )
        )
        self._session.flush()
        return revision

    def _validate_current_evidence(
        self,
        context: tuple[RetrievedEvidence, ...],
    ) -> None:
        seen_chunks: set[str] = set()
        for item in context:
            if item.chunk_id in seen_chunks:
                raise ValueError("Retrieved evidence context contains duplicate chunk IDs.")
            seen_chunks.add(item.chunk_id)
            if not item.text.strip():
                raise ValueError("Retrieved evidence chunks cannot be empty.")
            current_excerpt = self._session.scalar(
                select(ClaimEvidence.excerpt)
                .join(Claim, Claim.id == ClaimEvidence.claim_id)
                .join(Source, Source.id == ClaimEvidence.source_id)
                .where(
                    Claim.id == item.claim_id,
                    ClaimEvidence.source_id == item.source_id,
                    Claim.verification_status == "verified",
                    ClaimEvidence.support_type.in_(("supports", "context")),
                    Claim.claim_text == item.claim_text,
                    Claim.category == item.category,
                    ClaimEvidence.support_type == item.support_type,
                    Source.title == item.source_title,
                    Source.publisher == item.publisher,
                    Source.canonical_url == item.canonical_url,
                )
            )
            if current_excerpt is None or item.text not in current_excerpt:
                raise ConflictError(
                    f"Retrieved evidence is no longer current or verified: {item.chunk_id}"
                )

    @staticmethod
    def _all_citations(draft: EpisodePlanDraft) -> tuple[EvidenceCitation, ...]:
        return tuple(
            citation
            for statement in (
                *(beat.narration for beat in draft.beats),
                *(short.narration for short in draft.shorts),
            )
            for citation in statement.citations
        )

    @classmethod
    def _validate_unique_citations(cls, draft: EpisodePlanDraft) -> None:
        context_by_id = {item.chunk_id: item for item in draft.evidence_context}
        for citation in cls._all_citations(draft):
            if citation.chunk_id not in context_by_id:
                raise ValueError(
                    f"Citation references evidence outside plan context: {citation.chunk_id}"
                )

    @classmethod
    def _cited_claim_ids(cls, draft: EpisodePlanDraft) -> list[str]:
        context_by_id = {item.chunk_id: item for item in draft.evidence_context}
        return sorted(
            {
                str(context_by_id[citation.chunk_id].claim_id)
                for citation in cls._all_citations(draft)
            }
        )

    @classmethod
    def _serialize_plan(cls, draft: EpisodePlanDraft) -> dict[str, object]:
        def serialize_statement(statement: GroundedStatement) -> dict[str, object]:
            return {
                "text": statement.text,
                "citations": [
                    asdict(citation) for citation in statement.citations
                ],
            }

        return {
            "kind": "episode_plan",
            "title": draft.title.strip(),
            "logline": draft.logline.strip(),
            "target_duration_seconds": EPISODE_DURATION_SECONDS,
            "learning_objectives": [item.strip() for item in draft.learning_objectives],
            "story_arc": [item.strip() for item in draft.story_arc],
            "beats": [
                {
                    "title": beat.title.strip(),
                    "purpose": beat.purpose.strip(),
                    "start_seconds": beat.start_seconds,
                    "duration_seconds": beat.duration_seconds,
                    "narration": serialize_statement(beat.narration),
                }
                for beat in draft.beats
            ],
            "shorts": [
                {
                    "title": short.title.strip(),
                    "hook": short.hook.strip(),
                    "duration_seconds": short.duration_seconds,
                    "narration": serialize_statement(short.narration),
                    "call_to_action": short.call_to_action.strip(),
                }
                for short in draft.shorts
            ],
            "evidence": [
                {
                    "chunk_id": item.chunk_id,
                    "claim_id": str(item.claim_id),
                    "source_id": str(item.source_id),
                    "text": item.text,
                    "claim_text": item.claim_text,
                    "category": item.category,
                    "source_title": item.source_title,
                    "publisher": item.publisher,
                    "canonical_url": item.canonical_url,
                    "support_type": item.support_type,
                }
                for item in draft.evidence_context
            ],
            "limitations": [
                "Outline only; no generated script or storyboard scenes.",
                "All cited claims remain subject to editorial review.",
            ],
        }
