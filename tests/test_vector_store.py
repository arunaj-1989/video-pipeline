from typing import Any

import pytest

from universal_video_maker.db.vector_store import (
    ChromaVectorStore,
    VectorDocument,
)


class FakeCollection:
    def __init__(self) -> None:
        self.upserted: dict[str, Any] | None = None
        self.query_arguments: dict[str, Any] | None = None

    def upsert(self, **kwargs: Any) -> None:
        self.upserted = kwargs

    def query(self, **kwargs: Any) -> dict[str, list[list[object]]]:
        self.query_arguments = kwargs
        return {
            "ids": [["claim-1"]],
            "documents": [["Chandrayaan entered lunar orbit."]],
            "metadatas": [[{"claim_id": "claim-1", "approved": True}]],
            "distances": [[0.125]],
        }


def test_upsert_sends_documents_and_embeddings_to_collection() -> None:
    collection = FakeCollection()
    store = ChromaVectorStore(collection)
    document = VectorDocument(
        id="claim-1",
        text="Chandrayaan entered lunar orbit.",
        embedding=(0.1, 0.2),
        metadata={"claim_id": "claim-1", "approved": True},
    )

    store.upsert([document])

    assert collection.upserted == {
        "ids": ["claim-1"],
        "embeddings": [[0.1, 0.2]],
        "documents": ["Chandrayaan entered lunar orbit."],
        "metadatas": [{"claim_id": "claim-1", "approved": True}],
    }


def test_query_returns_typed_results_and_passes_metadata_filter() -> None:
    collection = FakeCollection()
    store = ChromaVectorStore(collection)

    results = store.query((0.2, 0.3), limit=3, where={"approved": True})

    assert results[0].id == "claim-1"
    assert results[0].text == "Chandrayaan entered lunar orbit."
    assert results[0].metadata == {"claim_id": "claim-1", "approved": True}
    assert results[0].distance == 0.125
    assert collection.query_arguments["where"] == {"approved": True}
    assert collection.query_arguments["n_results"] == 3


def test_empty_upsert_is_a_no_op() -> None:
    collection = FakeCollection()

    ChromaVectorStore(collection).upsert([])

    assert collection.upserted is None


@pytest.mark.parametrize(
    ("documents", "message"),
    [
        ([VectorDocument("", "text", (0.1,), {"id": "x"})], "IDs"),
        ([VectorDocument("empty", "text", (), {"id": "x"})], "empty"),
        ([VectorDocument("empty-text", "  ", (0.1,), {"id": "x"})], "Text"),
        (
            [
                VectorDocument("a", "text", (0.1,), {"id": "a"}),
                VectorDocument("b", "text", (0.1, 0.2), {"id": "b"}),
            ],
            "equal dimensions",
        ),
        ([VectorDocument("nan", "text", (float("nan"),), {"id": "x"})], "non-finite"),
        ([VectorDocument("no-meta", "text", (0.1,), {})], "Metadata"),
        (
            [VectorDocument("invalid-meta", "text", (0.1,), {"score": float("nan")})],
            "invalid value",
        ),
    ],
)
def test_upsert_rejects_invalid_documents(documents, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ChromaVectorStore(FakeCollection()).upsert(documents)


def test_query_rejects_invalid_embedding_and_limit() -> None:
    store = ChromaVectorStore(FakeCollection())

    with pytest.raises(ValueError, match="embedding"):
        store.query(())
    with pytest.raises(ValueError, match="limit"):
        store.query((0.1,), limit=0)
