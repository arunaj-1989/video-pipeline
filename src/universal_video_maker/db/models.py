from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        CheckConstraint(
            "ranking_score IS NULL OR (ranking_score >= 0 AND ranking_score <= 100)",
            name="ck_topics_ranking_score_range",
        ),
        Index("ix_topics_status_created_at", "status", "created_at"),
        Index("ix_topics_discovery_key", "discovery_key", unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), default="proposed", server_default="proposed")
    ranking_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    discovery_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    discovery_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    canonical_url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(String(500))
    publisher: Mapped[str] = mapped_column(String(250))
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    content_hash: Mapped[str] = mapped_column(String(128))
    source_type: Mapped[str] = mapped_column(String(48))
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    lineage_source_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )


class ResearchRecord(Base):
    __tablename__ = "research_records"
    __table_args__ = (UniqueConstraint("topic_id", "revision", name="uq_research_topic_revision"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    topic_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("topics.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(32), default="draft", server_default="draft")
    findings: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    open_questions: Mapped[list[Any]] = mapped_column(
        JSON_DOCUMENT, default=list, server_default=text("'[]'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ResearchSource(Base):
    __tablename__ = "research_sources"

    research_record_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("research_records.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("sources.id", ondelete="RESTRICT"), primary_key=True
    )
    relevance_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (Index("ix_claims_verification_status", "verification_status"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    research_record_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("research_records.id", ondelete="CASCADE"), index=True
    )
    claim_text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(48))
    verification_status: Mapped[str] = mapped_column(
        String(32), default="unverified", server_default="unverified"
    )
    reviewer_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"

    claim_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("sources.id", ondelete="RESTRICT"), primary_key=True
    )
    excerpt: Mapped[str] = mapped_column(Text)
    support_type: Mapped[str] = mapped_column(String(32))
    source_lineage_key: Mapped[str | None] = mapped_column(String(128), nullable=True)


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (Index("ix_episodes_status_publish_date", "status", "target_publish_date"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    topic_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("topics.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(32), default="draft", server_default="draft")
    target_publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EpisodeRevision(Base):
    __tablename__ = "episode_revisions"
    __table_args__ = (
        UniqueConstraint("episode_id", "revision", name="uq_episode_revision_number"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    episode_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("episodes.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (
        UniqueConstraint("revision_id", "scene_number", name="uq_scene_revision_number"),
        CheckConstraint("scene_number > 0", name="ck_scene_number_positive"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    revision_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("episode_revisions.id", ondelete="CASCADE"), index=True
    )
    scene_number: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)


class RenderJob(Base):
    __tablename__ = "render_jobs"
    __table_args__ = (Index("ix_render_jobs_status_created_at", "status", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    episode_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("episodes.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(80))
    remote_job_id: Mapped[str | None] = mapped_column(String(250), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", server_default="queued")
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    actual_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    runtime_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    episode_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("episodes.id", ondelete="CASCADE"), index=True
    )
    scene_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True
    )
    render_job_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("render_jobs.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(48))
    storage_uri: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    validation_status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (Index("ix_approvals_revision_stage", "revision_id", "stage"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    revision_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("episode_revisions.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String(48))
    decision: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (Index("ix_workflow_runs_status_started_at", "status", "started_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workflow_type: Mapped[str] = mapped_column(String(80))
    trigger: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), default="queued", server_default="queued")
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id", "step_name", "attempt", name="uq_workflow_step_attempt"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    workflow_run_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    step_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    output_data: Mapped[dict[str, Any] | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_object_created_at", "object_type", "object_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    object_type: Mapped[str] = mapped_column(String(80))
    object_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PublishingPackage(Base):
    __tablename__ = "publishing_packages"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    episode_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("episodes.id", ondelete="CASCADE"), index=True
    )
    publishing_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON_DOCUMENT, default=dict
    )
    status: Mapped[str] = mapped_column(String(32), default="draft", server_default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
