from collections.abc import Sequence
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    Base,
    Claim,
    ClaimEvidence,
    ResearchRecord,
    Source,
    Topic,
)
from universal_video_maker.db.vector_store import ChromaVectorStore
from universal_video_maker.rag import (
    EvidenceCitation,
    GroundedRAGService,
    GroundedStatement,
    RetrievedEvidence,
    build_grounded_answer,
    chunk_text,
)


class FakeCollection:
    def __init__(self) -> None:
        self.upserted: dict[str, Any] | None = None
        self.query_arguments: dict[str, Any] | None = None
        self.results: dict[str, list[list[object]]] = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

    def upsert(self, **kwargs: Any) -> None:
        self.upserted = kwargs

    def query(self, **kwargs: Any) -> dict[str, list[list[object]]]:
        self.query_arguments = kwargs
        return self.results


class FakeEmbeddings:
    def __init__(self) -> None:
        self.inputs: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self.inputs.append(list(texts))
        return [(float(len(text)), 1.0) for text in texts]


@pytest.fixture
def database_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def add_evidence(
    session: Session,
    *,
    status: str,
    support_type: str = "supports",
    excerpt: str = "Aditya-L1 launched on 2 September 2023. It studies the Sun.",
) -> tuple[Claim, Source, ClaimEvidence]:
    topic = Topic(title=f"Topic {uuid4()}", category="mission")
    session.add(topic)
    session.flush()
    record = ResearchRecord(topic_id=topic.id, revision=1)
    session.add(record)
    session.flush()
    claim = Claim(
        research_record_id=record.id,
        claim_text="Aditya-L1 launched on 2 September 2023.",
        category="historical_record",
        verification_status=status,
    )
    source = Source(
        canonical_url=f"https://example.org/{uuid4()}",
        title="Mission source",
        publisher="Research publisher",
        content_hash=uuid4().hex,
        source_type="official",
    )
    session.add_all([claim, source])
    session.flush()
    evidence = ClaimEvidence(
        claim_id=claim.id,
        source_id=source.id,
        excerpt=excerpt,
        support_type=support_type,
    )
    session.add(evidence)
    session.flush()
    return claim, source, evidence


def service_for(session: Session, collection: FakeCollection, embeddings: FakeEmbeddings, **kwargs):
    return GroundedRAGService(
        session,
        ChromaVectorStore(collection),
        embeddings,
        **kwargs,
    )


def test_chunk_text_normalizes_and_overlaps() -> None:
    chunks = chunk_text("alpha beta gamma delta epsilon", chunk_size=16, overlap=5)

    assert chunks
    assert all(chunk for chunk in chunks)
    assert all("  " not in chunk for chunk in chunks)
    assert any(
        set(left.split()) & set(right.split())
        for left, right in zip(chunks, chunks[1:], strict=False)
    )


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    [(0, 0), (10, -1), (10, 10)],
)
def test_chunk_text_rejects_invalid_configuration(chunk_size, overlap) -> None:
    with pytest.raises(ValueError):
        chunk_text("some text", chunk_size=chunk_size, overlap=overlap)


def test_index_only_verified_supporting_and_context_evidence(database_engine) -> None:
    collection = FakeCollection()
    embeddings = FakeEmbeddings()
    with Session(database_engine) as session:
        verified, _, _ = add_evidence(session, status="verified")
        add_evidence(session, status="unverified")
        add_evidence(session, status="verified", support_type="contradicts")
        context, _, _ = add_evidence(
            session,
            status="verified",
            support_type="context",
        )
        session.commit()

        count = service_for(
            session,
            collection,
            embeddings,
            chunk_size=200,
            chunk_overlap=20,
        ).index_approved_evidence()

        assert count == 2
        assert collection.upserted is not None
        indexed_ids = collection.upserted["ids"]
        assert len(indexed_ids) == 2
        metadata = collection.upserted["metadatas"]
        assert {item["claim_id"] for item in metadata} == {
            str(verified.id),
            str(context.id),
        }
        assert all(item["claim_status"] == "verified" for item in metadata)
        assert all(item["support_type"] in {"supports", "context"} for item in metadata)
        assert embeddings.inputs and len(embeddings.inputs[0]) == count


def test_index_returns_zero_without_calling_embeddings_when_no_verified_evidence(
    database_engine,
) -> None:
    collection = FakeCollection()
    embeddings = FakeEmbeddings()
    with Session(database_engine) as session:
        add_evidence(session, status="unverified")
        session.commit()

        count = service_for(session, collection, embeddings).index_approved_evidence()

        assert count == 0
        assert not embeddings.inputs
        assert collection.upserted is None


