from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    Base,
    ResearchRecord,
    Source,
    Topic,
)
from universal_video_maker.db.repositories import SourceRepository, TopicRepository
from universal_video_maker.db.session import create_database_engine, create_session_factory


@pytest.fixture
def database_engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_metadata_defines_expected_core_tables() -> None:
    assert {
        "topics",
        "sources",
        "research_records",
        "research_sources",
        "claims",
        "claim_evidence",
        "episodes",
        "episode_revisions",
        "scenes",
        "assets",
        "render_jobs",
        "approvals",
        "workflow_runs",
        "workflow_steps",
        "audit_events",
        "publishing_packages",
        "users",
    } == set(Base.metadata.tables)


def test_topic_repository_persists_and_filters_with_pagination(database_engine) -> None:
    session_factory = create_session_factory(database_engine)
    with session_factory() as session:
        repository = TopicRepository(session)
        proposed = repository.add(Topic(title="Chandrayaan", category="mission"))
        repository.add(Topic(title="Aditya-L1", category="mission", status="approved"))
        session.commit()

        assert repository.get(proposed.id) is not None
        listed = repository.list_by_status("proposed", limit=1, offset=0)
        assert [topic.title for topic in listed] == ["Chandrayaan"]


def test_source_repository_persists_source_metadata(database_engine) -> None:
    session_factory = create_session_factory(database_engine)
    source = Source(
        canonical_url="https://example.org/mission",
        title="Mission facts",
        publisher="Example Research",
        content_hash="sha256:abc",
        source_type="primary",
    )
    with session_factory() as session:
        repository = SourceRepository(session)
        repository.add(source)
        session.commit()

    with Session(database_engine) as session:
        stored = SourceRepository(session).get(source.id)
        assert stored is not None
        assert stored.publisher == "Example Research"


def test_research_record_rejects_missing_topic(database_engine) -> None:
    with Session(database_engine) as session:
        session.add(ResearchRecord(topic_id=uuid4()))
        with pytest.raises(IntegrityError):
            session.flush()


def test_database_engine_enables_pool_pre_ping() -> None:
    engine = create_database_engine("sqlite://")
    try:
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()

