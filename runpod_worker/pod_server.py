"""HTTP front end that runs the render worker on a RunPod Pod instead of Serverless.

Exposes the same job contract the API expects from RunPod Serverless:
    POST /run            {"input": {...}}  -> {"id", "status": "IN_QUEUE"}
    GET  /status/{id}                      -> {"id", "status", "output"?, "error"?, ...}
    POST /cancel/{id}                      -> {"id", "status"}
    GET  /health                           -> {"ok": true}   (no authentication)

Every route except /health requires `Authorization: Bearer <POD_API_KEY>`.
Jobs run one at a time and are kept in memory, so a Pod restart forgets them.
"""

from __future__ import annotations

import hmac
import os
import queue
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, status

JobRunner = Callable[[dict[str, Any]], dict[str, Any]]


def default_runner(job_input: dict[str, Any]) -> dict[str, Any]:
    # Imported lazily so the server can start (and be tested) without the GPU stack.
    from handler import handler

    return handler({"input": job_input})


class JobStore:
    def __init__(self, runner: JobRunner) -> None:
        self._runner = runner
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._work, daemon=True, name="render-worker").start()

    def submit(self, job_input: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {"id": job_id, "status": "IN_QUEUE", "input": job_input}
        self._queue.put(job_id)
        return {"id": job_id, "status": "IN_QUEUE"}

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return {key: value for key, value in job.items() if key != "input"}

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            # A render already in progress cannot be interrupted; its result is discarded.
            if job["status"] in {"IN_QUEUE", "IN_PROGRESS"}:
                job["status"] = "CANCELLED"
        return self.get(job_id)

    def _work(self) -> None:
        while True:
            job_id = self._queue.get()
            with self._lock:
                job = self._jobs[job_id]
                if job["status"] != "IN_QUEUE":
                    continue
                job["status"] = "IN_PROGRESS"
                job_input = job["input"]
            started = time.monotonic()
            try:
                result = self._runner(job_input)
            except Exception as error:  # reported to the API as a failed job
                result = {"error": f"{type(error).__name__}: {error}"}
            elapsed_ms = int((time.monotonic() - started) * 1000)
            with self._lock:
                if job["status"] == "CANCELLED":
                    continue
                job["executionTime"] = elapsed_ms
                if "error" in result:
                    job["status"] = "FAILED"
                    job["error"] = str(result["error"])
                else:
                    job["status"] = "COMPLETED"
                    job["output"] = result


def create_app(*, api_key: str, runner: JobRunner | None = None) -> FastAPI:
    if not api_key.strip():
        raise ValueError("POD_API_KEY must be set.")
    store = JobStore(runner or default_runner)
    app = FastAPI(title="UVM render pod", docs_url=None, redoc_url=None, openapi_url=None)

    def require_key(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {api_key}"
        if authorization is None or not hmac.compare_digest(
            authorization.encode(), expected.encode()
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API key.")

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/run", dependencies=[Depends(require_key)])
    def run(body: dict[str, Any]) -> dict[str, Any]:
        job_input = body.get("input")
        if not isinstance(job_input, dict):
            raise HTTPException(422, "Body needs an input object.")
        return store.submit(job_input)

    @app.get("/status/{job_id}", dependencies=[Depends(require_key)])
    def job_status(job_id: str) -> dict[str, Any]:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
        return job

    @app.post("/cancel/{job_id}", dependencies=[Depends(require_key)])
    def cancel(job_id: str) -> dict[str, Any]:
        job = store.cancel(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")
        return job

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        create_app(api_key=os.environ.get("POD_API_KEY", "")),
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )
