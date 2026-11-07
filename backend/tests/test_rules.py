import copy
import json
from pathlib import Path

import pytest

from backend.app.services import evaluator
from backend.app.services.fetcher import normalize_conversation
from backend.app.services.rules import analyze

from .test_evaluator import RECORD, llm_answer

ROOT = Path(__file__).resolve().parents[2]


def conv(*turns):
    return normalize_conversation({"conversation_id": "t", "transcript": [
        {"timestamp": f"00:{i:02d}", "speaker": s, "text": t} for i, (s, t) in enumerate(turns)]}, "T")


def types(*turns):
    return analyze(conv(*turns)["transcript"])["types"]


@pytest.mark.parametrize("text,expected", [
    ("Скиньте деньги мне на карту, я сам их вложу с выгодой.", ["private_service"]),
    ("Можете дать личный номер? Вечером объясню, это будет моя частная консультация.", ["private_service"]),
    ("Жеке нөміріңізді қалдырсаңыз, ақылы түрде айтып беремін.", ["private_service"]),
    ("Send me your personal number. I charge for private advice.", ["private_service"]),
    ("Вы вообще читать умеете? Бот всё написал.", ["insult"]),
    ("Can't you read? The bot already answered.", ["insult"]),
    ("Я посмотрел: на вашем счёте есть ограничение на вывод.", ["confidential_disclosure"]),
    ("Сіздің шотыңызда ақша шығаруға шектеу қойылғанын көріп тұрмын.", ["confidential_disclosure"]),
    ("I can see a withdrawal restriction on your account.", ["confidential_disclosure"]),
])
def test_patterns(text, expected):
    assert types(("operator", text)) == expected


@pytest.mark.parametrize("text", [
    "Пополните счёт со своей карты через «Меню → Пополнить».",
    "Отчёт брокера по вашему счёту можно скачать в приложении.",
    "Чтобы проверить ваш счёт, сначала подтвердим личность.",
    "Услуга доступна за плату по тарифу, подробности в разделе «Тарифы».",
    "Open Reports and Documents to download the statement for your account.",
    "Есеп шотыңыз бойынша «Отчёты и документы» бөлімінде.",
])
def test_benign_phrases_do_not_fire(text):
    assert types(("operator", text)) == []


def test_only_operator_outside_hold():
    assert types(("client", "Скиньте деньги мне на карту")) == []
    assert types(("system", "Клиент находится на удержании."), ("operator", "Да он тупой, скиньте деньги мне на карту"),
                 ("system", "Клиент больше не находится на удержании.")) == []


def test_disclosure_after_identity_is_fine():
    assert types(("system", "Личность клиента подтверждена."),
                 ("operator", "Я посмотрел: на вашем счёте есть ограничение на вывод.")) == []
    r = analyze(conv(("operator", "На вашем счёте есть ограничение."), ("operator", "Подтверждение пока не нужно."))["transcript"])
    assert r["first_disclosure"] == 0 and [s["type"] for s in r["signals"]] == ["confidential_disclosure", "identity_skip"]


@pytest.mark.skipif(not (ROOT / "data" / "dev.json").exists(), reason="нет data/dev.json")
def test_rules_match_dev_ground_truth():
    gt = json.loads((ROOT / "data/gt/dev_labels.json").read_text(encoding="utf-8"))["labels"]
    recs = json.loads((ROOT / "data/dev.json").read_text(encoding="utf-8"))["data"]["records"]
    for r in recs:
        got = analyze(normalize_conversation(r, "DEV")["transcript"])["types"]
        expected = sorted(gt[r["conversation_id"]]["flag_types"]) if r["conversation_id"] in gt else []
        assert got == expected, r["conversation_id"]


# --- слияние с решением модели -------------------------------------------------------------------------
@pytest.fixture
def checklist(default_checklist):
    return default_checklist


def assembled(checklist, record, llm):
    return evaluator.assemble(normalize_conversation(record, "DEV"), checklist, llm)


def test_agreement_marks_source(checklist):
    ev = assembled(checklist, RECORD, llm_answer())
    c08 = next(c for c in ev["criteria"] if c["criterion_id"] == "c08")
    assert ev["gross_error"]["rules_agree"] is True and c08["source"] == "llm+rules"
    assert ev["gross_error"]["rules_types"] == ["private_service"] and c08["rules_signals"][0]["index"] == 3
    assert ev["needs_review"] is False


def test_rules_catch_what_model_missed(checklist):
    llm = llm_answer(c08={"answer": "no", "flag_types": [], "evidence": []})
    ev = assembled(checklist, RECORD, llm)
    assert ev["gross_error"]["flag"] is False and ev["gross_error"]["rules_agree"] is False
    assert ev["needs_review"] is True and any("правила нашли" in r and "[#3]" in r for r in ev["review_reasons"])


def test_model_flag_without_rules_goes_to_review(checklist):
    clean = copy.deepcopy(RECORD)
    clean["transcript"][3]["text"] = "Здравствуйте, я Кирилл. Откройте «Котировки»."
    clean["transcript"][5]["text"] = "Конечно. Найдите бумагу по тикеру."
    llm = llm_answer(c08={"answer": "yes", "flag_types": ["insult"], "evidence": [{"index": 5, "quote": "Конечно"}]})
    ev = assembled(checklist, clean, llm)
    assert ev["gross_error"]["rules_agree"] is False and ev["needs_review"] is True


def test_disclosure_before_identity_checks_identity_criterion(checklist):
    rec = copy.deepcopy(RECORD)
    rec["transcript"][3]["text"] = "Здравствуйте, я Руслан. Я посмотрел: на вашем счёте есть ограничение на вывод."
    rec["transcript"][5]["text"] = "Подтверждение пока не нужно."
    llm = llm_answer(c03={"answer": "na", "na_reason": "x"},
                     c08={"answer": "yes", "flag_types": ["confidential_disclosure"],
                          "evidence": [{"index": 3, "quote": "на вашем счёте есть ограничение"}]})
    ev = assembled(checklist, rec, llm)
    assert {"type": "data_disclosure", "index": 3} in ev["timeline"]
    assert [e["type"] for e in ev["timeline"]] == ["escalation", "operator_greeting", "data_disclosure", "end"]
    assert any("Идентификация" in r for r in ev["review_reasons"])
