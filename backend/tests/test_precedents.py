"""Обучение на апелляциях: решения проверяющего попадают в промпт как прецеденты; база знаний."""
import copy

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import evaluator, precedents, prompts

from .test_workflow import CLEAN, RECORD, router_llm, wait_job

OTHER = {**copy.deepcopy(CLEAN), "conversation_id": "p1-ru-011"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SELF_CONSISTENCY", "1")
    monkeypatch.setattr("backend.app.routers.conversations.fetch_dataset", lambda g: ([RECORD, CLEAN, OTHER], "api"))
    seen = []

    def spy(messages, schema, **kw):
        seen.append(messages[0]["content"])
        return router_llm(messages, schema, **kw)
    monkeypatch.setattr(evaluator, "complete_json", spy)
    with TestClient(app) as c:
        c.post("/api/datasets/import", json={"group_id": "CTRL"})
        wait_job(c, c.post("/api/evaluate/batch", json={"group_id": "CTRL"}).json()["job_id"])
        c.seen = seen
        yield c


def appeal(client, cid, crit, new_answer, reason):
    ev = client.get(f"/api/conversations/{cid}").json()["evaluation"]
    ap = client.post("/api/appeals", json={"evaluation_id": ev["evaluation_id"], "criterion_id": crit,
                                           "comment": "не согласен"}).json()
    return client.post(f"/api/appeals/{ap['appeal_id']}/decision",
                       json={"action": "change", "new_answer": new_answer, "reason": reason}).json()


def test_reviewer_decision_becomes_precedent_for_other_dialogs(client):
    ap = appeal(client, "p1-ru-001", "c07", "yes", "Клиент подтвердил решение, прощание вежливое — завершение засчитывается")
    client.seen.clear()
    ev = client.post("/api/evaluate", json={"conversation_id": "p1-ru-011"}).json()
    assert ev["model"]["precedents"] == [ap["appeal_id"]]
    system = client.seen[-1]
    assert "Прецеденты" in system and "проверяющий исправил на «yes»" in system
    assert "прощание вежливое" in system and "Хорошего вечера" in system        # фрагмент диалога
    # в собственную переоценку прецедент не попадает (нет утечки ответа)
    client.seen.clear()
    ev_same = client.post("/api/evaluate", json={"conversation_id": "p1-ru-001"}).json()
    assert ev_same["model"]["precedents"] == [] and "Прецеденты" not in client.seen[-1]


def test_precedent_dropped_when_criterion_changes(client):
    appeal(client, "p1-ru-001", "c07", "yes", "ok")
    cl = client.get("/api/checklists/default").json()
    next(c for c in cl["criteria"] if c["id"] == "c07")["description"] = "Новая формулировка завершения"
    client.put("/api/checklists/default", json=cl)
    ev = client.post("/api/evaluate", json={"conversation_id": "p1-ru-011"}).json()
    assert ev["model"]["precedents"] == []


def test_precedents_can_be_switched_off(client):
    appeal(client, "p1-ru-001", "c07", "yes", "ok")
    client.put("/api/settings", json={"use_precedents": False})
    ev = client.post("/api/evaluate", json={"conversation_id": "p1-ru-011"}).json()
    assert ev["model"]["precedents"] == []


def test_knowledge_base_goes_to_prompt_and_survives_ui_edit(client, default_checklist):
    cl = client.get("/api/checklists/default").json()
    cl["knowledge_base"] = ["Реквизиты: «Меню → Пополнить → Перевод по реквизитам».", ""]
    v2 = client.put("/api/checklists/default", json=cl).json()
    assert v2["knowledge_base"] == "- Реквизиты: «Меню → Пополнить → Перевод по реквизитам»."
    client.seen.clear()
    ev = client.post("/api/evaluate", json={"conversation_id": "p1-ru-011"}).json()
    assert ev["model"]["knowledge_base"] is True and "Справочные факты компании" in client.seen[-1]
    edited = {k: v for k, v in client.get("/api/checklists/default").json().items() if k != "knowledge_base"}
    assert client.put("/api/checklists/default", json=edited).json()["knowledge_base"].startswith("- Реквизиты")
    assert "Справочные факты" not in prompts.build_system_prompt(default_checklist | {"knowledge_base": None})


def test_precedent_format_is_compact():
    block = precedents.format_block([{"action": "confirm", "ai_answer": "no", "human_answer": "no", "reason": "верно",
                                      "fragment": [{"index": 3, "speaker": "operator", "text": "x" * 500}]}])
    assert "подтвердил" in block and len(block) < 400
