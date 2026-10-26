"""RunPod Serverless dispatch with explicit approvals and bounded retries."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from universal_video_maker.config import Settings
from universal_video_maker.db.models import (
    Approval,
    AuditEvent,
    Episode,
    EpisodeRevision,
    RenderJob,
    Scene,
    User,
)
from universal_video_maker.errors import ConflictError, ForbiddenError, NotFoundError
from universal_video_maker.security import Permission, Principal, require_permission

ENDPOINT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_RETRIES = 3
ACTIVE_JOB_STATUSES = ("queued", "submitted", "running")


class RunPodClientError(Exception):
    """A sanitized RunPod API transport or response error."""


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: int,
    ) -> bytes: ...


class UrllibTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: int,
    ) -> bytes:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            raise RunPodClientError(
                f"RunPod API returned HTTP {exc.code}."
            ) from exc
        except URLError as exc:
            raise RunPodClientError("RunPod API request failed.") from exc
        except TimeoutError as exc:
            raise RunPodClientError("RunPod API request timed out.") from exc
        except OSError as exc:
            raise RunPodClientError("RunPod API request failed.") from exc


@dataclass(frozen=True)
class RemoteJob:
    job_id: str
    status: str
    output: Mapping[str, Any] | None = None
    error: str | None = None
    execution_time_ms: int | None = None
    cost_usd: Decimal | None = None


class RunPodServerlessClient:
    """Small REST client for a configured RunPod Serverless endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        api_base_url: str,
        timeout_seconds: int = 30,
        transport: HttpTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("RunPod API key cannot be empty.")
        if timeout_seconds < 1:
            raise ValueError("RunPod timeout must be positive.")
        parsed_base_url = urlsplit(api_base_url)
        if (
            parsed_base_url.scheme != "https"
            or not parsed_base_url.netloc
            or parsed_base_url.username is not None
            or parsed_base_url.password is not None
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise ValueError("RunPod API base URL must be a valid HTTPS URL.")
        self._api_key = api_key
        self._base_url = api_base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport or UrllibTransport()

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        transport: HttpTransport | None = None,
    ) -> RunPodServerlessClient:
        if (
            settings.runpod_api_key is None
            or not settings.runpod_api_key.get_secret_value().strip()
        ):
            raise ValueError("UVM_RUNPOD_API_KEY must be configured before dispatch.")
        return cls(
            api_key=settings.runpod_api_key.get_secret_value(),
            api_base_url=settings.runpod_api_base_url,
            timeout_seconds=settings.runpod_request_timeout_seconds,
            transport=transport,
        )

    def submit(self, endpoint_id: str, payload: Mapping[str, Any]) -> RemoteJob:
        data = self._request(
            "POST",
            self._endpoint_path(endpoint_id, "run"),
            payload={"input": dict(payload)},
        )
        return self._parse_job(data)

    def get_status(self, endpoint_id: str, job_id: str) -> RemoteJob:
        return self._parse_job(
            self._request(
                "GET",
                self._endpoint_path(endpoint_id, f"status/{self._safe_job_id(job_id)}"),
            )
        )

    def cancel(self, endpoint_id: str, job_id: str) -> RemoteJob:
        return self._parse_job(
            self._request(
                "POST",
                self._endpoint_path(endpoint_id, f"cancel/{self._safe_job_id(job_id)}"),
                payload={},
            )
        )

    @staticmethod
    def _safe_job_id(job_id: str) -> str:
        if not ENDPOINT_ID_PATTERN.fullmatch(job_id):
            raise ValueError("RunPod job ID contains invalid characters.")
        return job_id

    @classmethod
    def _endpoint_path(cls, endpoint_id: str, suffix: str) -> str:
        if not ENDPOINT_ID_PATTERN.fullmatch(endpoint_id):
            raise ValueError("RunPod endpoint ID contains invalid characters.")
        return f"/{endpoint_id}/{suffix}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        body = None
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }
        if payload is not None:
            try:
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ValueError("RunPod request payload must be JSON serializable.") from exc
            headers["Content-Type"] = "application/json"
        response = self._transport.request(
            method,
            f"{self._base_url}{path}",
            headers=headers,
            body=body,
            timeout=self._timeout,
        )
        try:
            parsed = json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunPodClientError("RunPod API returned invalid JSON.") from exc
        if not isinstance(parsed, dict):
            raise RunPodClientError("RunPod API returned an invalid response shape.")
        return parsed

    @staticmethod
    def _parse_job(data: Mapping[str, Any]) -> RemoteJob:
        job_id = data.get("id")
        status = data.get("status")
        if (
            not isinstance(job_id, str)
            or not ENDPOINT_ID_PATTERN.fullmatch(job_id)
            or not isinstance(status, str)
        ):
            raise RunPodClientError("RunPod API response is missing job identity or status.")
        output = data.get("output")
        error = data.get("error")
        execution_time = data.get("executionTime")
        cost_value = data.get("cost")
        if output is not None and not isinstance(output, dict):
            raise RunPodClientError("RunPod API returned an invalid job output.")
        if error is not None and not isinstance(error, str):
            error = "Remote job failed."
        if execution_time is not None and (
            not isinstance(execution_time, int)
            or isinstance(execution_time, bool)
            or execution_time < 0
        ):
            raise RunPodClientError("RunPod API returned an invalid execution time.")
        try:
            cost = Decimal(str(cost_value)) if cost_value is not None else None
        except (InvalidOperation, ValueError) as exc:
            raise RunPodClientError("RunPod API returned an invalid cost.") from exc
        if cost is not None and (not cost.is_finite() or cost < 0):
            raise RunPodClientError("RunPod API returned an invalid cost.")
        return RemoteJob(
            job_id=job_id,
            status=status.upper(),
            output=output,
            error=error,
            execution_time_ms=execution_time if execution_time is not None else None,
            cost_usd=cost,
        )


