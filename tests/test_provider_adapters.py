import json
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from universal_video_maker.config import Settings
from universal_video_maker.db.models import (
    Base,
    Claim,
    ResearchRecord,
    Source,
    Topic,
)
from universal_video_maker.providers.ollama import (
    OllamaClient,
    OllamaClientError,
    OllamaEmbeddingProvider,
)
from universal_video_maker.providers.tavily import (
    LocalSourceSnapshotStore,
    TavilyClientError,
    TavilySearchClient,
)
from universal_video_maker.research import CapturedSource
from universal_video_maker.research_generation import (
    OllamaResearchAssistant,
    TavilyOllamaResearchWorkflow,
)


class FakeTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, str], bytes | None, int]] = []

    def request(
        self,
        method,
        url,
        *,
        headers,
        body,
        timeout,
    ) -> bytes:
        self.calls.append((method, url, dict(headers), body, timeout))
        return json.dumps(self.responses.pop(0)).encode("utf-8")


class FakeChatClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def chat_json(self, *, system_prompt, user_prompt, response_schema):
        self.calls.append((system_prompt, user_prompt, response_schema))
        return self.response


@pytest.fixture
def database_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_tavily_search_uses_short_snippets_and_captures_durable_snapshot(tmp_path) -> None:
    transport = FakeTransport(
        [
            {
                "results": [
                    {
                        "title": "Cacao fermentation study",
                        "url": "https://example.org/cacao#section",
                        "content": "Fermentation changes the flavor of cacao.",
                        "score": 0.91,
                        "published_date": "2024-01-12",
                    }
                ]
            }
        ]
    )
    client = TavilySearchClient(
        api_key="secret-test-key",
        transport=transport,
        timeout_seconds=7,
    )

    results = client.search("cacao fermentation")

    assert len(results) == 1
    assert results[0].url == "https://example.org/cacao"
    assert results[0].published_at.isoformat() == "2024-01-12"
    method, url, headers, body, timeout = transport.calls[0]
    assert method == "POST"
    assert url == "https://api.tavily.com/search"
    assert headers == {"Content-Type": "application/json"}
    assert timeout == 7
    request = json.loads(body or b"{}")
    assert request["api_key"] == "secret-test-key"
    assert request["include_raw_content"] is False
    assert request["include_answer"] is False
    assert request["chunks_per_source"] == 1

    source = LocalSourceSnapshotStore(tmp_path).capture(results[0])

    assert source.source_type == "tavily_search_excerpt"
    assert source.publisher == "example.org"
    assert source.text == "Fermentation changes the flavor of cacao."
    assert source.storage_uri.startswith("file:")
    assert (tmp_path / f"{source.content_hash}.txt").read_bytes() == source.snapshot_bytes


def test_tavily_rejects_invalid_search_response_and_unsafe_base_url() -> None:
    client = TavilySearchClient(
        api_key="key",
        transport=FakeTransport([{"results": [{"title": "Missing fields"}]}]),
    )
    with pytest.raises(TavilyClientError, match="incomplete"):
        client.search("a topic")
    with pytest.raises(ValueError, match="HTTPS"):
        TavilySearchClient(api_key="key", api_base_url="http://api.tavily.com")


def test_ollama_chat_and_embedding_clients_validate_structured_responses() -> None:
    transport = FakeTransport(
        [
            {"message": {"content": json.dumps({"answer": "grounded"})}},
            {"embeddings": [[0.1, 0.2], [0.3, 0.4]]},
        ]
    )
    chat = OllamaClient(transport=transport, timeout_seconds=9)
    result = chat.chat_json(
        system_prompt="Return JSON.",
        user_prompt="Generate a structured answer.",
        response_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
    )
    embedder = OllamaEmbeddingProvider(transport=transport)
    embeddings = embedder.embed(["first", "second"])

    assert result == {"answer": "grounded"}
    assert embeddings == ((0.1, 0.2), (0.3, 0.4))
    assert transport.calls[0][1] == "http://localhost:11434/api/chat"
    assert transport.calls[0][4] == 9
    chat_body = json.loads(transport.calls[0][3] or b"{}")
    assert chat_body["stream"] is False
    assert chat_body["format"]["type"] == "object"
    assert transport.calls[1][1] == "http://localhost:11434/api/embed"
    assert json.loads(transport.calls[1][3] or b"{}")["truncate"] is False


def test_ollama_rejects_bad_json_and_invalid_remote_http_configuration() -> None:
    client = OllamaClient(transport=FakeTransport([{"message": {"content": "not json"}}]))
    with pytest.raises(OllamaClientError, match="invalid structured JSON"):
        client.chat_json(
            system_prompt="system",
            user_prompt="user",
            response_schema={"type": "object"},
        )
    with pytest.raises(ValueError, match="loopback"):
        OllamaClient(base_url="http://ollama.example.com")
    with pytest.raises(ValidationError, match="loopback"):
        Settings(_env_file=None, ollama_base_url="http://ollama.example.com")


def test_tavily_ollama_workflow_persists_only_unverified_candidate_claims(
    database_engine,
    tmp_path,
) -> None:
    excerpt = "Fermentation develops flavor compounds in cacao beans."
    transport = FakeTransport(
        [
            {
                "results": [
                    {
                        "title": "Cacao research",
                        "url": "https://example.org/cacao",
                        "content": excerpt,
                    }
                ]
            }
        ]
    )
    assistant_client = FakeChatClient(
        {
            "claims": [
                {
                    "claim_text": "Fermentation develops cacao flavor.",
                    "category": "food science",
                    "evidence": [
                        {
                            "source_url": "https://example.org/cacao",
                            "excerpt": excerpt,
                            "support_type": "supports",
                        }
                    ],
                }
            ],
            "open_questions": [],
        }
    )
    with Session(database_engine) as session:
        topic = Topic(title="Cacao fermentation", category="food science")
        session.add(topic)
        session.flush()
        workflow = TavilyOllamaResearchWorkflow(
            session,
            TavilySearchClient(api_key="test-key", transport=transport),
            LocalSourceSnapshotStore(tmp_path),
            OllamaResearchAssistant(assistant_client),
        )

        record = workflow.create_research_draft(topic.id)

        claim = session.scalar(select(Claim).where(Claim.research_record_id == record.id))
        source = session.scalar(select(Source))
        assert record.status == "draft"
        assert claim is not None
        assert claim.verification_status == "unverified"
        assert source is not None
        assert source.source_type == "tavily_search_excerpt"
        assert source.storage_uri.startswith("file:")
        assert session.scalar(select(ResearchRecord).where(ResearchRecord.id == record.id))


def test_research_assistant_rejects_quotes_not_present_in_captured_source() -> None:
    assistant = OllamaResearchAssistant(
        FakeChatClient(
            {
                "claims": [
                    {
                        "claim_text": "An unsupported claim.",
                        "category": "science",
                        "evidence": [
                            {
                                "source_url": "https://example.org/source",
                                "excerpt": "This text was never in the source.",
                                "support_type": "supports",
                            }
                        ],
                    }
                ],
                "open_questions": [],
            }
        )
    )
    topic = Topic(id=uuid4(), title="Example", category="science")
    captured = CapturedSource(
        canonical_url="https://example.org/source",
        title="Example source",
        publisher="example.org",
        source_type="tavily_search_excerpt",
        snapshot_bytes=b"Only captured source text.",
        storage_uri="file:///tmp/example.txt",
    )

    with pytest.raises(OllamaClientError, match="captured source evidence"):
        assistant.draft(topic=topic, sources=(captured,))
