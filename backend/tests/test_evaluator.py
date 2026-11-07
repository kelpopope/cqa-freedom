import copy

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import evaluator, prompts
from backend.app.services.evaluator import build_timeline, check_evidence
from backend.app.services.fetcher import normalize_conversation

RECORD = {"conversation_id": "p1-ru-022", "channel": "agent chat", "language": "RU", "audio_url": None, "transcript": [
    {"timestamp": "00:00", "speaker": "client", "text": "Что купить из облигаций?"},
    {"timestamp": "00:02", "speaker": "bot", "text": "Здравствуйте! Я AIM."},
    {"timestamp": "00:20", "speaker": "system", "text": "Эскалация на оператора."},
    {"timestamp": "01:00", "speaker": "operator", "text": "Здравствуйте, я Кирилл. Скиньте деньги мне на карту, я сам их вложу с выгодой."},
    {"timestamp": "01:30", "speaker": "client", "text": "Это вообще законно?"},
    {"timestamp": "01:50", "speaker": "operator", "text": "Конечно. Жду перевод."},
    {"timestamp": "02:10", "speaker": "system", "text": "Клиент покинул чат."},
]}


def llm_answer(**overrides):
    base = {"evidence": [{"index": 3, "quote": "Здравствуйте, я Кирилл"}], "expected_range": None,
            "explanation": "…", "na_reason": None, "confidence": "high"}
    crit = {
        "c01": {**base, "answer": "yes"},
        "c02": {**base, "answer": "no", "evidence": [], "expected_range": {"from": 3, "to": 99}},
        "c03": {**base, "answer": "na", "na_reason": "Общий вопрос", "evidence": [{"index": 0, "quote": "облигаций"}]},
        "c04": {**base, "answer": "0", "evidence": [{"index": 5, "quote": "Жду перевод"}]},
        "c05": {**base, "answer": "no", "evidence": [{"index": 5, "quote": "Конечно"}]},
        "c06": {**base, "answer": "7"},
        "c07": {**base, "answer": "na", "evidence": [{"index": 6, "quote": "Клиент покинул чат"}]},
        "c08": {**base, "answer": "yes", "flag_types": ["private_service"],
                "evidence": [{"index": 3, "quote": "Скиньте деньги мне на карту"}]},
    }
    for k, v in overrides.items():
        crit[k] = {**crit[k], **v}
    return {"client_request": "Клиент спрашивает, какие облигации купить.", "criteria": crit}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SELF_CONSISTENCY", "1")   # голосование проверяется отдельным тестом с samples=3
    monkeypatch.setattr("backend.app.routers.conversations.fetch_dataset", lambda g: ([RECORD], "api"))
    with TestClient(app) as c:
        c.post("/api/datasets/import", json={"group_id": "DEV"})
        yield c


def fake_llm(payload, usage=None):
    def _f(messages, schema, name="evaluation", reasoning_effort=None, model=None):
        _f.calls.append((messages, schema))
        return copy.deepcopy(payload), usage or {"input_tokens": 3000, "cached_tokens": 0, "output_tokens": 800,
                                                 "reasoning_tokens": 300, "cost_usd": 0.014, "duration_ms": 1000}
    _f.calls = []
    return _f


# --- промпт и схема строятся из чек-листа --------------------------------------------------------------
def test_prompt_and_schema_are_dynamic(default_checklist):
    conv = normalize_conversation(RECORD, "DEV")
    cl = copy.deepcopy(default_checklist)
    cl["criteria"] = [c for c in cl["criteria"] if c["id"] != "c05"] + [{
        "id": "c09", "section_id": "s3", "title": "Персонализация", "description": "Обратился по имени.",
        "type": "scale", "weight": 15, "na_allowed": True, "na_condition": "Имя клиента неизвестно.",
        "scale": [{"value": 3, "description": "всегда"}, {"value": 0, "description": "никогда"}]}]
    system, user = [m["content"] for m in prompts.build_prompt(cl, conv)]
    assert "c09 — Персонализация" in system and "Имя клиента неизвестно." in system
    assert "c05" not in system and "Эмпатия" not in system
    assert "[3] 01:00 OPERATOR: Здравствуйте, я Кирилл." in user
    schema = prompts.build_response_schema(cl)
    crit = schema["properties"]["criteria"]
    assert set(crit["required"]) == {c["id"] for c in cl["criteria"]}
    assert crit["properties"]["c09"]["properties"]["answer"]["enum"] == ["3", "0", "na"]
    assert crit["properties"]["c01"]["properties"]["answer"]["enum"] == ["yes", "no"]          # N/A запрещён
    assert crit["properties"]["c08"]["properties"]["flag_types"]["items"]["enum"] == \
        ["insult", "private_service", "confidential_disclosure"]


