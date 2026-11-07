import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import accuracy
from backend.app.services.scoring import score


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


def fake_eval(answers, checklist, gross=False):
    s = score(checklist, answers)
    return {"criteria": [{"criterion_id": r["criterion_id"], "answer": r["answer"]} for r in s["criteria"]],
            "total": s["total"], "gross_error": {"flag": gross}}


def test_gt_file_is_valid_for_checklist(default_checklist):
    gt = accuracy.load_gt("DEV")
    assert gt and len(gt["labels"]) == 60
    for cid, lab in gt["labels"].items():
        score(default_checklist, lab["answers"])            # каждая разметка допустима по чек-листу
        assert set(lab["uncertain"]) <= set(lab["answers"]), cid
        assert (lab["answers"]["c08"] == "yes") == bool(lab["flag_types"]), cid


def test_compute_counts_hits_uncertain_and_mae(client, default_checklist):
    gt = accuracy.load_gt("DEV")
    evals = {}
    for cid in ["p1-ru-001", "p1-ru-022"]:
        evals[cid] = fake_eval(gt["labels"][cid]["answers"], default_checklist,
                               gross=gt["labels"][cid]["answers"]["c08"] == "yes")
    # одна ошибка в спорной ячейке ru-001 (c07) и одна — в уверенной ru-022 (c04)
    evals["p1-ru-001"] = fake_eval({**gt["labels"]["p1-ru-001"]["answers"], "c07": "yes"}, default_checklist)
    evals["p1-ru-022"] = fake_eval({**gt["labels"]["p1-ru-022"]["answers"], "c04": 5}, default_checklist, gross=True)
    r = accuracy.compute("DEV", evals)
    assert r["n"] == 2 and r["overall"] == round(14 / 16, 4)
    assert r["overall_confident"] < 1 and r["per_criterion"]["c07"] == 0.5
    assert {(m["conversation_id"], m["criterion_id"], m["uncertain"]) for m in r["mismatches"]} == \
        {("p1-ru-001", "c07", True), ("p1-ru-022", "c04", False)}
    assert r["gross"] == {"tp": 1, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0}
    assert r["total_mae"] > 0


def test_accuracy_endpoint(client):
    r = client.get("/api/metrics/accuracy")
    assert r.status_code == 200 and r.json()["n"] == 0 and "mismatches" not in r.json()
    assert client.get("/api/metrics/accuracy", params={"group_id": "NOPE"}).status_code == 404
