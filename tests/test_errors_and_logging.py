import json
import logging
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from clinic.app import create_app
from clinic.config import Settings
from clinic.errors import ConversationBusy
from clinic.observability import JsonFormatter, request_id_var


@pytest.fixture
def client(make_settings: Callable[..., Settings]) -> TestClient:
    app = create_app(make_settings())

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/busy")
    def busy() -> None:
        raise ConversationBusy()

    return TestClient(app)


def test_unknown_route_has_not_found_body(client: TestClient) -> None:
    response = client.get("/nope")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_wrong_method_has_method_not_allowed_body(client: TestClient) -> None:
    response = client.post("/health")
    assert response.status_code == 405
    assert response.json()["code"] == "method_not_allowed"


def test_unexpected_exception_returns_500_without_traceback(client: TestClient) -> None:
    response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "internal_error"
    assert body["request_id"]
    assert "secret internal detail" not in response.text
    assert "Traceback" not in response.text


def test_domain_error_maps_to_its_status(client: TestClient) -> None:
    response = client.get("/busy")
    assert response.status_code == 409
    assert response.json()["code"] == "conversation_busy"


def test_each_request_logs_one_access_line(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="clinic.access"):
        client.get("/nope", headers={"X-Request-ID": "abc-123"})
    lines = [r for r in caplog.records if r.name == "clinic.access" and r.message == "request"]
    assert len(lines) == 1
    fields = lines[0].fields  # type: ignore[attr-defined]  # attached through `extra`
    assert fields["method"] == "GET"
    assert fields["path"] == "/nope"
    assert fields["status"] == 404
    assert fields["duration_ms"] >= 0


def test_json_formatter_includes_request_id_and_fields() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)
    record.fields = {"status": 200}
    token = request_id_var.set("rid-1")
    try:
        data = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert data["message"] == "hello"
    assert data["request_id"] == "rid-1"
    assert data["status"] == 200
