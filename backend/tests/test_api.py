import copy

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import fetcher


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


RECORDS = [
    {"conversation_id": "p1-ru-002", "channel": "agent chat", "language": "RU", "audio_url": None,
     "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Тариф"},
                    {"timestamp": "00:40", "speaker": "operator", "text": "Здравствуйте, я Тимур."}]},
    {"conversation_id": "p1-ru-010", "channel": "agent chat", "language": "RU", "audio_url": None,
     "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Портфель"}]},
    {"conversation_id": "p1-en-001", "channel": "agent chat", "language": "EN", "audio_url": None,
     "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Login"}]},
]


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True and body["llm"]["provider"] == "openai"


def test_import_list_get(client, monkeypatch):
    monkeypatch.setattr("backend.app.routers.conversations.fetch_dataset", lambda g: (RECORDS, "api"))
    r = client.post("/api/datasets/import", json={"group_id": "DEV"})
    assert r.json() == {"group_id": "DEV", "imported": 3, "source": "api"}

    items = client.get("/api/conversations", params={"group_id": "DEV"}).json()
    assert [i["conversation_id"] for i in items] == ["p1-en-001", "p1-ru-002", "p1-ru-010"]  # натуральная сортировка
    assert items[1]["operator_name"] == "Тимур" and items[1]["turns"] == 2 and items[1]["evaluation"] is None
    assert len(client.get("/api/conversations", params={"language": "en"}).json()) == 1
    assert [i["conversation_id"] for i in client.get("/api/conversations", params={"q": "тимур"}).json()] == ["p1-ru-002"]
    assert client.get("/api/conversations", params={"risk": "true"}).json() == []
    assert len(client.get("/api/conversations", params={"status": "not_evaluated"}).json()) == 3

    detail = client.get("/api/conversations/p1-ru-002").json()
    assert detail["conversation"]["transcript"][1]["offset_sec"] == 40
    assert detail["evaluation"] is None and detail["appeals"] == []
    assert client.get("/api/conversations/nope").status_code == 404

    # повторный импорт не дублирует
    client.post("/api/datasets/import", json={"group_id": "DEV"})
    assert len(client.get("/api/conversations").json()) == 3
    assert client.get("/api/datasets").json() == [{"group_id": "DEV", "count": 3}]


def test_import_error_is_contract_shaped(client, monkeypatch):
    def boom(g):
        raise fetcher.FetchError("ORG_API_URL не задан в .env")
    monkeypatch.setattr("backend.app.routers.conversations.fetch_dataset", boom)
    r = client.post("/api/datasets/import", json={"group_id": "X"})
    assert r.status_code == 502 and r.json() == {"error": "ORG_API_URL не задан в .env"}


def test_upload(client):
    r = client.post("/api/conversations/upload", json={
        "channel": "agent chat", "language": "RU",
        "transcript": [{"timestamp": "00:00", "speaker": "client", "text": "Привет"}]})
    conv = r.json()
    assert conv["group_id"] == "UPLOAD" and conv["conversation_id"].startswith("upload-")
    assert client.post("/api/conversations/upload", json={"transcript": []}).status_code == 422


def test_checklist_versions_and_activation(client):
    default = client.get("/api/checklists/default").json()
    assert default["version"] == 1 and default["is_active"] is True and len(default["criteria"]) == 8

    # «Замена критерия» на защите: c05 -> c09
    changed = copy.deepcopy(default)
    changed["criteria"] = [c for c in changed["criteria"] if c["id"] != "c05"] + [{
        "id": "c09", "section_id": "s3", "title": "Персонализация", "description": "Да — обратился по имени.",
        "type": "binary", "weight": 15, "na_allowed": False}]
    v2 = client.put("/api/checklists/default", json=changed).json()
    assert v2["version"] == 2 and v2["is_active"] is True
    assert client.get("/api/checklists/default").json()["version"] == 2           # активная
    assert client.get("/api/checklists/default", params={"version": 1}).json()["is_active"] is False
    assert [c["checklist_id"] for c in client.get("/api/checklists").json()] == ["default"]
    assert [v["version"] for v in client.get("/api/checklists/default/versions").json()] == [1, 2]

    back = client.post("/api/checklists/default/activate", json={"version": 1}).json()
    assert back["version"] == 1 and back["is_active"] is True
    assert client.get("/api/checklists/default").json()["version"] == 1
    assert client.post("/api/checklists/default/activate", json={"version": 9}).status_code == 404


def test_checklist_validation_errors(client):
    bad = {"name": "X", "sections": [{"id": "s1", "title": "S"}], "criteria": [
        {"id": "a", "section_id": "s1", "title": "A", "type": "binary", "weight": 10, "na_allowed": True},
        {"id": "a", "section_id": "s9", "title": "", "type": "scale", "weight": 10, "scale": [{"value": 5}]},
    ]}
    r = client.post("/api/checklists", json=bad)
    assert r.status_code == 422
    msg = r.json()["error"]
    for part in ["условие N/A", "повторяется", "s9", "нет названия", "минимум 2 ступени"]:
        assert part in msg


def test_checklist_create_and_conflict(client):
    body = {"checklist_id": "short", "name": "Короткий", "criteria": [
        {"id": "x", "title": "X", "type": "scale", "weight": 100, "scale": [{"value": 0}, {"value": 2}, {"value": 1}]}]}
    created = client.post("/api/checklists", json=body).json()
    assert created["version"] == 1 and [s["value"] for s in created["criteria"][0]["scale"]] == [2, 1, 0]
    assert client.post("/api/checklists", json=body).status_code == 422
    assert client.put("/api/checklists/missing", json=body).status_code == 404