class RunPodJobService:
    """Persist approved render requests and manage serverless job attempts."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def create_job(
        self,
        *,
        principal: Principal,
        actor_id: UUID,
        storyboard_revision_id: UUID,
        input_payload: Mapping[str, Any],
        estimated_cost_usd: Decimal,
        max_retries: int = 1,
    ) -> RenderJob:
        require_permission(principal, Permission.RENDER_SUBMIT)
        if principal.subject != str(actor_id):
            raise ForbiddenError("Submitter identity must match the authenticated principal.")
        actor = self._session.get(User, actor_id)
        if actor is None:
            raise NotFoundError(f"Submitter {actor_id} does not exist.")
        if actor.status != "active" or actor.role not in {
            role.value for role in principal.roles
        }:
            raise ForbiddenError("Submitter account is inactive or roles do not match.")
        if not estimated_cost_usd.is_finite() or estimated_cost_usd <= 0:
            raise ValueError("Estimated cost must be a positive finite USD amount.")
        if estimated_cost_usd > self._settings.runpod_max_job_cost_usd:
            raise ConflictError("Estimated job cost exceeds the configured per-job limit.")
        if not 0 <= max_retries <= MAX_RETRIES:
            raise ValueError(f"Maximum retries must be between 0 and {MAX_RETRIES}.")
        try:
            json.dumps(input_payload)
        except (TypeError, ValueError) as exc:
            raise ValueError("Render job input must be JSON serializable.") from exc

        revision = self._session.get(EpisodeRevision, storyboard_revision_id)
        if revision is None:
            raise NotFoundError(f"Storyboard revision {storyboard_revision_id} does not exist.")
        episode = self._session.get(Episode, revision.episode_id)
        if episode is None:
            raise NotFoundError(f"Episode {revision.episode_id} does not exist.")
        if episode.status != "draft" or revision.content.get("kind") != "storyboard":
            raise ConflictError("Rendering requires a storyboard on a draft episode.")
        approval = self._session.scalar(
            select(Approval).where(
                Approval.revision_id == storyboard_revision_id,
                Approval.stage == "render",
                Approval.decision == "approved",
            )
        )
        if approval is None:
            raise ForbiddenError(
                "A human render approval for this exact storyboard revision is required."
            )
        scene_count = self._session.scalar(
            select(func.count()).select_from(Scene).where(Scene.revision_id == revision.id)
        )
        if not scene_count or scene_count != revision.content.get("scene_count"):
            raise ConflictError("Storyboard scene records are incomplete.")
        active_job = self._session.scalar(
            select(RenderJob.id).where(
                RenderJob.episode_id == episode.id,
                RenderJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
        if active_job is not None:
            raise ConflictError("Episode already has an active render job.")

        self._lock_month_budget()
        if (
            self._current_month_spend() + estimated_cost_usd
            > self._settings.runpod_monthly_budget_usd
        ):
            raise ConflictError("Estimated job cost would exceed the configured monthly budget.")

        job = RenderJob(
            episode_id=episode.id,
            provider="runpod_serverless",
            status="queued",
            estimated_cost=estimated_cost_usd,
            parameters={
                "storyboard_revision_id": str(revision.id),
                "submitted_by": str(actor_id),
                "max_retries": max_retries,
                "per_attempt_estimate_usd": str(estimated_cost_usd),
                "attempts": [],
                "input_payload": dict(input_payload),
            },
        )
        self._session.add(job)
        self._session.flush()
        self._audit(
            actor_id,
            job,
            "render.job_queued",
            {
                "storyboard_revision_id": str(revision.id),
                "estimated_cost_usd": str(estimated_cost_usd),
            },
        )
        self._session.flush()
        return job

    def dispatch(
        self,
        job_id: UUID,
        *,
        client: RunPodServerlessClient,
        endpoint_id: str | None = None,
    ) -> RenderJob:
        job = self._get_job(job_id)
        if job.status != "queued" or job.remote_job_id is not None:
            raise ConflictError("Only undispatched queued jobs can be dispatched.")
        chosen_endpoint = endpoint_id or self._settings.runpod_serverless_endpoint_id
        if not chosen_endpoint:
            raise ValueError(
                "UVM_RUNPOD_SERVERLESS_ENDPOINT_ID must be configured before dispatch."
            )
        payload = dict(job.parameters["input_payload"])
        payload["sbe_job_id"] = str(job.id)
        remote = client.submit(chosen_endpoint, payload)
        attempts = list(job.parameters.get("attempts", []))
        attempts.append(
            {
                "remote_job_id": remote.job_id,
                "status": remote.status,
                "submitted_at": datetime.now(UTC).isoformat(),
            }
        )
        job.parameters = {**job.parameters, "attempts": attempts}
        job.remote_job_id = remote.job_id
        self._apply_remote_state(job, remote)
        self._audit(
            None,
            job,
            "render.job_dispatched",
            {"remote_job_id": remote.job_id, "remote_status": remote.status},
        )
        self._session.flush()
        return job

    def refresh_status(
        self,
        job_id: UUID,
        *,
        client: RunPodServerlessClient,
        endpoint_id: str | None = None,
    ) -> RenderJob:
        job = self._get_job(job_id)
        if job.status not in {"submitted", "running", "cancelling"} or not job.remote_job_id:
            raise ConflictError("Only active remote jobs can be refreshed.")
        chosen_endpoint = endpoint_id or self._settings.runpod_serverless_endpoint_id
        if not chosen_endpoint:
            raise ValueError(
                "UVM_RUNPOD_SERVERLESS_ENDPOINT_ID must be configured before status checks."
            )
        previous_status = job.status
        remote = client.get_status(chosen_endpoint, job.remote_job_id)
        self._apply_remote_state(job, remote)
        if job.status != previous_status:
            self._audit(
                None,
                job,
                "render.job_status_changed",
                {"previous_status": previous_status, "status": job.status},
            )
        self._session.flush()
        return job

    def cancel(
        self,
        job_id: UUID,
        *,
        principal: Principal,
        actor_id: UUID,
        client: RunPodServerlessClient,
        endpoint_id: str | None = None,
    ) -> RenderJob:
        self._require_actor(principal, actor_id)
        job = self._get_job(job_id)
        if job.status not in {"submitted", "running"} or not job.remote_job_id:
            raise ConflictError("Only active remote jobs can be cancelled.")
        chosen_endpoint = endpoint_id or self._settings.runpod_serverless_endpoint_id
        if not chosen_endpoint:
            raise ValueError("RunPod endpoint ID must be configured before cancellation.")
        remote = client.cancel(chosen_endpoint, job.remote_job_id)
        self._apply_remote_state(job, remote)
        self._audit(
            actor_id,
            job,
            "render.job_cancellation_requested",
            {"remote_job_id": remote.job_id, "remote_status": remote.status},
        )
        self._session.flush()
        return job

    def retry_failed_job(
        self,
        job_id: UUID,
        *,
        principal: Principal,
        actor_id: UUID,
    ) -> RenderJob:
        self._require_actor(principal, actor_id)
        job = self._get_job(job_id)
        if job.status != "failed":
            raise ConflictError("Only terminally failed jobs can be retried.")
        revision_id = UUID(job.parameters["storyboard_revision_id"])
        approval = self._session.scalar(
            select(Approval).where(
                Approval.revision_id == revision_id,
                Approval.stage == "render",
                Approval.decision == "approved",
            )
        )
        if approval is None:
            raise ForbiddenError("A current human render approval is required for retries.")
        attempts = list(job.parameters.get("attempts", []))
        max_retries = job.parameters.get("max_retries", 0)
        if not attempts or attempts[-1].get("status") not in {"FAILED", "TIMED_OUT"}:
            raise ConflictError("A provider-confirmed terminal failure is required for retry.")
        if len(attempts) - 1 >= max_retries:
            raise ConflictError("Configured maximum render retries has been reached.")
        try:
            next_attempt_estimate = Decimal(job.parameters["per_attempt_estimate_usd"])
        except (KeyError, InvalidOperation, TypeError) as exc:
            raise ConflictError("Failed job is missing its per-attempt cost estimate.") from exc
        if not next_attempt_estimate.is_finite() or next_attempt_estimate <= 0:
            raise ConflictError("Failed job has an invalid per-attempt cost estimate.")
        current_estimate = job.estimated_cost or Decimal("0")
        current_actual = job.actual_cost or Decimal("0")
        self._lock_month_budget()
        if (
            self._current_month_spend(excluding_job_id=job.id)
            + max(current_estimate, current_actual)
            + next_attempt_estimate
            > self._settings.runpod_monthly_budget_usd
        ):
            raise ConflictError("Retry would exceed the configured monthly budget.")
        if job.estimated_cost is None:
            raise ConflictError("Failed job is missing its cost estimate.")
        job.estimated_cost += next_attempt_estimate
        job.status = "queued"
        job.remote_job_id = None
        job.failure_reason = None
        self._audit(
            actor_id,
            job,
            "render.job_retry_queued",
            {"attempt": str(len(attempts) + 1)},
        )
        self._session.flush()
        return job

    def _apply_remote_state(self, job: RenderJob, remote: RemoteJob) -> None:
        if job.remote_job_id is not None and job.remote_job_id != remote.job_id:
            raise RunPodClientError("RunPod status response did not match the requested job.")
        state_map = {
            "IN_QUEUE": "submitted",
            "IN_PROGRESS": "running",
            "COMPLETED": "completed",
            "FAILED": "failed",
            "CANCELLED": "cancelled",
            "TIMED_OUT": "failed",
            "CANCELLING": "cancelling",
        }
        state = state_map.get(remote.status)
        if state is None:
            raise RunPodClientError("RunPod returned an unsupported job state.")
        job.status = state
        attempts = list(job.parameters.get("attempts", []))
        if attempts:
            last_attempt = dict(attempts[-1])
            last_attempt["status"] = remote.status
            if remote.execution_time_ms is not None:
                last_attempt["execution_time_ms"] = remote.execution_time_ms
            if remote.cost_usd is not None:
                last_attempt["actual_cost_usd"] = str(remote.cost_usd)
            if remote.output is not None:
                last_attempt["output"] = dict(remote.output)
            attempts[-1] = last_attempt
            job.parameters = {**job.parameters, "attempts": attempts}
            costs = [
                Decimal(str(attempt["actual_cost_usd"]))
                for attempt in attempts
                if attempt.get("actual_cost_usd") is not None
            ]
            if costs:
                job.actual_cost = sum(costs, Decimal("0"))
            runtimes = [
                int(attempt["execution_time_ms"])
                for attempt in attempts
                if attempt.get("execution_time_ms") is not None
            ]
            if runtimes:
                total_runtime_ms = sum(runtimes)
                job.runtime_seconds = (total_runtime_ms + 999) // 1000
        job.failure_reason = (
            remote.error[:1000] if state == "failed" and remote.error else None
        )
        if state in {"completed", "failed", "cancelled"}:
            job.completed_at = datetime.now(UTC)

    def _current_month_spend(self, *, excluding_job_id: UUID | None = None) -> Decimal:
        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query = select(RenderJob).where(
                RenderJob.provider == "runpod_serverless",
                RenderJob.created_at >= month_start.replace(tzinfo=None),
            )
        if excluding_job_id is not None:
            query = query.where(RenderJob.id != excluding_job_id)
        jobs = self._session.scalars(query)
        total = Decimal("0")
        for job in jobs:
            total += max(
                job.estimated_cost or Decimal("0"),
                job.actual_cost or Decimal("0"),
            )
        return total

    def _lock_month_budget(self) -> None:
        if self._session.get_bind().dialect.name == "postgresql":
            self._session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": 357842189120},
            )

    def _require_actor(self, principal: Principal, actor_id: UUID) -> None:
        require_permission(principal, Permission.RENDER_SUBMIT)
        if principal.subject != str(actor_id):
            raise ForbiddenError("Actor identity must match the authenticated principal.")
        actor = self._session.get(User, actor_id)
        if actor is None:
            raise NotFoundError(f"Actor {actor_id} does not exist.")
        if actor.status != "active" or actor.role not in {
            role.value for role in principal.roles
        }:
            raise ForbiddenError("Actor account is inactive or roles do not match.")

    def _get_job(self, job_id: UUID) -> RenderJob:
        job = self._session.get(RenderJob, job_id)
        if job is None:
            raise NotFoundError(f"Render job {job_id} does not exist.")
        return job

    def _audit(
        self,
        actor_id: UUID | None,
        job: RenderJob,
        action: str,
        details: dict[str, str],
    ) -> None:
        self._session.add(
            AuditEvent(
                actor_id=actor_id,
                object_type="render_job",
                object_id=job.id,
                action=action,
                details=details,
            )
        )