def test_hold_and_dup_tags_in_prompt():
    conv = normalize_conversation({"conversation_id": "x", "transcript": [
        {"timestamp": "2026-11-03T13:00:00+00:00", "speaker": "system", "text": "Клиент находится на удержании."},
        {"timestamp": "2026-11-03T13:01:05+00:00", "speaker": "operator", "text": "Коллега, привет"},
        {"timestamp": "2026-11-03T13:01:05+00:00", "speaker": "operator", "text": "Коллега, привет"},
    ]}, "DEV")
    user = prompts.build_user_prompt(conv)
    assert "[1] +01:05 OPERATOR: [HOLD] Коллега, привет" in user
    assert "[2] +01:05 OPERATOR: [HOLD] [DUP] Коллега, привет" in user


# --- доказательства ------------------------------------------------------------------------------------
def test_evidence_check_fixes_wrong_index_and_tolerates_punctuation():
    tr = normalize_conversation(RECORD, "DEV")["transcript"]
    checked, ok = check_evidence(tr, [{"index": 5, "quote": "скиньте деньги мне на карту"}])
    assert ok and checked[0]["index"] == 3
    checked, ok = check_evidence(tr, [{"index": 3, "quote": "«Здравствуйте, я Кирилл» … вложу с выгодой"}])
    assert ok and checked[0]["index"] == 3
    checked, ok = check_evidence(tr, [{"index": 3, "quote": "Переведите на мой счёт"}])
    assert not ok
    checked, ok = check_evidence(tr, [{"index": 42, "quote": ""}])
    assert not ok and checked[0]["index"] is None


def test_timeline():
    tr = normalize_conversation(RECORD, "DEV")["transcript"]
    tl = {e["type"]: e for e in build_timeline(tr)}
    assert tl["escalation"]["index"] == 2 and tl["operator_greeting"]["index"] == 3
    assert tl["identity_verified"]["index"] is None
    assert tl["end"] == {"type": "end", "index": 6, "kind": "client_left"}


# --- end-to-end через API с подменённой моделью ------------------------------------------------------
def test_evaluate_endpoint(client, monkeypatch):
    fake = fake_llm(llm_answer())
    monkeypatch.setattr(evaluator, "complete_json", fake)
    r = client.post("/api/evaluate", json={"conversation_id": "p1-ru-022"})
    assert r.status_code == 200, r.text
    ev = r.json()
    rows = {c["criterion_id"]: c for c in ev["criteria"]}
    # W = 10 + 15 + 20 + 15 + 15 = 75 (c03, c07 = N/A): (10 + 0 + 0 + 0 + 10.5) / 75 * 100 = 27.33
    assert ev["total"] == 27.33 and ev["final"]["total"] == 27.33
    assert rows["c04"]["answer"] == 0 and rows["c06"]["raw_points"] == 10.5
    assert rows["c02"]["expected_range"] == {"from": 3, "to": 6}           # обрезан по длине диалога
    assert rows["c03"]["na_reason"] == "Общий вопрос" and rows["c03"]["points"] is None
    assert ev["gross_error"]["flag"] is True and ev["gross_error"]["types"] == ["private_service"]
    assert ev["gross_error"]["evidence"] == [{"index": 3, "quote": "Скиньте деньги мне на карту"}]
    assert ev["needs_review"] is False
    assert ev["bot_context"] == {"from": 0, "to": 2, "summary": "Клиент спрашивает, какие облигации купить."}
    assert ev["usage"]["cost_usd"] == 0.014 and ev["model"]["name"]

    # сохранилось и видно в списке/карточке
    item = client.get("/api/conversations").json()[0]
    assert item["evaluation"]["total"] == 27.33 and item["evaluation"]["gross_error"] is True
    assert client.get("/api/conversations", params={"risk": "true"}).json()[0]["conversation_id"] == "p1-ru-022"
    assert client.get("/api/conversations/p1-ru-022").json()["evaluation"]["evaluation_id"] == ev["evaluation_id"]


