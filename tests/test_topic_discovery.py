from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import AuditEvent, Base, Topic
from universal_video_maker.topic_discovery import (
    CuratedCatalog,
    DailyDiscoverySchedule,
    DiscoveryCandidate,
    RankingInputs,
    SourceReference,
    TopicDiscoveryService,
    normalize_title,
)


@pytest.fixture
def database_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def source_reference() -> SourceReference:
    return SourceReference(
        url="https://example.org/",
        title="Editorial source reference",
        publisher="Example Publisher",
    )


def ranking_inputs(**overrides: int) -> RankingInputs:
    values = {
        "subject_relevance": 80,
        "story_visual_potential": 70,
        "search_interest": 60,
        "audience_suitability": 90,
        "novelty": 50,
        "timeliness": 40,
    }
    values.update(overrides)
    return RankingInputs(**values)


def candidate(
    title: str,
    *,
    category: str = "general",
    ranking: RankingInputs | None = None,
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        title=title,
        category=category,
        summary="A concise, editor-reviewed topic summary.",
        ranking_inputs=ranking or ranking_inputs(),
        source_references=(source_reference(),),
    )


def test_ranking_score_uses_equal_weighted_inputs() -> None:
    assert ranking_inputs().score() == Decimal("65.00")
    assert ranking_inputs(subject_relevance=100).score() == Decimal("68.33")


@pytest.mark.parametrize(
    "values",
    [
        {"subject_relevance": -1},
        {"story_visual_potential": 101},
        {"search_interest": True},
    ],
)
def test_ranking_inputs_reject_values_outside_integer_scale(values) -> None:
    with pytest.raises(ValueError):
        ranking_inputs(**values)


def test_source_references_require_absolute_https() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        SourceReference(url="http://example.org", title="Source", publisher="Publisher")


def test_title_normalization_ignores_case_punctuation_and_spacing() -> None:
    assert normalize_title("  Aditya–L1: Sun Stories ") == normalize_title("aditya l1 sun stories")


def test_discovery_ranks_imports_audits_and_deduplicates(database_engine) -> None:
    catalog = CuratedCatalog(
        name="editorial-curation",
        candidates=(
            candidate(
                "Low-ranked cacao fermentation", ranking=ranking_inputs(subject_relevance=10)
            ),
            candidate("How sourdough rises"),
            candidate("HOW SOURDOUGH RISES!", ranking=ranking_inputs(subject_relevance=100)),
        ),
    )
    with Session(database_engine) as session:
        result = TopicDiscoveryService(session).discover((catalog,))
        session.commit()

        assert [topic.title for topic in result.imported] == [
            "HOW SOURDOUGH RISES!",
            "Low-ranked cacao fermentation",
        ]
        assert len(result.duplicates) == 1
        assert result.duplicates[0].reason == "duplicate_candidate"
        assert result.duplicates[0].duplicate_of == "HOW SOURDOUGH RISES!"

        imported_topic = result.imported[0]
        stored = session.get(Topic, imported_topic.id)
        assert stored is not None
        assert stored.status == "proposed"
        assert stored.ranking_score == Decimal("68.33")
        assert stored.discovery_metadata["ranking_model"] == "domain_neutral_equal_weight_v2"
        assert stored.discovery_metadata["source_references"][0]["publisher"] == "Example Publisher"
        audit = session.scalar(select(AuditEvent).where(AuditEvent.object_id == imported_topic.id))
        assert audit is not None
        assert audit.action == "topic.discovered"


def test_discovery_skips_existing_topics_and_repeated_runs(database_engine) -> None:
    catalog = CuratedCatalog("editorial-curation", (candidate("Compound interest"),))
    with Session(database_engine) as session:
        session.add(Topic(title="Compound interest", category="personal finance"))
        session.commit()

        first = TopicDiscoveryService(session).discover((catalog,))
        second = TopicDiscoveryService(session).discover((catalog,))

        assert not first.imported
        assert first.duplicates[0].reason == "existing_topic"
        assert not second.imported
        assert second.duplicates[0].reason == "existing_topic"


def test_discovery_accepts_topics_outside_a_fixed_subject_domain(database_engine) -> None:
    food_topic = candidate("Why does sourdough rise?", category="Food science")
    catalog = CuratedCatalog("editorial-curation", (food_topic,))

    with Session(database_engine) as session:
        result = TopicDiscoveryService(session).discover((catalog,))

    assert len(result.imported) == 1
    assert result.imported[0].title == "Why does sourdough rise?"
    assert result.imported[0].category == "Food science"


def test_daily_schedule_uses_ist_and_runs_once_per_local_date() -> None:
    schedule = DailyDiscoverySchedule()
    before_run = datetime(2026, 10, 26, 0, 20, tzinfo=UTC)  # 05:50 IST
    due = datetime(2026, 10, 26, 0, 30, tzinfo=UTC)  # 06:00 IST
    after_due = datetime(2026, 10, 26, 1, 0, tzinfo=UTC)

    assert not schedule.is_due(before_run)
    assert schedule.is_due(due)
    assert not schedule.is_due(
        after_due,
        last_started_at=datetime(2026, 10, 26, 0, 31, tzinfo=UTC),
    )


def test_daily_schedule_rejects_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        DailyDiscoverySchedule().is_due(datetime(2026, 10, 26, 6, 0))
