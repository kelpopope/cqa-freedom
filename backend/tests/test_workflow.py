"""Сценарии защиты end-to-end (модель подменена): пакет → апелляция → решение → пересчёт →
изменение чек-листа → переоценка → «было → стало», фильтр грубых ошибок, аналитика, настройки."""
import copy
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import evaluator

from .test_evaluator import RECORD, llm_answer

CLEAN = {"conversation_id": "p1-ru-001", "channel": "agent chat", "language": "RU", "audio_url": None, "transcript": [
    {"timestamp": "00:00", "speaker": "client", "text": "Где посмотреть мой тариф?"},
    {"timestamp": "00:40", "speaker": "operator", "text": "Здравствуйте! Меня зовут Алина. Откройте «Кабинет»."},
    {"timestamp": "01:00", "speaker": "client", "text": "Нашла, спасибо."},
    {"timestamp": "01:10", "speaker": "operator", "text": "Пожалуйста. Хорошего вечера!"},
]}


def clean_answer():
    ev = [{"index": 1, "quote": "Меня зовут Алина"}]
    base = {"evidence": ev, "expected_range": None, "explanation": "ok", "na_reason": None, "confidence": "high"}
    return {"client_request": "Где тариф", "criteria": {
        "c01": {**base, "answer": "yes"}, "c02": {**base, "answer": "na", "na_reason": "хватало"},
        "c03": {**base, "answer": "na", "na_reason": "общий вопрос"}, "c04": {**base, "answer": "20"},
        "c05": {**base, "answer": "yes"}, "c06": {**base, "answer": "10"},
        "c07": {**base, "answer": "no", "evidence": [{"index": 3, "quote": "Хорошего вечера"}]},
        "c08": {**base, "answer": "no", "evidence": [], "flag_types": []}}}


def router_llm(messages, schema, name="evaluation", reasoning_effort=None, model=None):
    user = messages[-1]["content"]
    payload = llm_answer() if "p1-ru-022" in user else clean_answer()
    crit = schema["properties"]["criteria"]["properties"]
    # новый критерий в чек-листе → модель должна ответить и по нему (схема строгая)
    for cid, sch in crit.items():
        if cid not in payload["criteria"]:
            payload["criteria"][cid] = {"answer": sch["properties"]["answer"]["enum"][0], "evidence": [],
                                        "expected_range": None, "explanation": "new", "na_reason": None,
                                        "confidence": "high"}
    for cid in list(payload["criteria"]):
        if cid not in crit:
            del payload["criteria"][cid]
    return copy.deepcopy(payload), {"input_tokens": 1000, "cached_tokens": 0, "output_tokens": 200,
                                    "reasoning_tokens": 0, "cost_usd": 0.01, "duration_ms": 10}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SELF_CONSISTENCY", "1")
    monkeypatch.setattr("backend.app.routers.conversations.fetch_dataset", lambda g: ([RECORD, CLEAN], "api"))
    monkeypatch.setattr(evaluator, "complete_json", router_llm)
    with TestClient(app) as c:
        c.post("/api/datasets/import", json={"group_id": "CTRL"})
        yield c