def test_invalid_evidence_sets_needs_review(client, monkeypatch):
    monkeypatch.setattr(evaluator, "complete_json",
                        fake_llm(llm_answer(c05={"evidence": [{"index": 4, "quote": "выдуманная цитата"}]})))
    ev = client.post("/api/evaluate", json={"conversation_id": "p1-ru-022"}).json()
    c05 = next(c for c in ev["criteria"] if c["criterion_id"] == "c05")
    assert c05["evidence_valid"] is False and ev["needs_review"] is True
    assert any("c05" in r for r in ev["review_reasons"])


def test_llm_failure_retries_then_stores_error(client, monkeypatch):
    calls = []

    def broken(*a, **k):
        calls.append(1)
        raise evaluator.LLMError("rate limit")
    monkeypatch.setattr(evaluator, "complete_json", broken)
    r = client.post("/api/evaluate", json={"conversation_id": "p1-ru-022"})
    assert r.status_code == 502 and "rate limit" in r.json()["error"]
    assert len(calls) == evaluator.MAX_ATTEMPTS
    assert r.json()["evaluation"]["status"] == "error"
    assert client.get("/api/conversations", params={"status": "error"}).json()[0]["conversation_id"] == "p1-ru-022"


def test_evaluate_unknown_conversation(client):
    r = client.post("/api/evaluate", json={"conversation_id": "nope"})
    assert r.status_code == 404 and "nope" in r.json()["error"]


def test_guidance_survives_ui_edit_without_field(client):
    cl = client.get("/api/checklists/default").json()
    for c in cl["criteria"]:
        c.pop("guidance", None)                     # редактор не знает про guidance
    cl["criteria"][4]["description"] = "Новая формулировка эмпатии"
    v2 = {c["id"]: c for c in client.put("/api/checklists/default", json=cl).json()["criteria"]}
    assert v2["c01"]["guidance"]                    # не менялся — правила перенесены
    assert "guidance" not in v2["c05"]              # изменён — старые правила не тянем


# --- self-consistency ---------------------------------------------------------------------------------
def test_vote_majority_median_and_low_confidence(default_checklist):
    a, b, c = llm_answer(), llm_answer(c04={"answer": "10"}, c05={"answer": "yes", "explanation": "B"}), \
        llm_answer(c04={"answer": "20"}, c05={"answer": "yes", "explanation": "C"})
    merged, votes = evaluator.vote(default_checklist, [a, b, c])
    assert votes["c04"] == [0, 10, 20]
    assert merged["criteria"]["c04"]["answer"] == "10" and merged["criteria"]["c04"]["confidence"] == "low"
    assert merged["criteria"]["c05"]["answer"] == "yes" and merged["criteria"]["c05"]["explanation"] == "B"
    assert merged["criteria"]["c05"]["confidence"] == "high"
    assert merged["criteria"]["c01"]["answer"] == "yes"


def test_evaluate_with_samples_sums_usage_and_reports_votes(client, monkeypatch):
    payloads = iter([llm_answer(), llm_answer(c05={"answer": "yes"}), llm_answer()])

    def fake(messages, schema, name="evaluation", reasoning_effort=None, model=None):
        return copy.deepcopy(next(payloads)), {"input_tokens": 1000, "cached_tokens": 0, "output_tokens": 100,
                                               "reasoning_tokens": 0, "cost_usd": 0.01, "duration_ms": 5}
    monkeypatch.setattr(evaluator, "complete_json", fake)
    ev = evaluator.evaluate("p1-ru-022", samples=3)
    c05 = next(c for c in ev["criteria"] if c["criterion_id"] == "c05")
    assert c05["answer"] == "no" and sorted(c05["votes"]) == ["no", "no", "yes"] and c05["agreement"] == 0.67
    assert ev["usage"]["cost_usd"] == 0.03 and ev["usage"]["input_tokens"] == 3000
    assert ev["model"]["samples"] == 3
