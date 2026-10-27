from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from universal_video_maker.config import Settings
from universal_video_maker.db.models import (
    Approval,
    AuditEvent,
    Episode,
    EpisodeRevision,
    RenderJob,
    Topic,
)
from universal_video_maker.episode_generation import (
    NoGroundedEvidenceError,
    OllamaEpisodePlanGenerator,
)
from universal_video_maker.episode_planner import EpisodePlanDraft, EpisodePlannerService
from universal_video_maker.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from universal_video_maker.identity import CurrentPrincipal
from universal_video_maker.providers.ollama import OllamaClientError
from universal_video_maker.security import Permission, require_permission
from universal_video_maker.topics_api import DatabaseSession
from universal_video_maker.user_mapping import ensure_internal_user

router = APIRouter(prefix="/episodes", tags=["episodes"])


class PlanGenerator(Protocol):
    def generate(self, topic_id: UUID, *, query: str | None = None) -> EpisodePlanDraft: ...


PlanGeneratorFactory = Callable[[Session], PlanGenerator]


def build_plan_generator_factory(settings: Settings) -> PlanGeneratorFactory:
    """Create the evidence-grounded generator lazily so the API starts without Ollama/Chroma."""

    def factory(session: Session) -> PlanGenerator:
        from universal_video_maker.db.vector_store import ChromaVectorStore
        from universal_video_maker.providers.ollama import (
            OllamaClient,
            OllamaEmbeddingProvider,
        )
        from universal_video_maker.rag import GroundedRAGService

        try:
            store = ChromaVectorStore.persistent(
                settings.chroma_persist_directory, settings.chroma_collection
            )
        except RuntimeError as error:
            raise ServiceUnavailableError(str(error)) from error
        rag = GroundedRAGService(session, store, OllamaEmbeddingProvider.from_settings(settings))
        return OllamaEpisodePlanGenerator(session, rag, OllamaClient.from_settings(settings))

    return factory


class PlanRequest(BaseModel):
    topic_id: UUID


class EpisodePlanRead(BaseModel):
    episode_id: UUID
    revision: int
    created_at: datetime
    plan: dict[str, Any]


class EpisodeRead(BaseModel):
    id: UUID
    topic_id: UUID
    topic_title: str
    title: str
    status: str
    target_publish_date: date | None
    latest_revision: int | None
    approvals: int
    latest_render_status: str | None
    created_at: datetime


