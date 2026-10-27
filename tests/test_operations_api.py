from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tests.test_topics_api import StaticTokenVerifier
from universal_video_maker.api import create_app
from universal_video_maker.config import Settings
from universal_video_maker.db.models import (
    Approval,
    AuditEvent,
    Base,
    Episode,
    EpisodeRevision,
    RenderJob,
    Topic,
    WorkflowRun,
    WorkflowStep,
)
from universal_video_maker.security import Role

HEADERS = {"Authorization": "Bearer valid-token"}


def make_client(tmp_path: Path, role: Role, seed: bool = False) -> TestClient:
    url = f"sqlite:///{tmp_path / 'ops.sqlite'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    if seed:
        with Session(engine) as session:
            topic = Topic(title="Solar", category="Science", status="approved")
            session.add(topic)
            session.flush()
            episode = Episode(topic_id=topic.id, title="Solar episode")
            session.add(episode)
            session.flush()
            revision = EpisodeRevision(episode_id=episode.id, revision=1, content={})
            session.add(revision)
            session.flush()
            session.add(Approval(revision_id=revision.id, stage="outline", decision="approved"))
            session.add(AuditEvent(object_type="topic", action="topic.created"))
            run = WorkflowRun(
                workflow_type="research",
                trigger="manual",
                status="failed",
                created_at=datetime.now(UTC),
            )
            session.add(run)
            session.flush()
            session.add(
                WorkflowStep(
                    workflow_run_id=run.id, step_name="collect", status="failed", error="boom"
                )
            )
            session.add(
                RenderJob(
                    episode_id=episode.id,
                    provider="runpod",
                    status="failed",
                    actual_cost=Decimal("1.5"),
                    failure_reason="oom",
                    created_at=datetime.now(UTC),
                )
            )
            session.commit()
    engine.dispose()
    app = create_app(
        Settings(_env_file=None, database_url=url),
        token_verifier=StaticTokenVerifier(frozenset({role})),
    )
    return TestClient(app)


def test_governance_and_monitoring_require_authentication(tmp_path: Path) -> None:
    with make_client(tmp_path, Role.ADMIN) as client:
        assert client.get("/api/v1/governance/summary").status_code == 401
        assert client.get("/api/v1/monitoring/overview").status_code == 401


def test_governance_summary_counts_persisted_records(tmp_path: Path) -> None:
    with make_client(tmp_path, Role.ADMIN, seed=True) as client:
        summary = client.get("/api/v1/governance/summary", headers=HEADERS).json()
        approvals = client.get("/api/v1/governance/approvals", headers=HEADERS).json()
        audit = client.get("/api/v1/governance/audit-events", headers=HEADERS).json()

    assert summary["topics_total"] == 1
    assert summary["topics_approved"] == 1
    assert summary["episodes_with_approval"] == 1
    assert approvals[0]["episode_title"] == "Solar episode"
    assert audit[0]["action"] == "topic.created"


def test_governance_summary_is_empty_without_data(tmp_path: Path) -> None:
    with make_client(tmp_path, Role.VIEWER) as client:
        summary = client.get("/api/v1/governance/summary", headers=HEADERS).json()
    assert summary["topics_total"] == 0
    assert summary["claims_total"] == 0


def test_audit_events_are_admin_only(tmp_path: Path) -> None:
    with make_client(tmp_path, Role.EDITOR) as client:
        response = client.get("/api/v1/governance/audit-events", headers=HEADERS)
    assert response.status_code == 403


def test_monitoring_overview_reports_real_records(tmp_path: Path) -> None:
    with make_client(tmp_path, Role.VIEWER, seed=True) as client:
        overview = client.get("/api/v1/monitoring/overview?hours=24", headers=HEADERS).json()

    assert overview["workflow_runs"] == 1
    assert overview["workflow_failed"] == 1
    assert overview["render_failed"] == 1
    assert Decimal(overview["spend"]) == Decimal("1.5")
    assert sum(bucket["workflow_runs"] for bucket in overview["series"]) == 1
    assert {failure["source"] for failure in overview["failures"]} == {
        "workflow_step",
        "render_job",
    }
    assert overview["recent_jobs"][0]["episode_title"] == "Solar episode"


def test_monitoring_requires_analytics_permission(tmp_path: Path) -> None:
    with make_client(tmp_path, Role.EDITOR) as client:
        response = client.get("/api/v1/monitoring/overview", headers=HEADERS)
    assert response.status_code == 403


@pytest.mark.parametrize("hours", [0, 721])
def test_monitoring_rejects_invalid_window(tmp_path: Path, hours: int) -> None:
    with make_client(tmp_path, Role.ADMIN) as client:
        response = client.get(f"/api/v1/monitoring/overview?hours={hours}", headers=HEADERS)
    assert response.status_code == 422
