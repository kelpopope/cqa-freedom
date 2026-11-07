"""Официальный формат results.json (CQA_Student_Guide_Appendix_Results, JSON 1.0)."""
import copy

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import evaluator, results_export

from .test_workflow import CLEAN, RECORD, router_llm, wait_job


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SELF_CONSISTENCY", "1")
    missing = {**copy.deepcopy(CLEAN), "conversation_id": "p1-ru-099"}
    monkeypatch.setattr("backend.app.routers.conversations.fetch_dataset", lambda g: ([RECORD, CLEAN, missing], "api"))
    monkeypatch.setattr(evaluator, "complete_json", router_llm)
    with TestClient(app) as c:
        c.post("/api/datasets/import", json={"group_id": "CTRL"})
        wait_job(c, c.post("/api/evaluate/batch", json={"conversation_ids": ["p1-ru-022", "p1-ru-001"]}).json()["job_id"])
        yield c


def test_export_format(client):
    r = client.get("/api/results/export", params={"group_id": "CTRL"})
    doc = r.json()
    assert doc["schema_version"] == "1.0" and doc["model"] == {"mode": "external_api", "name": "gpt-6.1-sol"}
    res = {x["conversation_id"]: x for x in doc["results"]}
    assert set(res) == {"p1-ru-001", "p1-ru-022", "p1-ru-099"}

    ok = res["p1-ru-022"]
    crit = {c["criterion_id"]: c for c in ok["criteria"]}
    assert list(crit) == ["contact", "discovery", "identification", "consultation", "empathy", "dialogue",
                          "closure", "critical"]
    assert crit["contact"]["value"] is True and crit["discovery"]["value"] is False
    assert crit["identification"]["value"] is None and crit["closure"]["value"] is None      # N/A → null
    assert crit["consultation"]["value"] == 0 and crit["dialogue"]["value"] == 7              # ступени, не баллы
    assert crit["critical"]["value"] is True and ok["score"]["critical_flag"] is True
    assert crit["critical"]["evidence_turns"] == [4]          # реплика с индексом 3 → номер 4 (нумерация с 1)
    assert crit["contact"]["evidence_turns"] == [4]
    assert ok["score"]["final_score"] == 27.33 and all(c["rationale"] for c in ok["criteria"])

    err = res["p1-ru-099"]                                    # не оценён → error, не ноль
    assert err == {"conversation_id": "p1-ru-099", "status": "error", "criteria": [], "score": None,
                   "error": "Диалог не оценён"}
    assert r.headers["X-Results-Valid"] == "true"


def test_validator_catches_mistakes(client):
    doc = client.get("/api/results/export", params={"group_id": "CTRL"}).json()
    assert results_export.validate(doc, ["p1-ru-001", "p1-ru-022", "p1-ru-099"], 3) == []
    bad = copy.deepcopy(doc)
    entry = next(x for x in bad["results"] if x["conversation_id"] == "p1-ru-022")
    entry["criteria"][0]["value"] = None                      # contact не допускает null
    entry["criteria"][3]["value"] = 12                        # нет такой ступени
    entry["criteria"][5]["evidence_turns"] = [0]              # нумерация с 1
    entry["score"]["critical_flag"] = False                   # не совпадает с critical
    entry["criteria"].pop()                                   # нет одного критерия
    bad["results"].append(copy.deepcopy(bad["results"][0]))   # дубль ID
    problems = " | ".join(results_export.validate(bad, None, 3))
    for part in ["contact: null", "ступень 12", "evidence_turns", "critical_flag", "все 8 критериев", "повторяющиеся", "записей 4"]:
        assert part in problems, part


def test_export_uses_ai_result_not_appeal_decisions(client):
    ev = client.get("/api/conversations/p1-ru-001").json()["evaluation"]
    ap = client.post("/api/appeals", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c07", "comment": "x"}).json()
    client.post(f"/api/appeals/{ap['appeal_id']}/decision", json={"action": "change", "new_answer": "yes", "reason": "y"})
    doc = client.get("/api/results/export", params={"group_id": "CTRL"}).json()
    e = next(x for x in doc["results"] if x["conversation_id"] == "p1-ru-001")
    assert next(c for c in e["criteria"] if c["criterion_id"] == "closure")["value"] is False


def test_rationale_turn_refs_are_one_based():
    f = results_export.to_one_based_refs
    assert f("Оператор предложил [#9] и подтвердил [#11].") == "Оператор предложил [#10] и подтвердил [#12]."
    assert f("см. [#9, #11] и [#12–14]") == "см. [#10, #12] и [#13–15]"
    assert f("Ступень [10 из 20] без ссылок") == "Ступень [10 из 20] без ссылок"
