import json
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from universal_video_maker.config import Settings
from universal_video_maker.db.models import (
    Approval,
    Base,
    Episode,
    EpisodeRevision,
    Scene,
    Topic,
    User,
)
from universal_video_maker.errors import ConflictError, ForbiddenError
from universal_video_maker.runpod import (
    RemoteJob,
    RunPodClientError,
    RunPodJobService,
    RunPodServerlessClient,
)
from universal_video_maker.security import Principal, Role


class FakeTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, str], bytes | None, int]] = []

    def request(
        self,
        method,
        url,
        *,
        headers,
        body,
        timeout,
    ) -> bytes:
        self.calls.append((method, url, dict(headers), body, timeout))
        response = self.responses.pop(0)
        return json.dumps(response).encode("utf-8")


@pytest.fixture
def database_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def create_approved_storyboard(session: Session):
    editor_id = uuid4()
    editor = User(
        id=editor_id,
        email=f"{editor_id}@example.org",
        role=Role.EDITOR.value,
    )
    topic = Topic(title="Aditya-L1", category="mission")
    session.add_all([editor, topic])
    session.flush()
    episode = Episode(topic_id=topic.id, title="A solar observatory", status="draft")
    session.add(episode)
    session.flush()
    revision = EpisodeRevision(
        episode_id=episode.id,
        revision=1,
        created_by=editor_id,
        content={"kind": "storyboard", "scene_count": 1},
    )
    session.add(revision)
    session.flush()
    session.add(
        Scene(
            revision_id=revision.id,
            scene_number=1,
            content={"visual_prompt": "Earth and spacecraft."},
        )
    )
    session.add(
        Approval(
            revision_id=revision.id,
            reviewer_id=editor_id,
            stage="render",
            decision="approved",
        )
    )
    session.flush()
    return editor_id, episode, revision


def settings(**overrides) -> Settings:
    return Settings(
        runpod_api_key="test-api-key",
        runpod_serverless_endpoint_id="endpoint_123",
        **overrides,
    )


def test_client_submits_and_parses_serverless_job_without_logging_token() -> None:
    transport = FakeTransport([{"id": "remote_123", "status": "IN_QUEUE"}])
    client = RunPodServerlessClient(
        api_key="secret-token",
        api_base_url="https://api.runpod.io/v2",
        timeout_seconds=12,
        transport=transport,
    )

    job = client.submit("endpoint_123", {"sbe_job_id": "local-1"})

    assert job == RemoteJob(job_id="remote_123", status="IN_QUEUE")
    method, url, headers, body, timeout = transport.calls[0]
    assert method == "POST"
    assert url == "https://api.runpod.io/v2/endpoint_123/run"
    assert headers["Authorization"] == "Bearer secret-token"
    assert json.loads(body or b"{}") == {"input": {"sbe_job_id": "local-1"}}
    assert timeout == 12


def test_client_rejects_invalid_endpoint_and_response_shape() -> None:
    client = RunPodServerlessClient(
        api_key="secret-token",
        api_base_url="https://api.runpod.io/v2",
        transport=FakeTransport([{"status": "IN_QUEUE"}]),
    )

    with pytest.raises(ValueError, match="endpoint ID"):
        client.submit("endpoint/unsafe", {})
    with pytest.raises(RunPodClientError, match="job identity"):
        client.submit("endpoint_123", {})


def test_client_rejects_insecure_api_base_url() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        RunPodServerlessClient(api_key="token", api_base_url="http://localhost")


def test_create_job_requires_exact_revision_render_approval(database_engine) -> None:
    with Session(database_engine) as session:
        editor_id, episode, revision = create_approved_storyboard(session)
        session.query(Approval).delete()
        session.flush()
        principal = Principal(str(editor_id), frozenset({Role.EDITOR}))
        service = RunPodJobService(session, settings())

        with pytest.raises(ForbiddenError, match="human render approval"):
            service.create_job(
                principal=principal,
                actor_id=editor_id,
                storyboard_revision_id=revision.id,
                input_payload={"episode_id": str(episode.id)},
                estimated_cost_usd=Decimal("1.25"),
            )