@router.get("", response_model=list[EpisodeRead])
def list_episodes(
    session: DatabaseSession,
    principal: CurrentPrincipal,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[EpisodeRead]:
    require_permission(principal, Permission.EPISODES_READ)
    revisions = (
        select(
            EpisodeRevision.episode_id.label("episode_id"),
            func.max(EpisodeRevision.revision).label("latest_revision"),
        )
        .group_by(EpisodeRevision.episode_id)
        .subquery()
    )
    approvals = (
        select(
            EpisodeRevision.episode_id.label("episode_id"),
            func.count(Approval.id).label("approvals"),
        )
        .join(Approval, Approval.revision_id == EpisodeRevision.id)
        .group_by(EpisodeRevision.episode_id)
        .subquery()
    )
    rows = session.execute(
        select(
            Episode,
            Topic.title,
            revisions.c.latest_revision,
            func.coalesce(approvals.c.approvals, 0),
        )
        .join(Topic, Topic.id == Episode.topic_id)
        .outerjoin(revisions, revisions.c.episode_id == Episode.id)
        .outerjoin(approvals, approvals.c.episode_id == Episode.id)
        .order_by(Episode.created_at.desc(), Episode.id)
        .limit(limit)
    ).all()

    result: list[EpisodeRead] = []
    for episode, topic_title, latest_revision, approval_count in rows:
        render_status = session.scalar(
            select(RenderJob.status)
            .where(RenderJob.episode_id == episode.id)
            .order_by(RenderJob.created_at.desc())
            .limit(1)
        )
        result.append(
            EpisodeRead(
                id=episode.id,
                topic_id=episode.topic_id,
                topic_title=topic_title,
                title=episode.title,
                status=episode.status,
                target_publish_date=episode.target_publish_date,
                latest_revision=latest_revision,
                approvals=int(approval_count),
                latest_render_status=render_status,
                created_at=episode.created_at,
            )
        )
    return result


@router.post("/plan", response_model=EpisodePlanRead, status_code=status.HTTP_201_CREATED)
def generate_plan(
    body: PlanRequest,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> EpisodePlanRead:
    require_permission(principal, Permission.EPISODES_WRITE)
    topic = session.get(Topic, body.topic_id)
    if topic is None:
        raise NotFoundError(f"Topic {body.topic_id} does not exist.")
    if topic.status != "ready_to_plan":
        raise ConflictError("Only approved (ready to plan) topics can be planned.")

    factory: PlanGeneratorFactory = request.app.state.plan_generator_factory
    try:
        draft = factory(session).generate(topic.id)
    except NoGroundedEvidenceError as error:
        raise ConflictError(
            "No verified evidence exists for this topic yet. Verify its claims first."
        ) from error
    except OllamaClientError as error:
        raise ServiceUnavailableError(
            "The local AI model (Ollama) could not produce a valid plan. "
            "Check that Ollama is running and the models are installed."
        ) from error

    existing_draft = session.scalar(
        select(Episode.id)
        .where(Episode.topic_id == topic.id, Episode.status == "draft")
        .order_by(Episode.created_at.desc())
        .limit(1)
    )
    draft = replace(draft, episode_id=existing_draft)
    planner_id = ensure_internal_user(session, principal)
    try:
        revision = EpisodePlannerService(session).create_plan(
            principal=principal, planner_id=planner_id, draft=draft
        )
    except ValueError as error:
        raise ConflictError(str(error)) from error
    session.commit()
    session.refresh(revision)
    return EpisodePlanRead(
        episode_id=revision.episode_id,
        revision=revision.revision,
        created_at=revision.created_at,
        plan=revision.content,
    )


class ApprovalRequest(BaseModel):
    decision: Literal["approved", "rejected", "changes_requested"]
    note: str = Field(min_length=1, max_length=1000)

    @field_validator("note")
    @classmethod
    def _strip_note(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A review note is required.")
        return value


class ApprovalResult(BaseModel):
    id: UUID
    episode_id: UUID
    revision: int
    stage: str
    decision: str
    note: str | None
    created_at: datetime


@router.post(
    "/{episode_id}/approvals",
    response_model=ApprovalResult,
    status_code=status.HTTP_201_CREATED,
)
def record_plan_approval(
    episode_id: UUID,
    body: ApprovalRequest,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> ApprovalResult:
    """Record a human decision on the latest plan revision of an episode."""
    require_permission(principal, Permission.EPISODES_APPROVE)
    episode = session.get(Episode, episode_id)
    if episode is None:
        raise NotFoundError(f"Episode {episode_id} does not exist.")
    revision = session.scalar(
        select(EpisodeRevision)
        .where(EpisodeRevision.episode_id == episode_id)
        .order_by(EpisodeRevision.revision.desc())
        .limit(1)
    )
    if revision is None:
        raise ConflictError("This episode has no plan revision to review yet.")

    reviewer_id = ensure_internal_user(session, principal)
    approval = Approval(
        revision_id=revision.id,
        reviewer_id=reviewer_id,
        stage="plan",
        decision=body.decision,
        note=body.note,
    )
    session.add(approval)
    session.flush()
    session.add(
        AuditEvent(
            actor_id=reviewer_id,
            object_type="episode_revision",
            object_id=revision.id,
            action=f"episode.plan_{body.decision}",
            details={
                "episode_id": str(episode.id),
                "revision": revision.revision,
                "approval_id": str(approval.id),
            },
        )
    )
    session.commit()
    session.refresh(approval)
    return ApprovalResult(
        id=approval.id,
        episode_id=episode.id,
        revision=revision.revision,
        stage=approval.stage,
        decision=approval.decision,
        note=approval.note,
        created_at=approval.created_at,
    )


@router.get("/{episode_id}/plan", response_model=EpisodePlanRead)
def get_latest_plan(
    episode_id: UUID,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> EpisodePlanRead:
    require_permission(principal, Permission.EPISODES_READ)
    revision = session.scalar(
        select(EpisodeRevision)
        .where(EpisodeRevision.episode_id == episode_id)
        .order_by(EpisodeRevision.revision.desc())
        .limit(1)
    )
    if revision is None:
        raise NotFoundError("This episode has no plan revision yet.")
    return EpisodePlanRead(
        episode_id=revision.episode_id,
        revision=revision.revision,
        created_at=revision.created_at,
        plan=revision.content,
    )

