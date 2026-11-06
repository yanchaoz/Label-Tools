from http.client import HTTPConnection
import json
import threading

import pytest

from label_tools.server import LocalServer


@pytest.fixture
def server(dataset):
    with LocalServer(dataset, port=0) as service:
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        try:
            yield service
        finally:
            service.shutdown()
            thread.join(timeout=5)


def request(server, method, path, data=None, headers=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    body = None if data is None else json.dumps(data)
    fields = {"Content-Type": "application/json", "X-Annotation-Token": server.token}
    fields.update(headers or {})
    connection.request(method, path, body, fields)
    response = connection.getresponse()
    result = response.status, response.read(), dict(response.getheaders())
    connection.close()
    return result


def test_summary_and_static_assets(server):
    status, body, headers = request(server, "GET", "/api/dataset")
    assert status == 200 and len(json.loads(body)["records"]) == 3
    assert headers["X-Frame-Options"] == "DENY"
    assert "Access-Control-Allow-Origin" not in headers
    for path in [
        "/",
        "/app.js",
        "/i18n.js",
        "/style.css",
        "/vendor/lucide.min.js",
        "/images/IMG0001.png",
    ]:
        status, body, _ = request(server, "GET", path)
        assert status == 200 and len(body) > 20


def test_http_save_conflict_export(server, document):
    assert request(server, "POST", "/api/case/IMG0001", document)[0] == 200
    assert request(server, "POST", "/api/case/IMG0001", document)[0] == 409
    status, body, _ = request(server, "POST", "/api/export", {})
    assert status == 200
    assert request(server, "GET", json.loads(body)["download"])[0] == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "example.com"},
        {"Origin": "https://example.com"},
        {"X-Annotation-Token": "wrong"},
    ],
)
def test_cross_origin_and_bad_session_rejected(server, document, headers):
    assert request(server, "POST", "/api/case/IMG0001", document, headers)[0] == 403


def test_rebinding_and_traversal_rejected(server):
    assert request(server, "GET", "/api/dataset", headers={"Host": "example.com"})[0] == 403
    for path in [
        "/../manifest.json",
        "/images/../manifest.json",
        "/api/case/../secret",
        "/config.json",
    ]:
        assert request(server, "GET", path)[0] == 404


@pytest.mark.parametrize("payload", [None, [], 42, "bad"])
def test_malformed_payload_returns_json_error(server, payload):
    status, body, _ = request(server, "POST", "/api/case/IMG0001", payload)
    assert status in (400, 409)
    assert "error" in json.loads(body)


def test_content_type_size_and_transfer_encoding(server):
    assert request(server, "POST", "/api/export", {}, {"Content-Type": "text/plain"})[0] == 415
    assert request(server, "POST", "/api/export", headers={"Content-Length": "4000001"})[0] == 413
    assert (
        request(server, "POST", "/api/export", headers={"Transfer-Encoding": "chunked"})[0] == 400
    )
