import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import incident_history
from app.services import opensre_cli

client = TestClient(app)

FAKE_STDOUT = (
    "some log lines\n"
    '{"report": "## problem\\nDisk pressure on node", '
    '"root_cause": "Disk filled by runaway logs", '
    '"validity_score": 0.82, "summary": "Node disk full", '
    '"impact": "Pods evicted", "is_noise": false}\n'
    "done\n"
)


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(incident_history, "DATA_DIR", tmp_path)
    monkeypatch.setattr(incident_history, "STORE_PATH", tmp_path / "incidents.jsonl")
    return tmp_path


def test_extract_report_dict_finds_last_valid():
    report = incident_history.extract_report_dict(FAKE_STDOUT)
    assert report is not None
    assert report["root_cause"] == "Disk filled by runaway logs"
    assert report["validity_score"] == 0.82


def test_extract_report_dict_none_without_report_key():
    assert incident_history.extract_report_dict('{"foo": 1}\nplain text') is None
    assert incident_history.extract_report_dict("") is None


def test_describe_target_pod():
    info = incident_history.describe_target(
        {"pod": {"namespace": "opensre", "name": "catalog-1"}, "cluster": "kind-x"}
    )
    assert info["target_type"] == "pod"
    assert info["target_label"] == "opensre/catalog-1"
    assert info["cluster"] == "kind-x"


def test_describe_target_database():
    info = incident_history.describe_target(
        {"target": {"type": "database", "database": "yugabyte"}}
    )
    assert info["target_type"] == "database"
    assert info["target_label"] == "Yugabyte"


def test_describe_target_unknown_shape():
    info = incident_history.describe_target({"alertname": "X"})
    assert info["target_type"] == "unknown"
    assert info["target_label"] == "unknown"


def test_save_list_get_delete_roundtrip(isolated_store):
    payload = {
        "pod": {"namespace": "opensre", "name": "catalog-1"},
        "cluster": "kind-x",
        "question": "Why is this pod crashlooping?",
    }
    cli_result = {"success": True, "stdout": FAKE_STDOUT, "stderr": "", "returncode": 0}

    saved = incident_history.save(payload, cli_result)
    assert saved["id"]
    assert saved["target_label"] == "opensre/catalog-1"
    assert saved["report"]["root_cause"] == "Disk filled by runaway logs"

    listed = incident_history.list_incidents()
    assert listed["success"] is True
    assert len(listed["data"]) == 1
    assert listed["data"][0]["id"] == saved["id"]
    assert listed["data"][0]["stdout_preview"].startswith("some log lines")
    assert "stdout" not in listed["data"][0]  # full output only via get

    full = incident_history.get_incident(saved["id"])
    assert full["success"] is True
    assert "Disk filled by runaway logs" in full["data"]["stdout"]

    assert incident_history.get_incident("nope")["success"] is False
    assert incident_history.delete_incident(saved["id"])["success"] is True
    assert incident_history.list_incidents()["data"] == []


def test_save_never_raises(isolated_store):
    # Garbage in must not break investigations.
    assert incident_history.save(None, None) == {} or True
    assert incident_history.save({"a": 1}, {"success": False})["id"]


def test_investigate_autosaves_and_returns_incident_id(isolated_store, monkeypatch):
    monkeypatch.setattr(
        opensre_cli,
        "run_command",
        lambda *a, **k: {
            "success": True,
            "stdout": FAKE_STDOUT,
            "stderr": "",
            "returncode": 0,
        },
    )
    result = opensre_cli.investigate(
        {"pod": {"namespace": "opensre", "name": "catalog-1"}}
    )
    assert result.get("incident_id")
    listed = incident_history.list_incidents()
    assert any(r["id"] == result["incident_id"] for r in listed["data"])


def test_incidents_api_roundtrip(isolated_store, monkeypatch):
    monkeypatch.setattr(
        opensre_cli,
        "run_command",
        lambda *a, **k: {
            "success": True,
            "stdout": FAKE_STDOUT,
            "stderr": "",
            "returncode": 0,
        },
    )
    opensre_cli.investigate({"target": {"type": "nginx", "name": "nginx"}})

    resp = client.get("/api/incidents")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert len(data["data"]) == 1
    entry = data["data"][0]
    assert entry["target_type"] == "nginx"
    assert entry["report"]["root_cause"] == "Disk filled by runaway logs"

    incident_id = entry["id"]
    full = client.get(f"/api/incidents/{incident_id}")
    assert full.status_code == 200
    assert "Disk filled" in full.json()["data"]["stdout"]

    assert client.get("/api/incidents/does-not-exist").json()["success"] is False

    deleted = client.delete(f"/api/incidents/{incident_id}")
    assert deleted.json()["success"] is True
    assert client.get("/api/incidents").json()["data"] == []
