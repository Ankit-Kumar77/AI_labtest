from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_yugabyte_logs_live(monkeypatch):
    from app.services import kubectl

    monkeypatch.setattr(
        kubectl,
        "get_pod_logs",
        lambda *a, **k: {"success": True, "stdout": "line1\nline2\nline3"},
    )
    resp = client.get("/api/db-investigation/yugabyte/logs", params={"tail": 50})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["pod"] == "yugabytedb-0"
    assert data["lines"] == ["line1", "line2", "line3"]


def test_aerospike_logs_pod_not_found(monkeypatch):
    from app.services import kubectl

    monkeypatch.setattr(
        kubectl,
        "get_pod_logs",
        lambda *a, **k: {
            "success": False,
            "stderr": 'Error from server (NotFound): pods "aerospike-0" not found',
        },
    )
    resp = client.get("/api/db-investigation/aerospike/logs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "scaled" in data["error"]


def test_aerospike_logs_kubectl_error(monkeypatch):
    from app.services import kubectl

    monkeypatch.setattr(
        kubectl,
        "get_pod_logs",
        lambda *a, **k: {"success": False, "stderr": "boom"},
    )
    resp = client.get("/api/db-investigation/aerospike/logs")
    assert resp.json()["success"] is False
    assert resp.json()["detail"] == "boom"


def test_yugabyte_log_history(monkeypatch):
    from app.services import elasticsearch as es

    monkeypatch.setattr(
        es,
        "search_logs",
        lambda **k: {
            "success": True,
            "available": True,
            "total": 1,
            "hits": [{"_id": "1", "_source": {"log": "hello"}, "@timestamp": "t"}],
        },
    )
    resp = client.get(
        "/api/db-investigation/yugabyte/log-history",
        params={"since_minutes": 60, "limit": 10, "pattern": "hello"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["total"] == 1
    assert len(data["hits"]) == 1


def test_aerospike_log_history_es_down(monkeypatch):
    from app.services import elasticsearch as es

    monkeypatch.setattr(
        es,
        "search_logs",
        lambda **k: {"success": False, "available": False, "error": "nope"},
    )
    resp = client.get("/api/db-investigation/aerospike/log-history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "port-forward" in data["hint"]
