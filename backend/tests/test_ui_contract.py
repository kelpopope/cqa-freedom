"""Поля и эндпоинты, которые использует интерфейс (docs/API_CONTRACT.md, дополнения участника B)."""
import copy

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import evaluator

from .test_workflow import CLEAN, RECORD, router_llm, wait_job


def llm_with_headline(messages, schema, **kw):
    data, usage = router_llm(messages, schema, **kw)
    data["headline"] = "Предложение перевести деньги лично оператору"
    data["summary"] = "Клиент спрашивал про облигации, оператор предложил частное инвестирование."
    return data, usage


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SELF_CONSISTENCY", "1")
    monkeypatch.setattr("backend.app.routers.conversations.fetch_dataset", lambda g: ([RECORD, CLEAN], "api"))
    monkeypatch.setattr(evaluator, "complete_json", llm_with_headline)
    with TestClient(app) as c:
        c.post("/api/datasets/import", json={"group_id": "CTRL"})
        wait_job(c, c.post("/api/evaluate/batch", json={"group_id": "CTRL"}).json()["job_id"])
        yield c


def ev_of(client, cid):
    return client.get(f"/api/conversations/{cid}").json()["evaluation"]


def test_evaluation_fields_for_ui(client):
    ev = ev_of(client, "p1-ru-022")
    assert ev["headline"].startswith("Предложение") and ev["summary"]
    assert ev["applicable_weight"] == 75 and ev["review"] is None and ev["reproducibility"] is None
    c08 = next(c for c in ev["criteria"] if c["criterion_id"] == "c08")
    assert c08["inline_note"] and c08["uncertainty_reason"] is None
    full = next(c for c in ev["final"]["criteria"] if c["criterion_id"] == "c08")
    assert full["explanation"] and full["evidence"]                      # final.criteria — полные строки
    item = next(i for i in client.get("/api/conversations").json() if i["conversation_id"] == "p1-ru-022")
    assert item["evaluation"]["headline"] == ev["headline"] and item["evaluation"]["gross_types"] == ["private_service"]
    assert item["duration_sec"] == 130 and item["evaluation"]["created_at"]
    detail = client.get("/api/conversations/p1-ru-022").json()
    assert detail["history"][0]["evaluation_id"] == ev["evaluation_id"]


def test_review_confirm_and_unconfirm(client):
    ev = ev_of(client, "p1-ru-001")
    r = client.post(f"/api/evaluations/{ev['evaluation_id']}/review", json={"action": "confirm", "reviewer": "Елена"}).json()
    assert r["review"]["status"] == "confirmed" and r["review"]["by"] == "Елена"
    item = next(i for i in client.get("/api/conversations").json() if i["conversation_id"] == "p1-ru-001")
    assert item["evaluation"]["review"]["status"] == "confirmed"
    assert client.get("/api/stats").json()["reviewed"] == 1
    r = client.post(f"/api/evaluations/{ev['evaluation_id']}/review", json={"action": "unconfirm"}).json()
    assert r["review"] is None
    assert client.post("/api/evaluations/nope/review", json={"action": "confirm"}).status_code == 404


def test_reviewer_override_and_appeal_view(client):
    ev = ev_of(client, "p1-ru-001")
    ap = client.post("/api/appeals/override", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c07",
                                                     "new_answer": "yes", "reason": "Прощание есть", "reviewer": "Елена"}).json()
    assert ap["author_role"] == "reviewer" and ap["status"] == "resolved"
    assert ap["decision"]["total_before"] < ap["decision"]["new_total"] == 100.0 and ap["decision"]["reviewer"] == "Елена"
    assert ap["conversation"]["operator_name"] == "Алина" and ap["criterion_result"]["answer"] == "yes"
    assert ap["current_total"] == 100.0 and ap["context"]
    final = ev_of(client, "p1-ru-001")["final"]
    assert final["overrides"][0]["by"] == "reviewer" and final["total"] == 100.0
    item = next(i for i in client.get("/api/conversations").json() if i["conversation_id"] == "p1-ru-001")
    assert item["appeal_status"] is None            # правка проверяющего — не апелляция оператора
    bad = client.post("/api/appeals/override", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c01",
                                                      "new_answer": "maybe", "reason": "x"})
    assert bad.status_code == 422 and client.get("/api/appeals", params={"status": "open"}).json() == []


def test_operator_appeal_has_author_and_proposal(client):
    ev = ev_of(client, "p1-ru-022")
    ap = client.post("/api/appeals", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c05",
                                           "comment": "Я был вежлив", "proposed_answer": "yes"}).json()
    assert ap["author_name"] == "Кирилл" and ap["proposed_answer"] == "yes" and ap["original"]["raw_points"] == 0
    queue = client.get("/api/appeals", params={"status": "open"}).json()
    assert queue[0]["criterion_result"]["criterion_id"] == "c05" and queue[0]["checklist_version"] == 1
    d = client.post(f"/api/appeals/{ap['appeal_id']}/decision", json={"action": "confirm", "reason": "Нет реакции"}).json()
    assert d["decision"]["new_answer"] == "no" and d["decision"]["total_before"] == d["decision"]["new_total"]


def test_repeat_evaluation_reports_reproducibility(client):
    ev = ev_of(client, "p1-ru-022")
    r = client.post("/api/evaluate/repeat", json={"evaluation_id": ev["evaluation_id"]}).json()
    assert r["evaluation_id"] == ev["evaluation_id"]                       # новая оценка не создаётся
    assert r["reproducibility"]["agreement"] == 1.0 and r["reproducibility"]["runs"] == 2
    assert len(client.get("/api/conversations/p1-ru-022/evaluations").json()) == 1


def test_stats_overview(client):
    s = client.get("/api/stats", params={"group_id": "CTRL"}).json()
    assert s["total"] == 2 and s["evaluated"] == 2 and s["gross"] == 1
    assert s["risky"][0]["conversation_id"] == "p1-ru-022" and s["risky"][0]["headline"]
    assert sum(s["buckets"].values()) == 2 and s["by_language"][0]["language"] == "RU"
    c07 = next(c for c in s["criteria"] if c["criterion_id"] == "c07")
    assert c07["failed"] == 1 and c07["na"] == 1 and c07["lost_points"] == 5
    assert all("lost_points" in c for c in s["criteria"]) and "c08" not in {c["criterion_id"] for c in s["criteria"]}
