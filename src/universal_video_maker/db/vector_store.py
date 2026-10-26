from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Protocol

MetadataValue = str | int | float | bool


@dataclass(frozen=True)
class VectorDocument:
    id: str
    text: str
    embedding: tuple[float, ...]
    metadata: dict[str, MetadataValue]


@dataclass(frozen=True)
class VectorSearchResult:
    id: str
    text: str | None
    metadata: dict[str, MetadataValue] | None
    distance: float | None


class VectorCollection(Protocol):
    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, MetadataValue]],
    ) -> object: ...

    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        where: dict[str, MetadataValue] | None = None,
        include: list[str] | None = None,
    ) -> dict[str, list[list[object]]]: ...


class ChromaVectorStore:
    def __init__(self, collection: VectorCollection) -> None:
        self._collection = collection

    @classmethod
    def persistent(
        cls,
        persist_directory: str | Path,
        collection_name: str,
    ) -> "ChromaVectorStore":
        try:
            import chromadb
        except ImportError as error:
            raise RuntimeError(
                "ChromaDB is not installed; install the 'vector' project extra."
            ) from error

        client = chromadb.PersistentClient(path=str(persist_directory))
        return cls(client.get_or_create_collection(name=collection_name))

    def upsert(self, documents: list[VectorDocument]) -> None:
        if not documents:
            return
        ids: set[str] = set()
        expected_dimensions: int | None = None
        for document in documents:
            if not document.id.strip():
                raise ValueError("Vector document IDs must not be empty.")
            if document.id in ids:
                raise ValueError(f"Duplicate vector document ID: {document.id}")
            if not document.text.strip():
                raise ValueError(f"Text for {document.id} must not be empty.")
            ids.add(document.id)
            dimensions = len(document.embedding)
            if dimensions == 0:
                raise ValueError(f"Embedding for {document.id} must not be empty.")
            if expected_dimensions is None:
                expected_dimensions = dimensions
            elif dimensions != expected_dimensions:
                raise ValueError("All embeddings in one upsert must have equal dimensions.")
            if not all(isfinite(value) for value in document.embedding):
                raise ValueError(f"Embedding for {document.id} contains a non-finite value.")
            if not document.metadata:
                raise ValueError(f"Metadata for {document.id} must not be empty.")
            if any(
                not key.strip()
                or (isinstance(value, float) and not isfinite(value))
                for key, value in document.metadata.items()
            ):
                raise ValueError(f"Metadata for {document.id} contains an invalid value.")

        self._collection.upsert(
            ids=[document.id for document in documents],
            embeddings=[list(document.embedding) for document in documents],
            documents=[document.text for document in documents],
            metadatas=[document.metadata for document in documents],
        )

    def query(
        self,
        embedding: tuple[float, ...],
        *,
        limit: int = 5,
        where: dict[str, MetadataValue] | None = None,
    ) -> list[VectorSearchResult]:
        if not embedding or not all(isfinite(value) for value in embedding):
            raise ValueError("Query embedding must be non-empty and finite.")
        if limit < 1:
            raise ValueError("Query limit must be at least one.")

        raw = self._collection.query(
            query_embeddings=[list(embedding)],
            n_results=limit,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids = self._first_result_list(raw, "ids")
        documents = self._first_result_list(raw, "documents")
        metadatas = self._first_result_list(raw, "metadatas")
        distances = self._first_result_list(raw, "distances")
        return [
            VectorSearchResult(
                id=str(document_id),
                text=self._optional_text(documents, index),
                metadata=self._optional_metadata(metadatas, index),
                distance=self._optional_distance(distances, index),
            )
            for index, document_id in enumerate(ids)
        ]

    @staticmethod
    def _first_result_list(
        result: dict[str, list[list[object]]], key: str
    ) -> list[object]:
        values = result.get(key)
        if not values:
            return []
        return values[0]

    @staticmethod
    def _optional_text(values: list[object], index: int) -> str | None:
        value = values[index] if index < len(values) else None
        return value if isinstance(value, str) else None

    @staticmethod
    def _optional_metadata(values: list[object], index: int) -> dict[str, MetadataValue] | None:
        value = values[index] if index < len(values) else None
        if not isinstance(value, dict):
            return None
        return {
            key: item
            for key, item in value.items()
            if isinstance(key, str) and isinstance(item, (str, int, float, bool))
        }

    @staticmethod
    def _optional_distance(values: list[object], index: int) -> float | None:
        value = values[index] if index < len(values) else None
        return float(value) if isinstance(value, (int, float)) else None
