import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tests.test_claims_episodes_api import HEADERS, SubjectVerifier
from tests.test_rendering import FakeInspector, output_bundle
from tests.test_runpod import FakeTransport
from tests.test_storyboard import create_episode_plan
from universal_video_maker.api import create_app
from universal_video_maker.config import Settings
from universal_video_maker.db.models import Base, RenderJob
from universal_video_maker.runpod import RunPodClientError, RunPodServerlessClient
from universal_video_maker.security import Role


def make_client(
    tmp_path: Path,
    *,
    endpoint: str | None = "endpoint_123",
    responses=None,
    object_store=None,
    inspector=None,
) -> tuple[TestClient, str, FakeTransport]:
    url = f"sqlite:///{tmp_path / 'render.sqlite'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        editor_id, _, episode, _, _ = create_episode_plan(session)
        session.commit()
        episode_id = str(episode.id)
    engine.dispose()
    transport = FakeTransport(responses or [{"id": "remote_1", "status": "IN_QUEUE"}])
    app = create_app(
        Settings(
            _env_file=None,
            database_url=url,
            runpod_api_key="key",
            runpod_serverless_endpoint_id=endpoint,
        ),
        token_verifier=SubjectVerifier(frozenset({Role.EDITOR}), str(editor_id)),
        render_client_factory=lambda s: RunPodServerlessClient.from_settings(
            s, transport=transport
        ),
        object_store_factory=(lambda s: object_store) if object_store else None,
        artifact_inspector_factory=(lambda s, store: inspector) if inspector else None,
    )
    return TestClient(app), episode_id, transport


def approve(client: TestClient, episode_id: str, stage: str):
    return client.post(
        f"/api/v1/episodes/{episode_id}/approvals",
        headers=HEADERS,
        json={"decision": "approved", "note": "Looks right.", "stage": stage},
    )


def test_plan_to_render_flow_dispatches_to_runpod(tmp_path: Path) -> None:
    client, episode_id, transport = make_client(tmp_path)
    base = f"/api/v1/episodes/{episode_id}"
    with client:
        assert client.post(f"{base}/storyboard", headers=HEADERS).status_code == 409
        assert approve(client, episode_id, "render").status_code == 409
        assert approve(client, episode_id, "plan").status_code == 201

        storyboard = client.post(f"{base}/storyboard", headers=HEADERS)
        assert storyboard.status_code == 201
        assert storyboard.json()["scene_count"] == 1
        assert client.post(f"{base}/storyboard", headers=HEADERS).status_code == 409

        listed = client.get("/api/v1/episodes", headers=HEADERS).json()[0]
        assert (listed["stage"], listed["plan_approved"], listed["render_approved"]) == (
            "storyboard",
            True,
            False,
        )
        plan = client.get(f"{base}/plan", headers=HEADERS).json()
        assert plan["plan"]["kind"] == "episode_plan"

        body = {"estimated_cost_usd": "2.50"}
        assert client.post(f"{base}/render", headers=HEADERS, json=body).status_code == 403
        assert approve(client, episode_id, "render").status_code == 201

        rendered = client.post(f"{base}/render", headers=HEADERS, json=body)
        assert rendered.status_code == 201, rendered.text
        assert rendered.json()["remote_job_id"] == "remote_1"
        assert rendered.json()["status"] == "submitted"
        assert client.post(f"{base}/render", headers=HEADERS, json=body).status_code == 409

        listed = client.get("/api/v1/episodes", headers=HEADERS).json()[0]
        assert listed["render_approved"] is True
        assert listed["latest_render_status"] == "submitted"

    sent = json.loads(transport.calls[0][3] or b"{}")["input"]
    assert sent["episode_id"] == episode_id
    assert sent["scenes"][0]["narration"] == "Aditya-L1 launched on 2 September 2023."
    assert sent["shorts"] == []


class FakeStore:
    def __init__(self) -> None:
        self.bucket_checked = False

    def ensure_bucket(self) -> None:
        self.bucket_checked = True

    def presigned_get_url(self, storage_uri: str) -> str:
        return f"http://minio.test/download?uri={storage_uri}"


