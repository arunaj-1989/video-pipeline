"""Curated topic-catalog ingestion, ranking, and deduplicated topic import."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import AuditEvent, Topic

RANKING_MODEL = "domain_neutral_equal_weight_v2"
_RANKING_FIELDS = (
    "subject_relevance",
    "story_visual_potential",
    "search_interest",
    "audience_suitability",
    "novelty",
    "timeliness",
)


@dataclass(frozen=True)
class SourceReference:
    url: str
    title: str
    publisher: str

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Source references must use an absolute HTTPS URL.")
        if not self.title.strip() or not self.publisher.strip():
            raise ValueError("Source title and publisher are required.")


@dataclass(frozen=True)
class RankingInputs:
    subject_relevance: int
    story_visual_potential: int
    search_interest: int
    audience_suitability: int
    novelty: int
    timeliness: int

    def __post_init__(self) -> None:
        for field_name in _RANKING_FIELDS:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                raise ValueError(f"{field_name} must be an integer between 0 and 100.")

    def score(self) -> Decimal:
        total = sum(getattr(self, field_name) for field_name in _RANKING_FIELDS)
        return (Decimal(total) / Decimal(len(_RANKING_FIELDS))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )


@dataclass(frozen=True)
class DiscoveryCandidate:
    title: str
    category: str
    summary: str
    ranking_inputs: RankingInputs
    source_references: tuple[SourceReference, ...]

    def __post_init__(self) -> None:
        if not self.title.strip() or len(self.title.strip()) > 300:
            raise ValueError("Topic title must contain between 1 and 300 characters.")
        if not self.category.strip() or len(self.category.strip()) > 80:
            raise ValueError("Topic category must contain between 1 and 80 characters.")
        if not self.summary.strip():
            raise ValueError("A concise topic summary is required.")
        if not self.source_references:
            raise ValueError("At least one source reference is required.")


class TopicSource(Protocol):
    """A source of editorially curated topic candidates."""

    name: str

    def fetch_candidates(self) -> Iterable[DiscoveryCandidate]: ...


@dataclass(frozen=True)
class CuratedCatalog:
    """An in-process curated catalog; it performs no network access."""

    name: str
    candidates: Sequence[DiscoveryCandidate]

    def fetch_candidates(self) -> Iterable[DiscoveryCandidate]:
        return self.candidates


@dataclass(frozen=True)
class DailyDiscoverySchedule:
    """Daily local-time eligibility policy for a scheduler or workflow runner."""

    run_at: time = time(6, 0)
    timezone_name: str = "Asia/Kolkata"

    def is_due(self, now: datetime, last_started_at: datetime | None = None) -> bool:
        if now.tzinfo is None or (last_started_at is not None and last_started_at.tzinfo is None):
            raise ValueError("Schedule timestamps must be timezone-aware.")
        timezone = ZoneInfo(self.timezone_name)
        local_now = now.astimezone(timezone)
        if local_now.timetz().replace(tzinfo=None) < self.run_at:
            return False
        if last_started_at is None:
            return True
        return last_started_at.astimezone(timezone).date() < local_now.date()


@dataclass(frozen=True)
class DuplicateCandidate:
    title: str
    duplicate_of: str
    reason: str


@dataclass(frozen=True)
class DiscoveryResult:
    imported: tuple[Topic, ...]
    duplicates: tuple[DuplicateCandidate, ...]


def normalize_title(title: str) -> str:
    """Normalize Unicode, case, punctuation, and spacing for exact title matching."""
    normalized = unicodedata.normalize("NFKC", title).casefold()
    words = re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
    return " ".join(words)


def _discovery_key(normalized_title: str) -> str:
    return hashlib.sha256(normalized_title.encode("utf-8")).hexdigest()


class TopicDiscoveryService:
    """Rank catalog candidates and persist new proposals in the caller's transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def discover(self, sources: Sequence[TopicSource]) -> DiscoveryResult:
        existing_titles = list(self._session.scalars(select(Topic.title)))
        known_titles = {normalize_title(title): title for title in existing_titles}
        existing_keys = set(known_titles)

        ranked: list[tuple[DiscoveryCandidate, Decimal, str]] = []
        for source in sources:
            for candidate in source.fetch_candidates():
                ranked.append((candidate, candidate.ranking_inputs.score(), source.name))
        ranked.sort(key=lambda item: -item[1])

        imported: list[Topic] = []
        duplicates: list[DuplicateCandidate] = []
        for candidate, score, source_name in ranked:
            normalized_title = normalize_title(candidate.title)
            existing_title = known_titles.get(normalized_title)
            if existing_title is not None:
                duplicates.append(
                    DuplicateCandidate(
                        title=candidate.title,
                        duplicate_of=existing_title,
                        reason=(
                            "existing_topic"
                            if normalized_title in existing_keys
                            else "duplicate_candidate"
                        ),
                    )
                )
                continue

            discovery_key = _discovery_key(normalized_title)
            topic = Topic(
                title=candidate.title.strip(),
                category=candidate.category.strip(),
                status="proposed",
                ranking_score=score,
                discovery_key=discovery_key,
                discovery_metadata={
                    "summary": candidate.summary.strip(),
                    "ranking_model": RANKING_MODEL,
                    "ranking_inputs": asdict(candidate.ranking_inputs),
                    "source_catalog": source_name,
                    "source_references": [
                        {"url": source.url, "title": source.title, "publisher": source.publisher}
                        for source in candidate.source_references
                    ],
                },
            )
            self._session.add(topic)
            self._session.flush()
            self._session.add(
                AuditEvent(
                    object_type="topic",
                    object_id=topic.id,
                    action="topic.discovered",
                    details={
                        "title": topic.title,
                        "ranking_score": str(score),
                        "ranking_model": RANKING_MODEL,
                        "source_catalog": source_name,
                    },
                )
            )
            imported.append(topic)
            known_titles[normalized_title] = topic.title

        self._session.flush()
        return DiscoveryResult(tuple(imported), tuple(duplicates))
