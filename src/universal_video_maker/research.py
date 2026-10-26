"""Research record creation from captured, provenance-preserving source material."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    AuditEvent,
    Claim,
    ClaimEvidence,
    ResearchRecord,
    ResearchSource,
    Source,
    Topic,
)
from universal_video_maker.errors import ConflictError, NotFoundError

SupportType = Literal["supports", "contradicts", "context"]


@dataclass(frozen=True)
class CapturedSource:
    """Source metadata and bytes captured by an upstream, explicitly trusted adapter.

    The research service never fetches ``canonical_url``. The caller is
    responsible for capture and for persisting the bytes at ``storage_uri``.
    """

    canonical_url: str
    title: str
    publisher: str
    source_type: str
    snapshot_bytes: bytes
    storage_uri: str
    published_at: date | None = None
    lineage_source_id: UUID | None = None

    def __post_init__(self) -> None:
        parsed = urlsplit(self.canonical_url.strip())
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError("Research source URLs must be absolute HTTPS URLs.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Research source URLs cannot include credentials.")
        if not self.title.strip() or len(self.title.strip()) > 500:
            raise ValueError("Source title must contain between 1 and 500 characters.")
        if not self.publisher.strip() or len(self.publisher.strip()) > 250:
            raise ValueError("Source publisher must contain between 1 and 250 characters.")
        if not self.source_type.strip() or len(self.source_type.strip()) > 48:
            raise ValueError("Source type must contain between 1 and 48 characters.")
        if not self.snapshot_bytes:
            raise ValueError("Captured source snapshot cannot be empty.")
        if not self.storage_uri.strip():
            raise ValueError("A durable source snapshot storage URI is required.")
        try:
            self.snapshot_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Captured source snapshot must be UTF-8 text.") from error

        normalized_url = urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path or "/",
                parsed.query,
                "",
            )
        )
        object.__setattr__(self, "canonical_url", normalized_url)
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "publisher", self.publisher.strip())
        object.__setattr__(self, "source_type", self.source_type.strip())
        object.__setattr__(self, "storage_uri", self.storage_uri.strip())

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.snapshot_bytes).hexdigest()

    @property
    def text(self) -> str:
        return self.snapshot_bytes.decode("utf-8")


@dataclass(frozen=True)
class EvidenceDraft:
    source_url: str
    excerpt: str
    support_type: SupportType
    source_lineage_key: str | None = None

    def __post_init__(self) -> None:
        if not self.excerpt.strip():
            raise ValueError("Evidence excerpt cannot be empty.")
        if self.support_type not in {"supports", "contradicts", "context"}:
            raise ValueError("Evidence support type must be supports, contradicts, or context.")
        if self.source_lineage_key is not None and len(self.source_lineage_key) > 128:
            raise ValueError("Source lineage key cannot exceed 128 characters.")


@dataclass(frozen=True)
class CandidateClaim:
    claim_text: str
    category: str
    evidence: tuple[EvidenceDraft, ...]

    def __post_init__(self) -> None:
        if not self.claim_text.strip():
            raise ValueError("Candidate claim text cannot be empty.")
        if not self.category.strip() or len(self.category.strip()) > 48:
            raise ValueError("Claim category must contain between 1 and 48 characters.")
        if not self.evidence:
            raise ValueError("Candidate claims require at least one evidence excerpt.")


@dataclass(frozen=True)
class ResearchDraft:
    topic_id: UUID
    sources: tuple[CapturedSource, ...]
    findings: Mapping[str, object]
    open_questions: tuple[str, ...] = ()
    claims: tuple[CandidateClaim, ...] = ()

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError("A research record requires at least one captured source.")
        source_urls = [source.canonical_url for source in self.sources]
        if len(source_urls) != len(set(source_urls)):
            raise ValueError("A research record cannot contain duplicate source URLs.")
        if any(not question.strip() for question in self.open_questions):
            raise ValueError("Open questions cannot be empty.")
        try:
            json.dumps(dict(self.findings))
        except (TypeError, ValueError) as error:
            raise ValueError("Research findings must contain JSON-serializable values.") from error
        known_sources = {source.canonical_url: source for source in self.sources}
        for claim in self.claims:
            evidence_urls = [_canonicalize_url(item.source_url) for item in claim.evidence]
            if len(evidence_urls) != len(set(evidence_urls)):
                raise ValueError("A claim cannot contain duplicate evidence from one source.")
            for evidence, source_url in zip(claim.evidence, evidence_urls, strict=True):
                source = known_sources.get(source_url)
                if source is None:
                    raise ValueError(
                        "Claim evidence must reference a source in this research record."
                    )
                if evidence.excerpt not in source.text:
                    raise ValueError(
                        f"Evidence excerpt is not present in the captured source: {source_url}"
                    )


class ResearchService:
    """Persist a traceable research revision using the caller's transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_research(self, draft: ResearchDraft) -> ResearchRecord:
        topic = self._session.get(Topic, draft.topic_id)
        if topic is None:
            raise NotFoundError(f"Topic {draft.topic_id} does not exist.")

        for captured in draft.sources:
            self._validate_existing_source(captured)
        source_by_url: dict[str, Source] = {}
        for captured in draft.sources:
            source = self._get_or_create_source(captured)
            source_by_url[captured.canonical_url] = source

        revision = self._session.scalar(
            select(func.coalesce(func.max(ResearchRecord.revision), 0)).where(
                ResearchRecord.topic_id == draft.topic_id
            )
        )
        record = ResearchRecord(
            topic_id=draft.topic_id,
            revision=int(revision or 0) + 1,
            status="draft",
            findings=dict(draft.findings),
            open_questions=list(draft.open_questions),
        )
        self._session.add(record)
        self._session.flush()

        for captured in draft.sources:
            self._session.add(
                ResearchSource(
                    research_record_id=record.id,
                    source_id=source_by_url[captured.canonical_url].id,
                )
            )

        for candidate in draft.claims:
            claim = Claim(
                research_record_id=record.id,
                claim_text=candidate.claim_text.strip(),
                category=candidate.category.strip(),
                verification_status="unverified",
            )
            self._session.add(claim)
            self._session.flush()
            for evidence in candidate.evidence:
                source_url = _canonicalize_url(evidence.source_url)
                self._session.add(
                    ClaimEvidence(
                        claim_id=claim.id,
                        source_id=source_by_url[source_url].id,
                        excerpt=evidence.excerpt,
                        support_type=evidence.support_type,
                        source_lineage_key=evidence.source_lineage_key,
                    )
                )

        self._session.add(
            AuditEvent(
                object_type="research_record",
                object_id=record.id,
                action="research.created",
                details={
                    "topic_id": str(draft.topic_id),
                    "revision": record.revision,
                    "source_count": len(draft.sources),
                    "claim_count": len(draft.claims),
                },
            )
        )
        self._session.flush()
        return record

    def _get_or_create_source(self, captured: CapturedSource) -> Source:
        existing = self._session.scalar(
            select(Source).where(Source.canonical_url == captured.canonical_url)
        )
        if existing is not None:
            if existing.content_hash != captured.content_hash:
                raise ConflictError(
                    "Source content changed for an existing URL. "
                    "Create a versioned source snapshot before replacing it."
                )
            if (
                existing.title != captured.title
                or existing.publisher != captured.publisher
                or existing.source_type != captured.source_type
            ):
                raise ConflictError(
                    "Source metadata differs for an existing URL and content snapshot."
                )
            if (
                captured.lineage_source_id is not None
                and existing.lineage_source_id != captured.lineage_source_id
            ):
                raise ConflictError(
                    "Source lineage differs for an existing URL and content snapshot."
                )
            return existing

        source = Source(
            canonical_url=captured.canonical_url,
            title=captured.title,
            publisher=captured.publisher,
            published_at=captured.published_at,
            content_hash=captured.content_hash,
            source_type=captured.source_type,
            storage_uri=captured.storage_uri,
            lineage_source_id=captured.lineage_source_id,
        )
        self._session.add(source)
        self._session.flush()
        return source

    def _validate_existing_source(self, captured: CapturedSource) -> None:
        existing = self._session.scalar(
            select(Source).where(Source.canonical_url == captured.canonical_url)
        )
        if existing is not None:
            if existing.content_hash != captured.content_hash:
                raise ConflictError(
                    "Source content changed for an existing URL. "
                    "Create a versioned source snapshot before replacing it."
                )
            if (
                existing.title != captured.title
                or existing.publisher != captured.publisher
                or existing.source_type != captured.source_type
            ):
                raise ConflictError(
                    "Source metadata differs for an existing URL and content snapshot."
                )
            if (
                captured.lineage_source_id is not None
                and existing.lineage_source_id != captured.lineage_source_id
            ):
                raise ConflictError(
                    "Source lineage differs for an existing URL and content snapshot."
                )
        elif (
            captured.lineage_source_id is not None
            and self._session.get(Source, captured.lineage_source_id) is None
        ):
            raise NotFoundError(
                f"Lineage source {captured.lineage_source_id} does not exist."
            )


def _canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Research source URLs must be absolute HTTPS URLs.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Research source URLs cannot include credentials.")
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            parsed.query,
            "",
        )
    )
