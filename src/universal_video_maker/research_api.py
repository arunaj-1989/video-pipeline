from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from universal_video_maker.config import Settings
from universal_video_maker.db.models import Claim, ResearchRecord, ResearchSource, Topic
from universal_video_maker.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from universal_video_maker.identity import CurrentPrincipal
from universal_video_maker.providers.ollama import OllamaClientError
from universal_video_maker.providers.tavily import TavilyClientError
from universal_video_maker.security import Permission, require_permission
from universal_video_maker.topics_api import DatabaseSession

router = APIRouter(prefix="/research", tags=["research"])


class ResearchWorkflow(Protocol):
    def create_research_draft(self, topic_id: UUID) -> ResearchRecord: ...


ResearchWorkflowFactory = Callable[[Session], ResearchWorkflow]


def build_research_workflow_factory(settings: Settings) -> ResearchWorkflowFactory:
    """Build the Tavily + Ollama workflow lazily so the API starts without them."""

    def factory(session: Session) -> ResearchWorkflow:
        from universal_video_maker.providers.ollama import OllamaClient
        from universal_video_maker.providers.tavily import (
            LocalSourceSnapshotStore,
            TavilySearchClient,
        )
        from universal_video_maker.research_generation import (
            OllamaResearchAssistant,
            TavilyOllamaResearchWorkflow,
        )

        try:
            tavily = TavilySearchClient.from_settings(settings)
        except ValueError as error:
            raise ServiceUnavailableError(
                "Web search is not configured. Set UVM_TAVILY_API_KEY in .env and restart the API."
            ) from error
        return TavilyOllamaResearchWorkflow(
            session,
            tavily,
            LocalSourceSnapshotStore(settings.source_snapshot_directory),
            OllamaResearchAssistant(OllamaClient.from_settings(settings)),
            max_results=settings.tavily_max_results,
        )

    return factory


class ResearchRequest(BaseModel):
    topic_id: UUID


class ResearchRead(BaseModel):
    research_id: UUID
    topic_id: UUID
    revision: int
    source_count: int
    claim_count: int
    open_questions: list[str]


@router.post("", response_model=ResearchRead, status_code=status.HTTP_201_CREATED)
def run_research(
    body: ResearchRequest,
    request: Request,
    session: DatabaseSession,
    principal: CurrentPrincipal,
) -> ResearchRead:
    require_permission(principal, Permission.TOPICS_WRITE)
    topic = session.get(Topic, body.topic_id)
    if topic is None:
        raise NotFoundError(f"Topic {body.topic_id} does not exist.")
    if topic.status != "ready_to_plan":
        raise ConflictError("Only approved (ready to plan) topics can be researched.")

    factory: ResearchWorkflowFactory = request.app.state.research_workflow_factory
    try:
        record = factory(session).create_research_draft(topic.id)
        session.commit()
    except (TavilyClientError, OllamaClientError) as error:
        session.rollback()
        raise ServiceUnavailableError(
            "Research could not run. Check the web search key, that Ollama is running with "
            "the chat model installed, and try again."
        ) from error
    session.refresh(record)
    source_count = session.scalar(
        select(func.count())
        .select_from(ResearchSource)
        .where(ResearchSource.research_record_id == record.id)
    )
    claim_count = session.scalar(
        select(func.count()).select_from(Claim).where(Claim.research_record_id == record.id)
    )
    return ResearchRead(
        research_id=record.id,
        topic_id=record.topic_id,
        revision=record.revision,
        source_count=int(source_count or 0),
        claim_count=int(claim_count or 0),
        open_questions=list(record.open_questions or []),
    )
