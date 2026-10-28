"""Evidence assessment and authorized human fact-check decisions."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    AuditEvent,
    Claim,
    ClaimEvidence,
    Source,
    User,
)
from universal_video_maker.errors import ConflictError, ForbiddenError, NotFoundError
from universal_video_maker.security import Permission, Principal, require_permission


class ClaimCategory(StrEnum):
    VERIFIED = "verified"
    HISTORICAL_RECORD = "historical_record"
    SCIENTIFIC_THEORY = "scientific_theory"
    OPINION = "opinion"
    MYTH_OR_LEGEND = "myth_or_legend"
    REJECTED = "rejected"


class ReviewDecision(StrEnum):
    VERIFIED = "verified"
    NEEDS_FOLLOW_UP = "needs_follow_up"
    REJECTED = "rejected"


@dataclass(frozen=True)
class EvidenceSourceAssessment:
    source_id: UUID
    title: str
    publisher: str
    hostname: str
    support_type: str
    lineage_key: str | None
    independence_group: int
    independence_basis: str


@dataclass(frozen=True)
class ClaimAssessment:
    claim_id: UUID
    evidence_count: int
    supporting_source_count: int
    independent_supporting_source_count: int
    contradicting_source_count: int
    context_source_count: int
    has_conflicting_evidence: bool
    is_unsupported: bool
    evidence_sources: tuple[EvidenceSourceAssessment, ...]


def _normalize_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


class FactCheckService:
    """Assess evidence provenance; only a permitted human can record a verdict."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def assess_claim(self, claim_id: UUID) -> ClaimAssessment:
        claim = self._session.get(Claim, claim_id)
        if claim is None:
            raise NotFoundError(f"Claim {claim_id} does not exist.")

        evidence_rows = list(
            self._session.execute(
                select(ClaimEvidence, Source)
                .join(Source, Source.id == ClaimEvidence.source_id)
                .where(ClaimEvidence.claim_id == claim_id)
                .order_by(Source.publisher, Source.canonical_url)
            )
        )
        identities: list[set[str]] = []
        bases: list[str] = []
        for evidence, source in evidence_rows:
            hostname = (urlsplit(source.canonical_url).hostname or "").casefold()
            lineage_key = evidence.source_lineage_key
            source_identities = {
                f"host:{hostname}",
                f"publisher:{_normalize_identity(source.publisher)}",
            }
            basis = "publisher_or_hostname"
            if lineage_key:
                source_identities.add(f"lineage:{_normalize_identity(lineage_key)}")
                basis = "declared_lineage_publisher_or_hostname"
            identities.append(source_identities)
            bases.append(basis)

        parents = list(range(len(identities)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parents[right_root] = left_root

        for index, source_identities in enumerate(identities):
            for previous_index in range(index):
                if source_identities & identities[previous_index]:
                    union(index, previous_index)

        roots = [find(index) for index in range(len(identities))]
        root_to_group: dict[int, int] = {}
        groups = [
            root_to_group.setdefault(root, len(root_to_group))
            for root in roots
        ]

        assessments = tuple(
            EvidenceSourceAssessment(
                source_id=source.id,
                title=source.title,
                publisher=source.publisher,
                hostname=urlsplit(source.canonical_url).hostname or "",
                support_type=evidence.support_type,
                lineage_key=evidence.source_lineage_key,
                independence_group=groups[index],
                independence_basis=bases[index],
            )
            for index, (evidence, source) in enumerate(evidence_rows)
        )

        supporting = [
            item for item in assessments if item.support_type == "supports"
        ]
        contradicting = [
            item for item in assessments if item.support_type == "contradicts"
        ]
        contextual = [item for item in assessments if item.support_type == "context"]
        return ClaimAssessment(
            claim_id=claim_id,
            evidence_count=len(assessments),
            supporting_source_count=len(supporting),
            independent_supporting_source_count=len(
                {item.independence_group for item in supporting}
            ),
            contradicting_source_count=len(contradicting),
            context_source_count=len(contextual),
            has_conflicting_evidence=bool(supporting and contradicting),
            is_unsupported=not supporting,
            evidence_sources=assessments,
        )

    def review_claim(
        self,
        *,
        principal: Principal,
        reviewer_id: UUID,
        claim_id: UUID,
        decision: ReviewDecision,
        category: ClaimCategory,
        note: str,
    ) -> Claim:
        decision = ReviewDecision(decision)
        category = ClaimCategory(category)
        require_permission(principal, Permission.CLAIMS_REVIEW)
        if principal.subject != str(reviewer_id):
            raise ForbiddenError("Reviewer identity must match the authenticated principal.")
        if not note.strip():
            raise ValueError("A review note is required.")

        claim = self._session.get(Claim, claim_id)
        if claim is None:
            raise NotFoundError(f"Claim {claim_id} does not exist.")
        reviewer = self._session.get(User, reviewer_id)
        if reviewer is None:
            raise NotFoundError(f"Reviewer {reviewer_id} does not exist.")
        if reviewer.status != "active":
            raise ForbiddenError("Inactive users cannot review claims.")
        if reviewer.role not in {role.value for role in principal.roles}:
            raise ForbiddenError("Reviewer roles do not match the authenticated principal.")

        assessment = self.assess_claim(claim_id)
        if decision is ReviewDecision.VERIFIED:
            if assessment.has_conflicting_evidence:
                raise ConflictError(
                    "Conflicting supporting and contradicting evidence requires follow-up."
                )
            if assessment.independent_supporting_source_count < 1:
                raise ConflictError(
                    "Verification requires supporting evidence from at least one source."
                )
            if category is ClaimCategory.REJECTED:
                raise ConflictError("A verified claim cannot be classified as rejected.")
        elif decision is ReviewDecision.REJECTED:
            if assessment.has_conflicting_evidence:
                raise ConflictError(
                    "Conflicting evidence requires follow-up, not immediate rejection."
                )
            if assessment.contradicting_source_count == 0:
                raise ConflictError(
                    "A claim can be rejected only when contradicting evidence is recorded."
                )
            if category is not ClaimCategory.REJECTED:
                raise ConflictError("A rejected decision requires the rejected category.")
        elif decision is ReviewDecision.NEEDS_FOLLOW_UP:
            if category is ClaimCategory.VERIFIED:
                raise ConflictError(
                    "A claim needing follow-up cannot be classified as verified."
                )
            if category is ClaimCategory.REJECTED:
                raise ConflictError(
                    "A claim needing follow-up cannot be classified as rejected."
                )

        reviewed_at = datetime.now(UTC)
        previous_status = claim.verification_status
        review_metadata = {
            "decision": decision.value,
            "category": category.value,
            "note": note.strip(),
            "evidence_assessment": {
                "evidence_count": assessment.evidence_count,
                "supporting_source_count": assessment.supporting_source_count,
                "independent_supporting_source_count": (
                    assessment.independent_supporting_source_count
                ),
                "contradicting_source_count": assessment.contradicting_source_count,
                "context_source_count": assessment.context_source_count,
                "has_conflicting_evidence": assessment.has_conflicting_evidence,
                "is_unsupported": assessment.is_unsupported,
                "sources": [
                    {
                        **asdict(item),
                        "source_id": str(item.source_id),
                    }
                    for item in assessment.evidence_sources
                ],
            },
        }
        claim.category = category.value
        claim.verification_status = decision.value
        claim.reviewer_id = reviewer_id
        claim.reviewed_at = reviewed_at
        claim.review_metadata = review_metadata
        self._session.add(
            AuditEvent(
                actor_id=reviewer_id,
                object_type="claim",
                object_id=claim.id,
                action="claim.fact_checked",
                details={
                    **review_metadata,
                    "previous_status": previous_status,
                    "reviewed_at": reviewed_at.isoformat(),
                },
            )
        )
        self._session.flush()
        return claim