def wait_job(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_full_defense_scenario(client):
    # 1. «Оценить все» по group_id
    r = client.post("/api/evaluate/batch", json={"group_id": "CTRL"})
    assert r.status_code == 200 and r.json()["total"] == 2
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done" and job["done"] == 2 and job["failed"] == 0 and job["cost_usd"] == 0.02

    # 2. «Покажите все диалоги с грубыми ошибками»
    gross = client.get("/api/conversations", params={"gross": "true"}).json()
    assert [g["conversation_id"] for g in gross] == ["p1-ru-022"]
    detail = client.get("/api/conversations/p1-ru-022").json()["evaluation"]
    c08 = next(c for c in detail["criteria"] if c["criterion_id"] == "c08")
    assert c08["flag_types"] == ["private_service"] and c08["evidence"][0]["index"] == 3
    assert client.get("/api/conversations", params={"gross": "false"}).json()[0]["conversation_id"] == "p1-ru-001"

    # 3. Апелляция оператора по c07 диалога p1-ru-001
    ev = client.get("/api/conversations/p1-ru-001").json()["evaluation"]
    before = ev["final"]["total"]
    ap = client.post("/api/appeals", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c07",
                                           "comment": "Оператор попрощался в реплике 3."}).json()
    assert ap["status"] == "open" and ap["original"]["answer"] == "no" and ap["original"]["total"] == before
    dup = client.post("/api/appeals", json={"evaluation_id": ev["evaluation_id"], "criterion_id": "c07", "comment": "x"})
    assert dup.status_code == 409
    queue = client.get("/api/appeals", params={"status": "open"}).json()
    assert len(queue) == 1 and queue[0]["context"] and all("index" in t for t in queue[0]["context"])
    listed = {i["conversation_id"]: i for i in client.get("/api/conversations", params={"group_id": "CTRL"}).json()}
    assert listed["p1-ru-001"]["appeal_status"] == "open" and listed["p1-ru-022"]["appeal_status"] is None

    # 4. Решение проверяющего → пересчёт; ИИ-оценка сохраняется
    bad = client.post(f"/api/appeals/{ap['appeal_id']}/decision", json={"action": "change", "new_answer": "maybe",
                                                                       "reason": "..."})
    assert bad.status_code == 422
    assert client.post(f"/api/appeals/{ap['appeal_id']}/decision",
                       json={"action": "change", "new_answer": "yes", "reason": ""}).status_code == 422
    dec = client.post(f"/api/appeals/{ap['appeal_id']}/decision",
                      json={"action": "change", "new_answer": "yes", "reason": "Есть прощание и итог"}).json()
    assert dec["status"] == "resolved" and dec["decision"]["new_total"] == 100.0 and before < 100
    ev2 = client.get("/api/conversations/p1-ru-001").json()
    assert ev2["evaluation"]["total"] == before                      # исходная ИИ-оценка
    assert ev2["evaluation"]["final"]["total"] == 100.0              # финал после решения
    assert ev2["evaluation"]["final"]["overrides"][0] == {**ev2["evaluation"]["final"]["overrides"][0],
                                                          "criterion_id": "c07", "from": "no", "to": "yes"}
    assert [a["status"] for a in ev2["appeals"]] == ["resolved"]
    assert client.post(f"/api/appeals/{ap['appeal_id']}/decision",
                       json={"action": "confirm", "reason": "again"}).status_code == 409
    item = next(i for i in client.get("/api/conversations").json() if i["conversation_id"] == "p1-ru-001")
    assert item["evaluation"]["final_total"] == 100.0 and item["appeal_status"] == "resolved"

    # 5. Апелляция на грубую ошибку: проверяющий снимает флаг → финальный флаг снят
    ev22 = client.get("/api/conversations/p1-ru-022").json()["evaluation"]
    ap2 = client.post("/api/appeals", json={"evaluation_id": ev22["evaluation_id"], "criterion_id": "c08",
                                            "comment": "Шутка"}).json()
    client.post(f"/api/appeals/{ap2['appeal_id']}/decision", json={"action": "change", "new_answer": "no",
                                                                    "reason": "проверено"})
    assert client.get("/api/conversations", params={"gross": "true"}).json() == []

    # 6. Изменение чек-листа: заменить c05 новым критерием, поменять вес c04 → переоценка → «было → стало»
    cl = client.get("/api/checklists/default").json()
    cl["criteria"] = [c for c in cl["criteria"] if c["id"] != "c05"] + [{
        "id": "c09", "section_id": "s3", "title": "Персонализация", "description": "Обратился по имени.",
        "type": "binary", "weight": 15, "na_allowed": False}]
    next(c for c in cl["criteria"] if c["id"] == "c04")["weight"] = 25
    v2 = client.put("/api/checklists/default", json=cl).json()
    assert v2["version"] == 2
    job2 = wait_job(client, client.post("/api/evaluate/batch", json={"group_id": "CTRL"}).json()["job_id"])
    assert job2["checklist_version"] == 2 and job2["done"] == 2
    cmp = client.get("/api/evaluations/compare", params={"from_version": 1, "to_version": 2, "group_id": "CTRL"}).json()
    assert cmp["criteria_added"] == ["c09"] and cmp["criteria_removed"] == ["c05"]
    row = next(i for i in cmp["items"] if i["conversation_id"] == "p1-ru-001")
    assert row["from_total"] == 100.0 and "c09" in row["added"] and "c05" in row["removed"]
    assert row["changed_criteria"] == []           # решение по c07 (критерий не менялся) перенесено в v2
    ev_v2 = client.get("/api/conversations/p1-ru-001").json()["evaluation"]
    carried = ev_v2["final"]["overrides"]
    assert [(o["criterion_id"], o["to"]) for o in carried] == [("c07", "yes")] and carried[0]["carried_from"]
    assert next(c for c in ev_v2["criteria"] if c["criterion_id"] == "c07")["answer"] == "no"   # ИИ-ответ виден
    hist = client.get("/api/conversations/p1-ru-001/evaluations").json()
    assert [h["checklist_version"] for h in hist] == [2, 1]


def test_analytics_and_settings(client):
    wait_job(client, client.post("/api/evaluate/batch", json={"group_id": "CTRL"}).json()["job_id"])
    a = client.get("/api/analytics", params={"group_id": "CTRL"}).json()
    assert a["evaluated"] == 2 and a["gross_errors"][0]["conversation_id"] == "p1-ru-022"
    c07 = next(c for c in a["criteria"] if c["criterion_id"] == "c07")
    assert c07["failed_conversations"] == ["p1-ru-001"] and c07["answers"] == {"no": 1, "na": 1}
    assert c07["fail_rate"] == 1.0                               # N/A не входит в знаменатель
    assert a["by_operator"] and a["by_language"]["RU"] is not None

    s = client.put("/api/settings", json={"self_consistency": 3, "reasoning_effort": "high"}).json()
    assert s["self_consistency"] == 3 and s["reasoning_effort"] == "high"
    assert client.get("/api/health").json()["llm"]["samples"] == 3
    assert client.put("/api/settings", json={"reasoning_effort": "turbo"}).status_code == 422


def test_batch_errors(client):
    assert client.post("/api/evaluate/batch", json={"group_id": "EMPTY"}).status_code == 422
    assert client.post("/api/evaluate/batch", json={"conversation_ids": ["nope"]}).status_code == 422
    assert client.get("/api/jobs/missing").status_code == 404
