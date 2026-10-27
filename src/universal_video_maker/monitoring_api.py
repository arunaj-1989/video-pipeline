from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from universal_video_maker.db.models import Episode, RenderJob, WorkflowRun, WorkflowStep
from universal_video_maker.identity import CurrentPrincipal
from universal_video_maker.security import Permission, require_permission
from universal_video_maker.topics_api import DatabaseSession

router = APIRouter(prefix="/monitoring", tags=["monitoring"])

ACTIVE_RENDER_STATUSES = ("queued", "submitted", "running")
BUCKET_COUNT = 12


class RenderJobRead(BaseModel):
    id: UUID
    episode_title: str
    provider: str
    remote_job_id: str | None
    status: str
    runtime_seconds: int | None
    cost: Decimal | None
    failure_reason: str | None
    created_at: datetime


class FailureRead(BaseModel):
    id: str
    source: Literal["workflow_step", "render_job"]
    title: str
    detail: str
    occurred_at: datetime


class ChartBucket(BaseModel):
    start: datetime
    workflow_runs: int


class MonitoringOverview(BaseModel):
    window_hours: int
    generated_at: datetime
    workflow_runs: int
    workflow_running: int
    workflow_completed: int
    workflow_failed: int
    render_jobs: int
    render_active: int
    render_failed: int
    spend: Decimal
    failed_spend: Decimal
    render_queue: dict[str, int]
    series: list[ChartBucket]
    recent_jobs: list[RenderJobRead]
    failures: list[FailureRead]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@router.get("/overview", response_model=MonitoringOverview)
def monitoring_overview(
    session: DatabaseSession,
    principal: CurrentPrincipal,
    hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> MonitoringOverview:
    require_permission(principal, Permission.ANALYTICS_READ)
    now = datetime.now(UTC)
    since = now - timedelta(hours=hours)

    workflow_counts = dict(
        tuple(row)
        for row in session.execute(
            select(WorkflowRun.status, func.count(WorkflowRun.id))
            .where(WorkflowRun.created_at >= since)
            .group_by(WorkflowRun.status)
        ).all()
    )
    render_counts = dict(
        tuple(row)
        for row in session.execute(
            select(RenderJob.status, func.count(RenderJob.id))
            .where(RenderJob.created_at >= since)
            .group_by(RenderJob.status)
        ).all()
    )
    active_queue = dict(
        tuple(row)
        for row in session.execute(
            select(RenderJob.status, func.count(RenderJob.id))
            .where(RenderJob.status.in_(ACTIVE_RENDER_STATUSES))
            .group_by(RenderJob.status)
        ).all()
    )

    def job_cost(job: RenderJob) -> Decimal:
        return job.actual_cost if job.actual_cost is not None else Decimal(0)

    window_jobs = list(session.scalars(select(RenderJob).where(RenderJob.created_at >= since)))
    spend = sum((job_cost(job) for job in window_jobs), Decimal(0))
    failed_spend = sum((job_cost(job) for job in window_jobs if job.status == "failed"), Decimal(0))

    bucket_size = timedelta(hours=hours) / BUCKET_COUNT
    series = [
        ChartBucket(start=since + bucket_size * index, workflow_runs=0)
        for index in range(BUCKET_COUNT)
    ]
    for created_at in session.scalars(
        select(WorkflowRun.created_at).where(WorkflowRun.created_at >= since)
    ):
        index = min(int((_aware(created_at) - since) / bucket_size), BUCKET_COUNT - 1)
        series[max(index, 0)].workflow_runs += 1

    recent_rows = session.execute(
        select(RenderJob, Episode.title)
        .join(Episode, RenderJob.episode_id == Episode.id)
        .order_by(RenderJob.created_at.desc(), RenderJob.id)
        .limit(25)
    ).all()
    recent_jobs = [
        RenderJobRead(
            id=job.id,
            episode_title=title,
            provider=job.provider,
            remote_job_id=job.remote_job_id,
            status=job.status,
            runtime_seconds=job.runtime_seconds,
            cost=job.actual_cost if job.actual_cost is not None else job.estimated_cost,
            failure_reason=job.failure_reason,
            created_at=job.created_at,
        )
        for job, title in recent_rows
    ]

    failures = [
        FailureRead(
            id=f"workflow-step:{step.id}",
            source="workflow_step",
            title=f"{step.step_name} failed (attempt {step.attempt})",
            detail=(step.error or "No error message recorded.")[:300],
            occurred_at=step.completed_at or step.started_at or since,
        )
        for step in session.scalars(
            select(WorkflowStep)
            .where(WorkflowStep.status == "failed")
            .order_by(WorkflowStep.completed_at.desc().nulls_last(), WorkflowStep.id)
            .limit(10)
        )
    ] + [
        FailureRead(
            id=f"render-job:{job.id}",
            source="render_job",
            title=f"Render job failed on {job.provider}",
            detail=(job.failure_reason or "No failure reason recorded.")[:300],
            occurred_at=job.completed_at or job.created_at,
        )
        for job in session.scalars(
            select(RenderJob)
            .where(RenderJob.status == "failed")
            .order_by(RenderJob.created_at.desc(), RenderJob.id)
            .limit(10)
        )
    ]
    failures.sort(key=lambda failure: _aware(failure.occurred_at), reverse=True)

    return MonitoringOverview(
        window_hours=hours,
        generated_at=now,
        workflow_runs=sum(workflow_counts.values()),
        workflow_running=workflow_counts.get("running", 0),
        workflow_completed=workflow_counts.get("completed", 0),
        workflow_failed=workflow_counts.get("failed", 0),
        render_jobs=sum(render_counts.values()),
        render_active=sum(active_queue.values()),
        render_failed=render_counts.get("failed", 0),
        spend=spend,
        failed_spend=failed_spend,
        render_queue={status: active_queue.get(status, 0) for status in ACTIVE_RENDER_STATUSES},
        series=series,
        recent_jobs=recent_jobs,
        failures=failures[:10],
    )
