from fastapi.testclient import TestClient

from universal_video_maker.api import create_app
from universal_video_maker.config import Settings
from universal_video_maker.errors import ForbiddenError


def test_health_endpoints_report_live_and_ready() -> None:
    with TestClient(create_app()) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


def test_api_adds_request_correlation_id() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health/live")

    assert response.headers["X-Request-ID"]


def test_application_errors_return_safe_structured_response() -> None:
    app = create_app()

    async def forbidden_route() -> None:
        raise ForbiddenError()

    app.add_api_route("/test/forbidden", forbidden_route)
    with TestClient(app) as client:
        response = client.get("/test/forbidden")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_openapi_docs_are_disabled_outside_development(monkeypatch) -> None:
    from universal_video_maker.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("UVM_ENVIRONMENT", "production")
    monkeypatch.setenv("UVM_LOG_LEVEL", "ERROR")
    app = create_app()
    get_settings.cache_clear()

    assert app.docs_url is None
    assert app.openapi_url is None


def test_settings_support_new_and_legacy_environment_prefixes(monkeypatch) -> None:
    monkeypatch.setenv("UVM_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("SBE_LOG_LEVEL", "WARNING")
    assert Settings(_env_file=None).log_level == "ERROR"

    monkeypatch.delenv("UVM_LOG_LEVEL")
    assert Settings(_env_file=None).log_level == "WARNING"
