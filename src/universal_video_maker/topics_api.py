from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session, sessionmaker

from universal_video_maker.db.models import AuditEvent, ResearchRecord, ResearchSource, Topic
from universal_video_maker.db.repositories import TopicRepository
from universal_video_maker.errors import ConflictError, NotFoundError
from universal_video_maker.identity import CurrentPrincipal
from universal_video_maker.security import Permission, require_permission
from universal_video_maker.user_mapping import ensure_internal_user

router = APIRouter(prefix="/topics", tags=["topics"])


def get_database_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] | None = request.app.state.session_factory
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured.",
        )
    with factory() as session:
        yield session


DatabaseSession = Annotated[Session, Depends(get_database_session)]


class TopicCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    category: str = Field(default="General", min_length=1, max_length=80)

    model_config = ConfigDict(str_strip_whitespace=True)


class TopicRead(BaseModel):
    id: UUID
    title: str
    category: str
    status: str
    ranking_score: float | None
    source_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=list[TopicRead])
def list_topics(
    session: DatabaseSession,
    principal: CurrentPrincipal,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TopicRead]:
    require_permission(principal, Permission.TOPICS_READ)
    statement = (
        select(Topic, func.count(distinct(ResearchSource.source_id)).label("source_count"))
        .outerjoin(ResearchRecord, ResearchRecord.topic_id == Topic.id)
        .outerjoin(ResearchSource, ResearchSource.research_record_id == ResearchRecord.id)
        .group_by(Topic.id)
        .order_by(Topic.created_at.desc(), Topic.id)
        .offset(offset)
        .limit(limit)
    )
    return [
        TopicRead.model_validate(topic).model_copy(update={"source_count": source_count})
        for topic, source_count in session.execute(statement)
    ]


class TopicDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = Field(min_length=1, max_length=2000)

    model_config = ConfigDict(str_strip_whitespace=True)


_DECISION_STATUS = {"approve": "ready_to_plan", "reject": "rejected"}


@router.post("/{topic_id}/decision", response_model=TopicRead)
def decide_topic(
    topic_id: UUID,
    request: TopicDecisionRequest,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> TopicRead:
    require_permission(principal, Permission.TOPICS_WRITE)
    topic = session.get(Topic, topic_id, with_for_update=True)
    if topic is None:
        raise NotFoundError(f"Topic {topic_id} does not exist.")
    if topic.status != "proposed":
        raise ConflictError(
            f"Only proposed topics can be approved or rejected; this topic is {topic.status}."
        )
    actor_id = ensure_internal_user(session, principal)
    previous_status = topic.status
    topic.status = _DECISION_STATUS[request.decision]
    session.add(
        AuditEvent(
            actor_id=actor_id,
            object_type="topic",
            object_id=topic.id,
            action=f"topic.{request.decision}d",
            details={
                "previous_status": previous_status,
                "status": topic.status,
                "note": request.note,
            },
        )
    )
    session.commit()
    session.refresh(topic)
    return TopicRead.model_validate(topic)


@router.post("", response_model=TopicRead, status_code=status.HTTP_201_CREATED)
def create_topic(
    request: TopicCreate,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> Topic:
    require_permission(principal, Permission.TOPICS_WRITE)
    topic = TopicRepository(session).add(
        Topic(title=request.title, category=request.category, status="proposed")
    )
    session.commit()
    session.refresh(topic)
    return topic
