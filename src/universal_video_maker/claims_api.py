from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from universal_video_maker.db.models import Claim, ClaimEvidence, ResearchRecord, Source, Topic
from universal_video_maker.errors import NotFoundError
from universal_video_maker.fact_check import (
    ClaimCategory,
    FactCheckService,
    ReviewDecision,
)
from universal_video_maker.identity import CurrentPrincipal
from universal_video_maker.security import Permission, require_permission
from universal_video_maker.topics_api import DatabaseSession
from universal_video_maker.user_mapping import ensure_internal_user

router = APIRouter(prefix="/claims", tags=["claims"])


class EvidenceRead(BaseModel):
    source_id: UUID
    title: str
    publisher: str
    url: str
    support_type: str
    excerpt: str
    independence_group: int


class ClaimRead(BaseModel):
    id: UUID
    topic_id: UUID
    topic_title: str
    claim_text: str
    category: str
    verification_status: str
    reviewed_at: datetime | None
    review_note: str | None
    evidence_count: int
    supporting_source_count: int
    independent_supporting_source_count: int
    contradicting_source_count: int
    has_conflicting_evidence: bool
    sources: list[EvidenceRead]


class ClaimReviewRequest(BaseModel):
    decision: ReviewDecision
    category: ClaimCategory
    note: str = Field(min_length=1, max_length=2000)

    model_config = ConfigDict(str_strip_whitespace=True)


def _claim_read(
    session: DatabaseSession, claim: Claim, topic: Topic, service: FactCheckService
) -> ClaimRead:
    assessment = service.assess_claim(claim.id)
    groups = {item.source_id: item.independence_group for item in assessment.evidence_sources}
    evidence_rows = session.execute(
        select(ClaimEvidence, Source)
        .join(Source, Source.id == ClaimEvidence.source_id)
        .where(ClaimEvidence.claim_id == claim.id)
        .order_by(Source.publisher, Source.canonical_url)
    ).all()
    note = claim.review_metadata.get("note") if claim.review_metadata else None
    return ClaimRead(
        id=claim.id,
        topic_id=topic.id,
        topic_title=topic.title,
        claim_text=claim.claim_text,
        category=claim.category,
        verification_status=claim.verification_status,
        reviewed_at=claim.reviewed_at,
        review_note=note if isinstance(note, str) else None,
        evidence_count=assessment.evidence_count,
        supporting_source_count=assessment.supporting_source_count,
        independent_supporting_source_count=assessment.independent_supporting_source_count,
        contradicting_source_count=assessment.contradicting_source_count,
        has_conflicting_evidence=assessment.has_conflicting_evidence,
        sources=[
            EvidenceRead(
                source_id=source.id,
                title=source.title,
                publisher=source.publisher,
                url=source.canonical_url,
                support_type=evidence.support_type,
                excerpt=evidence.excerpt,
                independence_group=groups.get(source.id, 0),
            )
            for evidence, source in evidence_rows
        ],
    )


def _load_claim(session: DatabaseSession, claim_id: UUID) -> tuple[Claim, Topic]:
    row = session.execute(
        select(Claim, Topic)
        .join(ResearchRecord, ResearchRecord.id == Claim.research_record_id)
        .join(Topic, Topic.id == ResearchRecord.topic_id)
        .where(Claim.id == claim_id)
    ).first()
    if row is None:
        raise NotFoundError(f"Claim {claim_id} does not exist.")
    return row[0], row[1]


@router.get("", response_model=list[ClaimRead])
def list_claims(
    session: DatabaseSession,
    principal: CurrentPrincipal,
    verification_status: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ClaimRead]:
    require_permission(principal, Permission.TOPICS_READ)
    statement = (
        select(Claim, Topic)
        .join(ResearchRecord, ResearchRecord.id == Claim.research_record_id)
        .join(Topic, Topic.id == ResearchRecord.topic_id)
        .order_by(Claim.created_at.desc(), Claim.id)
        .limit(limit)
    )
    if verification_status:
        statement = statement.where(Claim.verification_status == verification_status)
    service = FactCheckService(session)
    rows = session.execute(statement)
    return [_claim_read(session, claim, topic, service) for claim, topic in rows]


@router.post("/{claim_id}/review", response_model=ClaimRead)
def review_claim(
    claim_id: UUID,
    request: ClaimReviewRequest,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> ClaimRead:
    require_permission(principal, Permission.CLAIMS_REVIEW)
    reviewer_id = ensure_internal_user(session, principal)
    service = FactCheckService(session)
    claim = service.review_claim(
        principal=principal,
        reviewer_id=reviewer_id,
        claim_id=claim_id,
        decision=request.decision,
        category=request.category,
        note=request.note,
    )
    session.commit()
    claim, topic = _load_claim(session, claim.id)
    return _claim_read(session, claim, topic, service)