def test_refresh_registers_outputs_and_exposes_download_links(tmp_path: Path) -> None:
    bundle, facts = output_bundle()
    artifacts = [
        {
            "kind": a.kind,
            "variant": a.variant,
            "storage_uri": a.storage_uri,
            "checksum": a.checksum,
        }
        for a in bundle.artifacts
    ]
    client, episode_id, _ = make_client(
        tmp_path,
        responses=[
            {"id": "remote_1", "status": "IN_QUEUE"},
            {"id": "remote_1", "status": "IN_PROGRESS"},
            {"id": "remote_1", "status": "COMPLETED", "output": {"artifacts": artifacts}},
        ],
        object_store=FakeStore(),
        inspector=FakeInspector(facts),
    )
    base = f"/api/v1/episodes/{episode_id}"
    with client:
        assert client.get(f"{base}/render", headers=HEADERS).json()["job_id"] is None
        assert client.post(f"{base}/render/refresh", headers=HEADERS).status_code == 404
        assert approve(client, episode_id, "plan").status_code == 201
        assert client.post(f"{base}/storyboard", headers=HEADERS).status_code == 201
        assert approve(client, episode_id, "render").status_code == 201
        body = {"estimated_cost_usd": "2.50"}
        assert client.post(f"{base}/render", headers=HEADERS, json=body).status_code == 201

        running = client.post(f"{base}/render/refresh", headers=HEADERS).json()
        assert (running["status"], running["assets"]) == ("running", [])

        done = client.post(f"{base}/render/refresh", headers=HEADERS)
        assert done.status_code == 200, done.text
        payload = done.json()
        assert payload["status"] == "completed"
        assert payload["message"] is None
        assert len(payload["assets"]) == 9
        assert all(a["download_url"].startswith("http://minio.test/") for a in payload["assets"])

        again = client.get(f"{base}/render", headers=HEADERS).json()
        assert len(again["assets"]) == 9


def test_refresh_reports_unverifiable_outputs(tmp_path: Path) -> None:
    client, episode_id, _ = make_client(
        tmp_path,
        responses=[
            {"id": "remote_1", "status": "IN_QUEUE"},
            {"id": "remote_1", "status": "COMPLETED", "output": {"note": "no artifacts"}},
        ],
        object_store=FakeStore(),
        inspector=FakeInspector({}),
    )
    base = f"/api/v1/episodes/{episode_id}"
    with client:
        approve(client, episode_id, "plan")
        client.post(f"{base}/storyboard", headers=HEADERS)
        approve(client, episode_id, "render")
        client.post(f"{base}/render", headers=HEADERS, json={"estimated_cost_usd": "1.00"})
        result = client.post(f"{base}/render/refresh", headers=HEADERS).json()

    assert result["status"] == "completed"
    assert result["assets"] == []
    assert "output.artifacts" in result["message"]


def test_cancel_frees_the_episode_for_a_new_render(tmp_path: Path) -> None:
    client, episode_id, _ = make_client(
        tmp_path,
        responses=[
            {"id": "remote_1", "status": "IN_QUEUE"},
            {"id": "remote_1", "status": "CANCELLED"},
            {"id": "remote_2", "status": "IN_QUEUE"},
        ],
    )
    base = f"/api/v1/episodes/{episode_id}"
    body = {"estimated_cost_usd": "1.00"}
    with client:
        assert client.post(f"{base}/render/cancel", headers=HEADERS).status_code == 404
        approve(client, episode_id, "plan")
        client.post(f"{base}/storyboard", headers=HEADERS)
        approve(client, episode_id, "render")
        assert client.post(f"{base}/render", headers=HEADERS, json=body).status_code == 201
        assert client.post(f"{base}/render", headers=HEADERS, json=body).status_code == 409

        cancelled = client.post(f"{base}/render/cancel", headers=HEADERS)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert client.post(f"{base}/render", headers=HEADERS, json=body).status_code == 201


def test_cancel_treats_a_missing_remote_job_as_cancelled(tmp_path: Path) -> None:
    client, episode_id, transport = make_client(tmp_path)
    base = f"/api/v1/episodes/{episode_id}"
    body = {"estimated_cost_usd": "1.00"}
    with client:
        approve(client, episode_id, "plan")
        client.post(f"{base}/storyboard", headers=HEADERS)
        approve(client, episode_id, "render")
        assert client.post(f"{base}/render", headers=HEADERS, json=body).status_code == 201

        def not_found(*args, **kwargs):
            raise RunPodClientError("RunPod API returned HTTP 404.", status_code=404)

        transport.request = not_found
        cancelled = client.post(f"{base}/render/cancel", headers=HEADERS)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"


def test_render_reports_missing_runpod_endpoint_without_queueing(tmp_path: Path) -> None:
    client, episode_id, _ = make_client(tmp_path, endpoint=None)
    with client:
        response = client.post(
            f"/api/v1/episodes/{episode_id}/render",
            headers=HEADERS,
            json={"estimated_cost_usd": "1.00"},
        )

    assert response.status_code == 503
    assert "UVM_RUNPOD_SERVERLESS_ENDPOINT_ID" in response.json()["error"]["message"]
    engine = create_engine(f"sqlite:///{tmp_path / 'render.sqlite'}")
    with Session(engine) as session:
        assert session.scalars(select(RenderJob)).all() == []
    engine.dispose()