def test_retrieve_filters_stale_unverified_vector_results(database_engine) -> None:
    collection = FakeCollection()
    embeddings = FakeEmbeddings()
    with Session(database_engine) as session:
        verified, verified_source, _ = add_evidence(session, status="verified")
        unverified, unverified_source, _ = add_evidence(session, status="unverified")
        session.commit()
        rag = service_for(session, collection, embeddings)
        assert rag.index_approved_evidence() == 1
        assert collection.upserted is not None
        verified_chunk_id = collection.upserted["ids"][0]
        verified_metadata = collection.upserted["metadatas"][0]
        collection.results = {
            "ids": [
                [
                    verified_chunk_id,
                    "stale-same-source-chunk",
                    "stale-unverified-chunk",
                ]
            ],
            "documents": [
                [
                    collection.upserted["documents"][0],
                    "Old material from the same verified source.",
                    "Stale unverified material.",
                ]
            ],
            "metadatas": [
                [
                    verified_metadata,
                    verified_metadata,
                    {
                        "claim_id": str(unverified.id),
                        "source_id": str(unverified_source.id),
                        "claim_status": "verified",
                        "support_type": "supports",
                        "claim_text": unverified.claim_text,
                        "category": unverified.category,
                        "source_title": unverified_source.title,
                        "publisher": unverified_source.publisher,
                        "canonical_url": unverified_source.canonical_url,
                    },
                ]
            ],
            "distances": [[0.1, 0.15, 0.2]],
        }

        results = rag.retrieve("When did Aditya-L1 launch?")

        assert len(results) == 1
        assert results[0].claim_id == verified.id
        assert results[0].source_id == verified_source.id
        assert results[0].distance == 0.1
        assert collection.query_arguments is not None
        assert collection.query_arguments["where"] == {"claim_status": "verified"}


def test_retrieval_rejects_stale_chunk_after_evidence_text_changes(database_engine) -> None:
    collection = FakeCollection()
    embeddings = FakeEmbeddings()
    with Session(database_engine) as session:
        _, _, evidence = add_evidence(session, status="verified")
        session.commit()
        rag = service_for(session, collection, embeddings)
        assert rag.index_approved_evidence() == 1
        assert collection.upserted is not None
        stale_id = collection.upserted["ids"][0]
        stale_metadata = collection.upserted["metadatas"][0]

        evidence.excerpt = "Updated source statement, after an approved revision."
        session.commit()
        collection.results = {
            "ids": [[stale_id]],
            "documents": [[
                "Aditya-L1 launched on 2 September 2023. It studies the Sun."
            ]],
            "metadatas": [[stale_metadata]],
            "distances": [[0.01]],
        }

        assert rag.retrieve("Aditya-L1") == ()


def test_retrieval_returns_empty_without_verified_database_evidence(database_engine) -> None:
    collection = FakeCollection()
    embeddings = FakeEmbeddings()
    with Session(database_engine) as session:
        add_evidence(session, status="unverified")
        session.commit()

        results = service_for(session, collection, embeddings).retrieve("question")

        assert results == ()
        assert not embeddings.inputs
        assert collection.query_arguments is None


def test_grounding_requires_retrieved_citations_and_exact_quotes(database_engine) -> None:
    collection = FakeCollection()
    embeddings = FakeEmbeddings()
    with Session(database_engine) as session:
        claim, source, evidence = add_evidence(session, status="verified")
        session.commit()
        evidence_item = RetrievedEvidence(
            chunk_id="chunk-1",
            claim_id=claim.id,
            source_id=source.id,
            text=evidence.excerpt,
            claim_text=claim.claim_text,
            category=claim.category,
            support_type=evidence.support_type,
            source_title=source.title,
            publisher=source.publisher,
            canonical_url=source.canonical_url,
            distance=0.1,
        )
        service = service_for(session, collection, embeddings)

        valid_answer = build_grounded_answer(
            (
                GroundedStatement(
                    text="Aditya-L1 launched on the stated date.",
                    citations=(
                        EvidenceCitation(
                            chunk_id="chunk-1",
                            quote="Aditya-L1 launched on 2 September 2023.",
                        ),
                    ),
                ),
            )
        )
        assert service.validate_answer(valid_answer, context=(evidence_item,)).valid

        fabricated_quote = build_grounded_answer(
            (
                GroundedStatement(
                    text="Aditya-L1 launched in 2024.",
                    citations=(
                        EvidenceCitation(
                            chunk_id="chunk-1",
                            quote="Aditya-L1 launched in 2024.",
                        ),
                    ),
                ),
            )
        )
        validation = service.validate_answer(fabricated_quote, context=(evidence_item,))
        assert not validation.valid
        assert "quote not present" in validation.issues[0]

        outside_context = build_grounded_answer(
            (
                GroundedStatement(
                    text="An unsupported statement.",
                    citations=(EvidenceCitation("not-retrieved", "some quote"),),
                ),
            )
        )
        assert not service.validate_answer(
            outside_context, context=(evidence_item,)
        ).valid


def test_retrieval_rejects_empty_query_and_invalid_limit(database_engine) -> None:
    collection = FakeCollection()
    with Session(database_engine) as session:
        service = GroundedRAGService(
            session,
            ChromaVectorStore(collection),
            FakeEmbeddings(),
        )
        with pytest.raises(ValueError, match="query"):
            service.retrieve(" ")
        with pytest.raises(ValueError, match="limit"):
            service.retrieve("query", limit=0)
