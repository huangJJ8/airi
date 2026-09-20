from unittest.mock import patch

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from airi.core.exceptions import DuplicateRegistrationError, ToolOutputError
from airi.infrastructure.database import get_session
from airi.main import create_app


def test_health_and_openapi(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert len(response.headers["X-Request-ID"]) == 32
    schema = client.get("/openapi.json").json()
    assert schema["components"]["schemas"]["MetricIR"]["additionalProperties"] is False
    assert "ErrorResponse" in schema["components"]["schemas"]


def test_validate_metric(client, metric_payload):
    response = client.post("/api/v1/metric-ir/validate", json=metric_payload)
    assert response.status_code == 200
    assert response.json()["name"] == metric_payload["name"]


def test_uniform_validation_error_without_payload_leak(client, metric_payload):
    metric_payload["private"] = "do-not-log-or-return"
    response = client.post("/api/v1/metric-ir/validate", json=metric_payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "do-not-log-or-return" not in response.text


def test_http_errors(client):
    for response, status in [(client.get("/missing"), 404), (client.post("/health"), 405)]:
        assert response.status_code == status
        assert response.json()["error"]["code"] == "http_error"


def test_domain_and_unexpected_errors(settings):
    app = create_app(settings)

    @app.get("/duplicate")
    def duplicate():
        raise DuplicateRegistrationError("Already registered")

    @app.get("/broken")
    def broken():
        raise RuntimeError("secret-database-password")

    @app.get("/bad-output")
    def bad_output():
        raise ToolOutputError("secret-tool-result")

    with TestClient(app) as client:
        assert client.get("/duplicate").status_code == 409
        for path in ("/broken", "/bad-output"):
            response = client.get(path)
            assert response.status_code == 500
            assert "secret" not in response.text
            assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_startup_never_connects_and_shutdown_disposes(settings):
    app = create_app(settings)
    with (
        patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("No connection")),
        patch("airi.infrastructure.database.Database.dispose") as dispose,
    ):
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            dispose.assert_not_called()
        dispose.assert_called_once()
    assert create_app(settings).state.tools is not app.state.tools


def test_session_dependency(settings):
    app = create_app(settings)

    @app.get("/db-check")
    def check(session: Session = Depends(get_session)):
        return {"value": session.scalar(text("SELECT 1"))}

    with TestClient(app) as client:
        assert client.get("/db-check").json() == {"value": 1}
