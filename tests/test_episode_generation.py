import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from universal_video_maker.db.models import Base, Topic
from universal_video_maker.episode_generation import (
    NoGroundedEvidenceError,
    OllamaEpisodePlanGenerator,
)
from universal_video_maker.providers.ollama import OllamaClientError
from universal_video_maker.rag import RetrievedEvidence


class FakeRAG:
    def __init__(self, evidence: tuple[RetrievedEvidence, ...]) -> None:
        self.evidence = evidence
        self.calls: list[tuple[str, int]] = []

    def retrieve(self, query: str, *, limit: int):
        self.calls.append((query, limit))
        return self.evidence


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


def _evidence() -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id="evidence-1",
        claim_id=uuid4(),
        source_id=uuid4(),
        text="Fermentation develops flavor compounds in cacao beans.",
        claim_text="Fermentation develops cacao flavor.",
        category="food science",
        support_type="supports",
        source_title="Cacao research",
        publisher="Example Research",
        canonical_url="https://example.org/cacao",
        distance=0.1,
    )


def _valid_plan(quote: str) -> dict[str, object]:
    citation = {"chunk_id": "evidence-1", "quote": quote}
    narration = {
        "text": "Fermentation is an important step in cacao processing.",
        "citations": [citation],
    }
    return {
        "title": "From cacao bean to chocolate",
        "logline": "Follow cacao through an essential processing stage.",
        "learning_objectives": ["Explain why cacao is fermented."],
        "story_arc": ["Harvest", "Fermentation", "Chocolate"],
        "beats": [
            {
                "title": "The turning point",
                "purpose": "Introduce fermentation.",
                "start_seconds": 0,
                "duration_seconds": 180,
                "narration": narration,
            }
        ],
        "shorts": [
            {
                "title": f"Short {index}",
                "hook": "What changes inside cacao?",
                "duration_seconds": 30,
                "narration": narration,
                "call_to_action": "Learn more about cacao.",
            }
            for index in range(1, 4)
        ],
    }


def test_episode_generator_uses_retrieved_verified_evidence(database_engine) -> None:
    evidence = _evidence()
    rag = FakeRAG((evidence,))
    client = FakeChatClient(_valid_plan(evidence.text))
    with Session(database_engine) as session:
        topic = Topic(title="Cacao fermentation", category="food science")
        session.add(topic)
        session.flush()
        generator = OllamaEpisodePlanGenerator(session, rag, client)

        draft = generator.generate(topic.id, query="cacao fermentation process")

        assert draft.topic_id == topic.id
        assert draft.evidence_context == (evidence,)
        assert rag.calls == [("cacao fermentation process", 12)]
        prompt_payload = json.loads(client.calls[0][1])
        assert prompt_payload["verified_evidence_context"][0]["chunk_id"] == "evidence-1"


def test_episode_generator_blocks_missing_evidence_before_model_call(database_engine) -> None:
    client = FakeChatClient({})
    with Session(database_engine) as session:
        topic = Topic(title="Unknown topic", category="general")
        session.add(topic)
        session.flush()
        generator = OllamaEpisodePlanGenerator(session, FakeRAG(()), client)

        with pytest.raises(NoGroundedEvidenceError, match="verified evidence"):
            generator.generate(topic.id)

    assert client.calls == []


def test_episode_generator_rejects_model_quotes_outside_retrieved_evidence(
    database_engine,
) -> None:
    evidence = _evidence()
    client = FakeChatClient(_valid_plan("an invented quote"))
    with Session(database_engine) as session:
        topic = Topic(title="Cacao fermentation", category="food science")
        session.add(topic)
        session.flush()
        generator = OllamaEpisodePlanGenerator(session, FakeRAG((evidence,)), client)

        with pytest.raises(OllamaClientError, match="timing and evidence"):
            generator.generate(topic.id)