def test_create_and_dispatch_job_enforces_budget_and_tracks_state(database_engine) -> None:
    with Session(database_engine) as session:
        editor_id, episode, revision = create_approved_storyboard(session)
        principal = Principal(str(editor_id), frozenset({Role.EDITOR}))
        service = RunPodJobService(
            session,
            settings(runpod_monthly_budget_usd=Decimal("5.00")),
        )
        job = service.create_job(
            principal=principal,
            actor_id=editor_id,
            storyboard_revision_id=revision.id,
            input_payload={"episode_id": str(episode.id)},
            estimated_cost_usd=Decimal("1.25"),
            max_retries=1,
        )
        transport = FakeTransport([{"id": "remote_123", "status": "IN_QUEUE"}])
        client = RunPodServerlessClient(
            api_key="test-api-key",
            api_base_url="https://api.runpod.io/v2",
            transport=transport,
        )

        job = service.dispatch(job.id, client=client)

        assert job.status == "submitted"
        assert job.remote_job_id == "remote_123"
        assert job.parameters["attempts"][0]["status"] == "IN_QUEUE"
        request_body = json.loads(transport.calls[0][3] or b"{}")
        assert request_body["input"]["sbe_job_id"] == str(job.id)


def test_job_refresh_records_completion_cost_and_runtime(database_engine) -> None:
    with Session(database_engine) as session:
        editor_id, episode, revision = create_approved_storyboard(session)
        principal = Principal(str(editor_id), frozenset({Role.EDITOR}))
        service = RunPodJobService(session, settings())
        job = service.create_job(
            principal=principal,
            actor_id=editor_id,
            storyboard_revision_id=revision.id,
            input_payload={"episode_id": str(episode.id)},
            estimated_cost_usd=Decimal("1.25"),
        )
        job.status = "running"
        job.remote_job_id = "remote_123"
        job.parameters = {
            **job.parameters,
            "attempts": [{"remote_job_id": "remote_123", "status": "IN_PROGRESS"}],
        }
        client = RunPodServerlessClient(
            api_key="test-api-key",
            api_base_url="https://api.runpod.io/v2",
            transport=FakeTransport(
                [
                    {
                        "id": "remote_123",
                        "status": "COMPLETED",
                        "executionTime": 1201,
                        "cost": "0.42",
                        "output": {"manifest": "output.json"},
                    }
                ]
            ),
        )

        job = service.refresh_status(job.id, client=client)

        assert job.status == "completed"
        assert job.runtime_seconds == 2
        assert job.actual_cost == Decimal("0.42")
        assert job.completed_at is not None
        assert job.parameters["attempts"][-1]["status"] == "COMPLETED"


def test_create_job_rejects_cost_over_limit(database_engine) -> None:
    with Session(database_engine) as session:
        editor_id, _, revision = create_approved_storyboard(session)
        principal = Principal(str(editor_id), frozenset({Role.EDITOR}))
        service = RunPodJobService(
            session,
            settings(runpod_max_job_cost_usd=Decimal("1.00")),
        )

        with pytest.raises(ConflictError, match="per-job limit"):
            service.create_job(
                principal=principal,
                actor_id=editor_id,
                storyboard_revision_id=revision.id,
                input_payload={},
                estimated_cost_usd=Decimal("1.01"),
            )


def test_retry_is_bounded_authorized_and_reserves_cost(database_engine) -> None:
    with Session(database_engine) as session:
        editor_id, episode, revision = create_approved_storyboard(session)
        principal = Principal(str(editor_id), frozenset({Role.EDITOR}))
        service = RunPodJobService(session, settings())
        job = service.create_job(
            principal=principal,
            actor_id=editor_id,
            storyboard_revision_id=revision.id,
            input_payload={"episode_id": str(episode.id)},
            estimated_cost_usd=Decimal("1.25"),
            max_retries=1,
        )
        job.status = "failed"
        job.parameters = {
            **job.parameters,
            "attempts": [{"remote_job_id": "remote_123", "status": "FAILED"}],
        }
        session.flush()

        retried = service.retry_failed_job(
            job.id,
            principal=principal,
            actor_id=editor_id,
        )

        assert retried.status == "queued"
        assert retried.remote_job_id is None
        assert retried.estimated_cost == Decimal("2.50")
        retried.status = "failed"
        retried.parameters = {
            **retried.parameters,
            "attempts": [
                *retried.parameters["attempts"],
                {"remote_job_id": "remote_456", "status": "FAILED"},
            ],
        }
        session.flush()

        with pytest.raises(ConflictError, match="maximum render retries"):
            service.retry_failed_job(
                job.id,
                principal=principal,
                actor_id=editor_id,
            )

        with pytest.raises(ForbiddenError):
            service.retry_failed_job(
                job.id,
                principal=Principal(str(editor_id), frozenset({Role.VIEWER})),
                actor_id=editor_id,
            )
