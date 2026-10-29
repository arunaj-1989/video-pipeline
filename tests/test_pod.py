import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from universal_video_maker.config import Settings
from universal_video_maker.runpod import (
    RunPodPodClient,
    RunPodServerlessClient,
    build_render_client,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runpod_worker"))

from pod_server import create_app  # noqa: E402

KEY = "pod-test-key"
AUTH = {"Authorization": f"Bearer {KEY}"}


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def request(self, method, url, *, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        return json.dumps({"id": "job1", "status": "IN_QUEUE"}).encode()


def pod_settings(**overrides) -> Settings:
    values = {
        "runpod_pod_url": "https://abc-8000.proxy.runpod.net/",
        "runpod_pod_api_key": KEY,
        "runpod_serverless_endpoint_id": "endpoint123",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_pod_client_posts_to_pod_without_endpoint_id() -> None:
    transport = RecordingTransport()
    client = RunPodPodClient.from_settings(pod_settings(), transport=transport)

    job = client.submit("pod", {"episode_id": "e1"})
    client.get_status("pod", "job1")
    client.cancel("pod", "job1")

    assert job.job_id == "job1"
    urls = [(call[0], call[1]) for call in transport.calls]
    assert urls == [
        ("POST", "https://abc-8000.proxy.runpod.net/run"),
        ("GET", "https://abc-8000.proxy.runpod.net/status/job1"),
        ("POST", "https://abc-8000.proxy.runpod.net/cancel/job1"),
    ]
    assert json.loads(transport.calls[0][3]) == {"input": {"episode_id": "e1"}}
    assert transport.calls[0][2]["Authorization"].endswith(KEY)


def test_pod_url_takes_priority_over_serverless() -> None:
    settings = pod_settings()
    assert settings.runpod_dispatch_target == "pod"
    assert isinstance(build_render_client(settings), RunPodPodClient)


def test_serverless_is_used_without_a_pod_url() -> None:
    settings = pod_settings(runpod_pod_url=None, runpod_api_key="serverless-key")
    assert settings.runpod_dispatch_target == "endpoint123"
    client = build_render_client(settings)
    assert type(client) is RunPodServerlessClient


def test_pod_client_requires_api_key_and_https() -> None:
    with pytest.raises(ValueError, match="POD_API_KEY"):
        RunPodPodClient.from_settings(pod_settings(runpod_pod_api_key=None))
    with pytest.raises(ValueError, match="HTTPS"):
        pod_settings(runpod_pod_url="http://abc.example.com")


def wait_for(client: TestClient, job_id: str, wanted: set[str]) -> dict:
    for _ in range(100):
        body = client.get(f"/status/{job_id}", headers=AUTH).json()
        if body["status"] in wanted:
            return body
        time.sleep(0.02)
    raise AssertionError(f"job never reached {wanted}")


def test_pod_server_runs_a_job_to_completion() -> None:
    app = create_app(api_key=KEY, runner=lambda job_input: {"artifacts": [job_input["n"]]})
    client = TestClient(app)

    submitted = client.post("/run", json={"input": {"n": 7}}, headers=AUTH).json()
    assert submitted["status"] == "IN_QUEUE"

    done = wait_for(client, submitted["id"], {"COMPLETED"})
    assert done["output"] == {"artifacts": [7]}
    assert isinstance(done["executionTime"], int)


def test_pod_server_reports_worker_errors_as_failed() -> None:
    app = create_app(api_key=KEY, runner=lambda job_input: {"error": "ValueError: bad scenes"})
    client = TestClient(app)

    job_id = client.post("/run", json={"input": {}}, headers=AUTH).json()["id"]

    failed = wait_for(client, job_id, {"FAILED"})
    assert failed["error"] == "ValueError: bad scenes"


def test_pod_server_reports_crashes_as_failed() -> None:
    def crash(job_input):
        raise RuntimeError("gpu exploded")

    client = TestClient(create_app(api_key=KEY, runner=crash))
    job_id = client.post("/run", json={"input": {}}, headers=AUTH).json()["id"]

    assert wait_for(client, job_id, {"FAILED"})["error"] == "RuntimeError: gpu exploded"


def test_pod_server_requires_the_api_key() -> None:
    client = TestClient(create_app(api_key=KEY, runner=lambda job_input: {}))

    assert client.get("/health").json() == {"ok": True}
    assert client.post("/run", json={"input": {}}).status_code == 401
    assert (
        client.post("/run", json={"input": {}}, headers={"Authorization": "Bearer x"}).status_code
        == 401
    )
    assert client.get("/status/abc").status_code == 401
    assert client.get("/status/abc", headers=AUTH).status_code == 404
    assert client.post("/cancel/abc", headers=AUTH).status_code == 404
    assert client.post("/run", json={"input": "nope"}, headers=AUTH).status_code == 422


def test_pod_server_refuses_an_empty_key() -> None:
    with pytest.raises(ValueError):
        create_app(api_key="  ")


def test_pod_server_cancel_discards_the_result() -> None:
    release = []

    def slow(job_input):
        while not release:
            time.sleep(0.01)
        return {"artifacts": []}

    client = TestClient(create_app(api_key=KEY, runner=slow))
    job_id = client.post("/run", json={"input": {}}, headers=AUTH).json()["id"]
    wait_for(client, job_id, {"IN_PROGRESS"})

    assert client.post(f"/cancel/{job_id}", headers=AUTH).json()["status"] == "CANCELLED"
    release.append(True)
    time.sleep(0.2)
    assert client.get(f"/status/{job_id}", headers=AUTH).json()["status"] == "CANCELLED"
