from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, distinct, func, select

from universal_video_maker.db.models import (
    Approval,
    AuditEvent,
    Claim,
    Episode,
    EpisodeRevision,
    ResearchRecord,
    ResearchSource,
    Topic,
)
from universal_video_maker.identity import CurrentPrincipal
from universal_video_maker.security import Permission, require_permission
from universal_video_maker.topics_api import DatabaseSession

router = APIRouter(prefix="/governance", tags=["governance"])


class GovernanceSummary(BaseModel):
    topics_total: int
    topics_approved: int
    topics_with_multiple_sources: int
    sources_linked: int
    claims_total: int
    claims_reviewed: int
    claims_verified: int
    episodes_total: int
    episodes_with_approval: int
    approvals_total: int


class ApprovalRead(BaseModel):
    id: UUID
    episode_id: UUID
    episode_title: str
    revision: int
    stage: str
    decision: str
    note: str | None
    created_at: datetime


class AuditEventRead(BaseModel):
    id: UUID
    object_type: str
    object_id: UUID | None
    action: str
    details: dict[str, object]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


def _count(session: DatabaseSession, statement: Select[Any]) -> int:
    return int(session.scalar(statement) or 0)


@router.get("/summary", response_model=GovernanceSummary)
def governance_summary(session: DatabaseSession, principal: CurrentPrincipal) -> GovernanceSummary:
    require_permission(principal, Permission.TOPICS_READ)

    source_counts = (
        select(
            ResearchRecord.topic_id.label("topic_id"),
            func.count(distinct(ResearchSource.source_id)).label("sources"),
        )
        .join(ResearchSource, ResearchSource.research_record_id == ResearchRecord.id)
        .group_by(ResearchRecord.topic_id)
        .subquery()
    )

    return GovernanceSummary(
        topics_total=_count(session, select(func.count(Topic.id))),
        topics_approved=_count(
            session, select(func.count(Topic.id)).where(Topic.status == "approved")
        ),
        topics_with_multiple_sources=_count(
            session,
            select(func.count()).select_from(source_counts).where(source_counts.c.sources >= 2),
        ),
        sources_linked=_count(session, select(func.count(distinct(ResearchSource.source_id)))),
        claims_total=_count(session, select(func.count(Claim.id))),
        claims_reviewed=_count(
            session,
            select(func.count(Claim.id)).where(Claim.verification_status != "unverified"),
        ),
        claims_verified=_count(
            session,
            select(func.count(Claim.id)).where(Claim.verification_status == "verified"),
        ),
        episodes_total=_count(session, select(func.count(Episode.id))),
        episodes_with_approval=_count(
            session,
            select(func.count(distinct(EpisodeRevision.episode_id)))
            .join(Approval, Approval.revision_id == EpisodeRevision.id)
            .where(Approval.decision == "approved"),
        ),
        approvals_total=_count(session, select(func.count(Approval.id))),
    )


@router.get("/approvals", response_model=list[ApprovalRead])
def list_approvals(
    session: DatabaseSession,
    principal: CurrentPrincipal,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ApprovalRead]:
    require_permission(principal, Permission.EPISODES_READ)
    statement = (
        select(Approval, EpisodeRevision, Episode)
        .join(EpisodeRevision, Approval.revision_id == EpisodeRevision.id)
        .join(Episode, EpisodeRevision.episode_id == Episode.id)
        .order_by(Approval.created_at.desc(), Approval.id)
        .limit(limit)
    )
    return [
        ApprovalRead(
            id=approval.id,
            episode_id=episode.id,
            episode_title=episode.title,
            revision=revision.revision,
            stage=approval.stage,
            decision=approval.decision,
            note=approval.note,
            created_at=approval.created_at,
        )
        for approval, revision, episode in session.execute(statement)
    ]


@router.get("/audit-events", response_model=list[AuditEventRead])
def list_audit_events(
    session: DatabaseSession,
    principal: CurrentPrincipal,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AuditEvent]:
    require_permission(principal, Permission.AUDIT_READ)
    statement = (
        select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id).limit(limit)
    )
    return list(session.scalars(statement))
