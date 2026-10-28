from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from universal_video_maker.config import Settings
from universal_video_maker.db.models import (
    Approval,
    Asset,
    AuditEvent,
    Episode,
    EpisodeRevision,
    RenderJob,
    Scene,
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
from universal_video_maker.providers.minio_storage import MinioObjectStore, MinioStorageError
from universal_video_maker.providers.ollama import OllamaClientError
from universal_video_maker.rendering import (
    ArtifactInspectionError,
    ArtifactInspector,
    MinioArtifactInspector,
    RenderArtifactDraft,
    RenderingService,
    RenderOutputBundle,
)
from universal_video_maker.runpod import RunPodClientError, RunPodJobService, RunPodServerlessClient
from universal_video_maker.security import Permission, require_permission
from universal_video_maker.storyboard import (
    StoryboardDraft,
    StoryboardSceneDraft,
    StoryboardService,
)
from universal_video_maker.topics_api import DatabaseSession
from universal_video_maker.user_mapping import ensure_internal_user

router = APIRouter(prefix="/episodes", tags=["episodes"])


class PlanGenerator(Protocol):
    def generate(self, topic_id: UUID, *, query: str | None = None) -> EpisodePlanDraft: ...


PlanGeneratorFactory = Callable[[Session], PlanGenerator]
RenderClientFactory = Callable[[Settings], RunPodServerlessClient]
ObjectStoreFactory = Callable[[Settings], MinioObjectStore]
ArtifactInspectorFactory = Callable[[Settings, MinioObjectStore], ArtifactInspector]


def default_object_store(settings: Settings) -> MinioObjectStore:
    return MinioObjectStore.from_settings(settings)


def default_artifact_inspector(
    settings: Settings, store: MinioObjectStore
) -> ArtifactInspector:
    return MinioArtifactInspector(store)


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
        # Verified claims are only searchable once indexed; sync before planning.
        rag.index_approved_evidence()
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
    stage: Literal["none", "plan", "storyboard"] = "none"
    plan_approved: bool = False
    render_approved: bool = False


def _revisions_newest_first(session: Session, episode_id: UUID) -> list[EpisodeRevision]:
    return list(
        session.scalars(
            select(EpisodeRevision)
            .where(EpisodeRevision.episode_id == episode_id)
            .order_by(EpisodeRevision.revision.desc())
        )
    )


def _is_storyboard(revision: EpisodeRevision) -> bool:
    return revision.content.get("kind") == "storyboard"


def _latest_plan_revision(session: Session, episode_id: UUID) -> EpisodeRevision | None:
    return next(
        (r for r in _revisions_newest_first(session, episode_id) if not _is_storyboard(r)), None
    )


def _has_approval(session: Session, revision_id: UUID, stage: str) -> bool:
    return (
        session.scalar(
            select(Approval.id)
            .where(
                Approval.revision_id == revision_id,
                Approval.stage == stage,
                Approval.decision == "approved",
            )
            .limit(1)
        )
        is not None
    )


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
        revisions_desc = _revisions_newest_first(session, episode.id)
        stage: Literal["none", "plan", "storyboard"] = "none"
        plan_approved = render_approved = False
        if revisions_desc:
            stage = "storyboard" if _is_storyboard(revisions_desc[0]) else "plan"
            plan_revision = next((r for r in revisions_desc if not _is_storyboard(r)), None)
            if plan_revision is not None:
                plan_approved = _has_approval(session, plan_revision.id, "plan")
            if stage == "storyboard":
                render_approved = _has_approval(session, revisions_desc[0].id, "render")
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
                stage=stage,
                plan_approved=plan_approved,
                render_approved=render_approved,
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
    stage: Literal["plan", "render"] = "plan"

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
    """Record a human decision on the latest plan, or on the storyboard for the render stage."""
    require_permission(principal, Permission.EPISODES_APPROVE)
    episode = session.get(Episode, episode_id)
    if episode is None:
        raise NotFoundError(f"Episode {episode_id} does not exist.")
    if body.stage == "render":
        revisions = _revisions_newest_first(session, episode_id)
        revision = revisions[0] if revisions else None
        if revision is None or not _is_storyboard(revision):
            raise ConflictError("Create a storyboard before approving this episode for render.")
    else:
        revision = _latest_plan_revision(session, episode_id)
        if revision is None:
            raise ConflictError("This episode has no plan revision to review yet.")

    reviewer_id = ensure_internal_user(session, principal)
    approval = Approval(
        revision_id=revision.id,
        reviewer_id=reviewer_id,
        stage=body.stage,
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
            action=f"episode.{body.stage}_{body.decision}",
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
    revision = _latest_plan_revision(session, episode_id)
    if revision is None:
        raise NotFoundError("This episode has no plan revision yet.")
    return EpisodePlanRead(
        episode_id=revision.episode_id,
        revision=revision.revision,
        created_at=revision.created_at,
        plan=revision.content,
    )


class StoryboardResult(BaseModel):
    episode_id: UUID
    revision: int
    scene_count: int


@router.post(
    "/{episode_id}/storyboard",
    response_model=StoryboardResult,
    status_code=status.HTTP_201_CREATED,
)
def create_storyboard(
    episode_id: UUID,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> StoryboardResult:
    """Turn the approved plan's beats into storyboard scenes (visual directions only)."""
    require_permission(principal, Permission.EPISODES_WRITE)
    if session.get(Episode, episode_id) is None:
        raise NotFoundError(f"Episode {episode_id} does not exist.")
    revisions = _revisions_newest_first(session, episode_id)
    if not revisions:
        raise ConflictError("This episode has no plan revision yet.")
    plan = revisions[0]
    if _is_storyboard(plan):
        raise ConflictError("This episode already has a storyboard for its latest plan.")
    if not _has_approval(session, plan.id, "plan"):
        raise ConflictError("Approve the latest plan before creating a storyboard.")
    beats = plan.content.get("beats")
    if not isinstance(beats, list) or not beats:
        raise ConflictError("The latest plan has no beats to storyboard.")

    scenes = tuple(
        StoryboardSceneDraft(
            visual_description=f"{beat['title']}: {beat['purpose']}",
            camera_direction="Steady medium shot with a gentle push-in; minimal on-screen text.",
            visual_prompt=(
                f"Clear, documentary-style illustration for '{beat['title']}'. "
                f"Narration context: {beat['narration']['text']}"
            )[:3900],
        )
        for beat in beats
    )
    editor_id = ensure_internal_user(session, principal)
    try:
        revision = StoryboardService(session).create_storyboard(
            principal=principal,
            editor_id=editor_id,
            draft=StoryboardDraft(plan_revision_id=plan.id, scenes=scenes),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise ConflictError(f"Could not build a storyboard from this plan: {error}") from error
    session.commit()
    return StoryboardResult(
        episode_id=episode_id, revision=revision.revision, scene_count=len(scenes)
    )


class RenderRequest(BaseModel):
    estimated_cost_usd: Decimal = Field(gt=0, le=Decimal("1000"), max_digits=10, decimal_places=2)


class RenderResult(BaseModel):
    job_id: UUID
    episode_id: UUID
    status: str
    remote_job_id: str | None


@router.post(
    "/{episode_id}/render",
    response_model=RenderResult,
    status_code=status.HTTP_201_CREATED,
)
def submit_render(
    episode_id: UUID,
    body: RenderRequest,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> RenderResult:
    """Queue a render job for the render-approved storyboard and dispatch it to RunPod."""
    require_permission(principal, Permission.RENDER_SUBMIT)
    settings: Settings = request.app.state.settings
    if not settings.runpod_serverless_endpoint_id:
        raise ServiceUnavailableError(
            "RunPod is not configured. Set UVM_RUNPOD_SERVERLESS_ENDPOINT_ID in .env and "
            "restart the API."
        )
    try:
        client = request.app.state.render_client_factory(settings)
    except ValueError as error:
        raise ServiceUnavailableError(str(error)) from error

    if session.get(Episode, episode_id) is None:
        raise NotFoundError(f"Episode {episode_id} does not exist.")
    revisions = _revisions_newest_first(session, episode_id)
    storyboard = revisions[0] if revisions else None
    if storyboard is None or not _is_storyboard(storyboard):
        raise ConflictError("Create and approve a storyboard before rendering.")
    scenes = list(
        session.scalars(
            select(Scene).where(Scene.revision_id == storyboard.id).order_by(Scene.scene_number)
        )
    )
    plan = _latest_plan_revision(session, episode_id)
    plan_shorts = plan.content.get("shorts", []) if plan is not None else []
    payload = {
        "episode_id": str(episode_id),
        "storyboard_revision_id": str(storyboard.id),
        "target_duration_seconds": storyboard.content.get("target_duration_seconds"),
        "shorts": [
            {
                "title": short.get("title"),
                "hook": short.get("hook"),
                "duration_seconds": short.get("duration_seconds"),
                "narration": short.get("narration", {}).get("text"),
                "call_to_action": short.get("call_to_action"),
            }
            for short in plan_shorts
        ],
        "scenes": [
            {
                "scene_number": scene.scene_number,
                "start_seconds": scene.content.get("start_seconds"),
                "duration_seconds": scene.content.get("duration_seconds"),
                "narration": scene.content.get("narration", {}).get("text"),
                "visual_description": scene.content.get("visual_description"),
                "camera_direction": scene.content.get("camera_direction"),
                "visual_prompt": scene.content.get("visual_prompt"),
                "required_assets": scene.content.get("required_assets", []),
            }
            for scene in scenes
        ],
    }

    actor_id = ensure_internal_user(session, principal)
    service = RunPodJobService(session, settings)
    job = service.create_job(
        principal=principal,
        actor_id=actor_id,
        storyboard_revision_id=storyboard.id,
        input_payload=payload,
        estimated_cost_usd=body.estimated_cost_usd,
    )
    try:
        service.dispatch(job.id, client=client)
    except (RunPodClientError, ValueError) as error:
        session.rollback()
        raise ServiceUnavailableError(f"RunPod rejected the job: {error}") from error
    session.commit()
    session.refresh(job)
    return RenderResult(
        job_id=job.id, episode_id=episode_id, status=job.status, remote_job_id=job.remote_job_id
    )


class AssetRead(BaseModel):
    kind: str
    variant: str
    download_url: str | None


class RenderStatusRead(BaseModel):
    job_id: UUID | None
    status: str | None
    failure_reason: str | None = None
    assets: list[AssetRead] = []
    message: str | None = None


def _build_object_store(request: Request) -> Any:
    try:
        return request.app.state.object_store_factory(request.app.state.settings)
    except ValueError as error:
        raise ServiceUnavailableError(str(error)) from error


def _render_status(
    session: Session, request: Request, episode_id: UUID, message: str | None = None
) -> RenderStatusRead:
    job = session.scalar(
        select(RenderJob)
        .where(RenderJob.episode_id == episode_id)
        .order_by(RenderJob.created_at.desc())
        .limit(1)
    )
    if job is None:
        return RenderStatusRead(job_id=None, status=None, message=message)
    assets = list(session.scalars(select(Asset).where(Asset.render_job_id == job.id)))
    reads: list[AssetRead] = []
    if assets:
        store = _build_object_store(request)
        for asset in assets:
            try:
                url: str | None = store.presigned_get_url(asset.storage_uri)
            except (MinioStorageError, ValueError):
                url = None
            reads.append(
                AssetRead(
                    kind=asset.kind,
                    variant=str(asset.provenance.get("variant", asset.kind)),
                    download_url=url,
                )
            )
    return RenderStatusRead(
        job_id=job.id,
        status=job.status,
        failure_reason=job.failure_reason,
        assets=reads,
        message=message,
    )


@router.get("/{episode_id}/render", response_model=RenderStatusRead)
def get_render_status(
    episode_id: UUID,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> RenderStatusRead:
    """Return the latest render job and download links for its registered outputs."""
    require_permission(principal, Permission.EPISODES_READ)
    return _render_status(session, request, episode_id)


@router.post("/{episode_id}/render/refresh", response_model=RenderStatusRead)
def refresh_render(
    episode_id: UUID,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> RenderStatusRead:
    """Poll RunPod and, once complete, verify and register the uploaded MinIO artifacts."""
    require_permission(principal, Permission.RENDER_SUBMIT)
    settings: Settings = request.app.state.settings
    job = session.scalar(
        select(RenderJob)
        .where(RenderJob.episode_id == episode_id)
        .order_by(RenderJob.created_at.desc())
        .limit(1)
    )
    if job is None:
        raise NotFoundError("This episode has no render job yet.")
    message: str | None = None
    if job.status in {"submitted", "running", "cancelling"}:
        try:
            client = request.app.state.render_client_factory(settings)
            RunPodJobService(session, settings).refresh_status(job.id, client=client)
        except (RunPodClientError, ValueError) as error:
            raise ServiceUnavailableError(f"Could not read RunPod status: {error}") from error
        session.commit()
        session.refresh(job)
    has_assets = (
        session.scalar(select(Asset.id).where(Asset.render_job_id == job.id).limit(1)) is not None
    )
    if job.status == "completed" and not has_assets:
        store = _build_object_store(request)
        try:
            store.ensure_bucket()
            bundle = _bundle_from_job(job)
            inspector = request.app.state.artifact_inspector_factory(settings, store)
            RenderingService(session, inspector).register_outputs(bundle)
        except (ValueError, ArtifactInspectionError, MinioStorageError) as error:
            session.rollback()
            message = f"Render finished but its outputs could not be verified: {error}"
        else:
            session.commit()
    return _render_status(session, request, episode_id, message)


@router.post("/{episode_id}/render/cancel", response_model=RenderStatusRead)
def cancel_render(
    episode_id: UUID,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> RenderStatusRead:
    """Cancel the episode's active RunPod job so a new render can be submitted."""
    require_permission(principal, Permission.RENDER_SUBMIT)
    settings: Settings = request.app.state.settings
    job = session.scalar(
        select(RenderJob)
        .where(RenderJob.episode_id == episode_id)
        .order_by(RenderJob.created_at.desc())
        .limit(1)
    )
    if job is None:
        raise NotFoundError("This episode has no render job yet.")
    try:
        client = request.app.state.render_client_factory(settings)
        RunPodJobService(session, settings).cancel(
            job.id,
            principal=principal,
            actor_id=ensure_internal_user(session, principal),
            client=client,
        )
    except (RunPodClientError, ValueError) as error:
        session.rollback()
        raise ServiceUnavailableError(f"Could not cancel the RunPod job: {error}") from error
    session.commit()
    return _render_status(session, request, episode_id)


def _bundle_from_job(job: RenderJob) -> RenderOutputBundle:
    attempts = job.parameters.get("attempts") or []
    output = attempts[-1].get("output") if attempts else None
    artifacts = output.get("artifacts") if isinstance(output, dict) else None
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("The RunPod worker returned no output.artifacts list.")
    return RenderOutputBundle(
        job_id=job.id,
        artifacts=tuple(
            RenderArtifactDraft(
                kind=str(item["kind"]),
                variant=str(item["variant"]),
                storage_uri=str(item["storage_uri"]),
                checksum=str(item["checksum"]),
            )
            for item in artifacts
        ),
    )

