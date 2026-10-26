from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from universal_video_maker.db.models import (
    Base,
    Claim,
    Episode,
    ResearchRecord,
    Source,
    Topic,
)

ModelT = TypeVar("ModelT", bound=Base)


class Repository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        self.session.flush()
        return entity

    def get(self, entity_id: UUID) -> ModelT | None:
        return self.session.get(self.model, entity_id)

    def list(self, *, limit: int = 50, offset: int = 0) -> list[ModelT]:
        statement = select(self.model).offset(offset).limit(limit)
        return list(self.session.scalars(statement))


class TopicRepository(Repository[Topic]):
    model = Topic

    def list_by_status(self, status: str, *, limit: int = 50, offset: int = 0) -> list[Topic]:
        statement = (
            select(Topic)
            .where(Topic.status == status)
            .order_by(Topic.created_at.desc(), Topic.id)
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.scalars(statement))


class SourceRepository(Repository[Source]):
    model = Source


class ResearchRepository(Repository[ResearchRecord]):
    model = ResearchRecord


class ClaimRepository(Repository[Claim]):
    model = Claim


class EpisodeRepository(Repository[Episode]):
    model = Episode

