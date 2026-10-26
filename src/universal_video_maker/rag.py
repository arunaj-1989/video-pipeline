"""Grounded retrieval over human-verified claim evidence."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import Claim, ClaimEvidence, Source
from universal_video_maker.db.vector_store import (
    ChromaVectorStore,
    VectorDocument,
    VectorSearchResult,
)

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120


class EmbeddingProvider(Protocol):
    """Provide compatible document and query embeddings from one model."""

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


@dataclass(frozen=True)
class EvidenceChunk:
    id: str
    claim_id: UUID
    source_id: UUID
    text: str
    claim_text: str
    category: str
    support_type: str
    source_title: str
    publisher: str
    canonical_url: str
    chunk_index: int


@dataclass(frozen=True)
class RetrievedEvidence:
    chunk_id: str
    claim_id: UUID
    source_id: UUID
    text: str
    claim_text: str
    category: str
    support_type: str
    source_title: str
    publisher: str
    canonical_url: str
    distance: float | None


@dataclass(frozen=True)
class EvidenceCitation:
    chunk_id: str
    quote: str


@dataclass(frozen=True)
class GroundedStatement:
    text: str
    citations: tuple[EvidenceCitation, ...]


@dataclass(frozen=True)
class GroundedAnswer:
    statements: tuple[GroundedStatement, ...]


@dataclass(frozen=True)
class GroundingValidation:
    valid: bool
    issues: tuple[str, ...]


def validate_grounded_answer(
    answer: GroundedAnswer,
    *,
    context: Sequence[RetrievedEvidence],
) -> GroundingValidation:
    context_by_id = {item.chunk_id: item for item in context}
    issues: list[str] = []
    if not answer.statements:
        issues.append("Answer must contain at least one statement.")
    for statement_index, statement in enumerate(answer.statements):
        if not statement.text.strip():
            issues.append(f"Statement {statement_index + 1} is empty.")
        if not statement.citations:
            issues.append(f"Statement {statement_index + 1} has no citations.")
            continue
        for citation in statement.citations:
            retrieved = context_by_id.get(citation.chunk_id)
            if retrieved is None:
                issues.append(
                    f"Statement {statement_index + 1} cites evidence outside retrieved context."
                )
                continue
            if not citation.quote.strip() or citation.quote not in retrieved.text:
                issues.append(
                    f"Statement {statement_index + 1} contains a quote not present "
                    "in its cited evidence."
                )
    return GroundingValidation(valid=not issues, issues=tuple(issues))


def chunk_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[str, ...]:
    if chunk_size < 1:
        raise ValueError("Chunk size must be at least one character.")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk overlap must be non-negative and smaller than chunk size.")
    normalized = " ".join(text.split())
    if not normalized:
        return ()

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(" ", start + chunk_size // 2, end)
            if boundary > start:
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)
    return tuple(chunks)


def _chunk_id(claim_id: UUID, source_id: UUID, index: int, text: str) -> str:
    digest = hashlib.sha256(
        f"{claim_id}:{source_id}:{index}:{text}".encode()
    ).hexdigest()
    return f"evidence-{digest}"


class GroundedRAGService:
    """Index and retrieve only evidence attached to currently verified claims.

    The service does not call an LLM or decide whether a retrieved passage
    semantically supports a generated statement.
    """

    def __init__(
        self,
        session: Session,
        vector_store: ChromaVectorStore,
        embedding_provider: EmbeddingProvider,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        if chunk_size < 1:
            raise ValueError("Chunk size must be at least one character.")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("Chunk overlap must be non-negative and smaller than chunk size.")
        self._session = session
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def index_approved_evidence(self) -> int:
        chunks = self._current_eligible_chunks()
        if not chunks:
            return 0

        embeddings = self._embed([chunk.text for chunk in chunks])
        documents = [
            VectorDocument(
                id=chunk.id,
                text=chunk.text,
                embedding=embedding,
                metadata={
                    "claim_id": str(chunk.claim_id),
                    "source_id": str(chunk.source_id),
                    "claim_status": "verified",
                    "support_type": chunk.support_type,
                    "source_title": chunk.source_title,
                    "publisher": chunk.publisher,
                    "canonical_url": chunk.canonical_url,
                    "claim_text": chunk.claim_text,
                    "category": chunk.category,
                    "chunk_index": chunk.chunk_index,
                },
            )
            for chunk, embedding in zip(chunks, embeddings, strict=True)
        ]
        self._vector_store.upsert(documents)
        return len(documents)

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> tuple[RetrievedEvidence, ...]:
        if not query.strip():
            raise ValueError("Retrieval query cannot be empty.")
        if limit < 1:
            raise ValueError("Retrieval limit must be at least one.")

        current_chunks = {chunk.id: chunk for chunk in self._current_eligible_chunks()}
        if not current_chunks:
            return ()

        query_embedding = self._embed([query])[0]
        results = self._vector_store.query(
            query_embedding,
            limit=limit,
            where={"claim_status": "verified"},
        )
        evidence: list[RetrievedEvidence] = []
        for result in results:
            item = self._to_retrieved_evidence(result, current_chunks)
            if item is not None:
                evidence.append(item)
        return tuple(evidence)

    def validate_answer(
        self,
        answer: GroundedAnswer,
        *,
        context: Sequence[RetrievedEvidence],
    ) -> GroundingValidation:
        return validate_grounded_answer(answer, context=context)

    def _embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        embeddings = self._embedding_provider.embed(texts)
        if len(embeddings) != len(texts):
            raise ValueError(
                "Embedding provider must return one embedding for each input text."
            )
        normalized: list[tuple[float, ...]] = []
        expected_dimensions: int | None = None
        for embedding in embeddings:
            vector = tuple(float(value) for value in embedding)
            if not vector or not all(math.isfinite(value) for value in vector):
                raise ValueError("Embedding provider returned an empty or non-finite vector.")
            if expected_dimensions is None:
                expected_dimensions = len(vector)
            elif len(vector) != expected_dimensions:
                raise ValueError("Embedding provider returned inconsistent vector dimensions.")
            normalized.append(vector)
        return normalized

    def _current_eligible_chunks(self) -> list[EvidenceChunk]:
        rows = self._session.execute(
            select(ClaimEvidence, Claim, Source)
            .join(Claim, Claim.id == ClaimEvidence.claim_id)
            .join(Source, Source.id == ClaimEvidence.source_id)
            .where(
                Claim.verification_status == "verified",
                ClaimEvidence.support_type.in_(("supports", "context")),
            )
            .order_by(Claim.id, Source.id)
        )
        chunks: list[EvidenceChunk] = []
        for evidence, claim, source in rows:
            for index, text in enumerate(
                chunk_text(
                    evidence.excerpt,
                    chunk_size=self._chunk_size,
                    overlap=self._chunk_overlap,
                )
            ):
                chunks.append(
                    EvidenceChunk(
                        id=_chunk_id(claim.id, source.id, index, text),
                        claim_id=claim.id,
                        source_id=source.id,
                        text=text,
                        claim_text=claim.claim_text,
                        category=claim.category,
                        support_type=evidence.support_type,
                        source_title=source.title,
                        publisher=source.publisher,
                        canonical_url=source.canonical_url,
                        chunk_index=index,
                    )
                )
        return chunks

    @staticmethod
    def _to_retrieved_evidence(
        result: VectorSearchResult,
        current_chunks: dict[str, EvidenceChunk],
    ) -> RetrievedEvidence | None:
        metadata = result.metadata
        if not result.text or metadata is None:
            return None
        chunk = current_chunks.get(result.id)
        if chunk is None or result.text != chunk.text:
            return None
        claim_id_value = metadata.get("claim_id")
        source_id_value = metadata.get("source_id")
        if not isinstance(claim_id_value, str) or not isinstance(source_id_value, str):
            return None
        if claim_id_value != str(chunk.claim_id) or source_id_value != str(chunk.source_id):
            return None
        if metadata.get("claim_status") != "verified":
            return None
        if metadata.get("support_type") != chunk.support_type:
            return None
        distance = result.distance
        if distance is not None and not math.isfinite(distance):
            distance = None
        return RetrievedEvidence(
            chunk_id=result.id,
            claim_id=chunk.claim_id,
            source_id=chunk.source_id,
            text=result.text,
            claim_text=chunk.claim_text,
            category=chunk.category,
            support_type=chunk.support_type,
            source_title=chunk.source_title,
            publisher=chunk.publisher,
            canonical_url=chunk.canonical_url,
            distance=distance,
        )


def build_grounded_answer(
    statements: Sequence[GroundedStatement],
) -> GroundedAnswer:
    return GroundedAnswer(tuple(statements))
