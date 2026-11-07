import pytest

from backend.app.services.scoring import ScoringError, apply_overrides, round_half_up, score

ALL_YES = {"c01": "yes", "c02": "yes", "c03": "yes", "c04": 20, "c05": "yes", "c06": 10, "c07": "yes", "c08": "no"}
ALL_NO = {"c01": "no", "c02": "no", "c03": "no", "c04": 0, "c05": "no", "c06": 0, "c07": "no", "c08": "no"}


def by_id(result):
    return {row["criterion_id"]: row for row in result["criteria"]}


# --- базовые случаи -------------------------------------------------------------------------------
def test_all_yes_is_100_and_points_equal_weights(default_checklist):
    r = score(default_checklist, ALL_YES)
    assert r["total"] == 100.0
    rows = by_id(r)
    for c in default_checklist["criteria"]:
        if c["type"] != "flag":
            assert rows[c["id"]]["points"] == c["weight"]
            assert rows[c["id"]]["effective_weight"] == c["weight"]
    assert r["gross_error"] is False


def test_all_no_is_zero(default_checklist):
    r = score(default_checklist, ALL_NO)
    assert r["total"] == 0.0
    assert all(row["points"] in (0, None) for row in r["criteria"])


def test_scale_points_are_weight_times_step_over_max(default_checklist):
    r = score(default_checklist, {**ALL_YES, "c04": 15, "c06": 7})
    rows = by_id(r)
    assert rows["c04"]["raw_points"] == 15.0   # 20 * 15 / 20
    assert rows["c06"]["raw_points"] == 10.5   # 15 * 7 / 10
    assert r["total"] == 100 - 5 - 4.5


# --- перераспределение N/A ------------------------------------------------------------------------
def test_na_redistributes_contract_example(default_checklist):
    # c02 (15) = N/A -> W = 85 -> c01: 10 * 100 / 85 = 11.76 (пример из API_CONTRACT)
    r = score(default_checklist, {**ALL_YES, "c02": "na"})
    rows = by_id(r)
    assert rows["c02"]["points"] is None and rows["c02"]["effective_weight"] == 0
    assert rows["c01"]["points"] == 11.76
    assert rows["c01"]["effective_weight"] == 11.76
    assert r["total"] == 100.0


def test_na_two_criteria(default_checklist):
    # c02 и c03 = N/A -> W = 65; c01 = no; остальные yes -> (65 - 10) / 65 * 100 = 84.615...
    r = score(default_checklist, {**ALL_YES, "c01": "no", "c02": "na", "c03": "na"})
    assert r["total"] == 84.62
    assert by_id(r)["c05"]["effective_weight"] == 23.08  # 15 * 100 / 65


def test_effective_weights_sum_to_100(default_checklist):
    r = score(default_checklist, {**ALL_NO, "c02": "na", "c04": "na", "c07": "na"})
    exact = sum(row["effective_weight"] for row in r["criteria"] if row["effective_weight"])
    assert abs(exact - 100) < 0.05


def test_no_applicable_criteria_gives_null_total():
    checklist = {
        "rounding": 2,
        "sections": [{"id": "s1", "title": "x"}],
        "criteria": [
            {"id": "a", "section_id": "s1", "type": "binary", "weight": 50, "na_allowed": True},
            {"id": "b", "section_id": "s1", "type": "scale", "weight": 50, "na_allowed": True,
             "scale": [{"value": 10}, {"value": 0}]},
            {"id": "f", "section_id": "s1", "type": "flag", "weight": 0, "na_allowed": False},
        ],
    }
    r = score(checklist, {"a": "na", "b": "na", "f": "yes"})
    assert r["total"] is None
    assert r["sections"][0]["points"] is None
    assert r["gross_error"] is True


# --- грубые ошибки ----------------------------------------------------------------------------------
def test_gross_error_does_not_change_total(default_checklist):
    clean = score(default_checklist, ALL_YES)
    flagged = score(default_checklist, {**ALL_YES, "c08": "yes"})
    assert flagged["total"] == clean["total"] == 100.0
    assert flagged["gross_error"] is True and flagged["gross_criteria"] == ["c08"]
    assert by_id(flagged)["c08"]["points"] is None


# --- секции -----------------------------------------------------------------------------------------
def test_sections(default_checklist):
    r = score(default_checklist, {**ALL_YES, "c02": "na", "c04": 10})
    s = {x["section_id"]: x for x in r["sections"]}
    # W = 85: s1 = c01 только -> 11.76 / 11.76; s2 = c03 + c04(10 из 20)
    assert s["s1"] == {"section_id": "s1", "points": 11.76, "max_points": 11.76}
    assert s["s2"]["max_points"] == round_half_up(40 * 100 / 85)
    assert s["s2"]["points"] == round_half_up(30 * 100 / 85)
    assert s["s4"] == {"section_id": "s4", "points": None, "max_points": 0}


# --- округление -------------------------------------------------------------------------------------
def test_round_half_up_not_bankers():
    assert round_half_up(13.125) == 13.13      # round() дал бы 13.12
    assert round_half_up(0.005) == 0.01
    assert round_half_up(2.675) == 2.68         # float 2.675 < 2.675, но считаем по десятичной записи


def test_total_uses_exact_arithmetic_and_half_up():
    # W = 80, raw = 15 * 7 / 10 = 10.5 -> 10.5 * 100 / 80 = 13.125 -> 13.13
    checklist = {
        "rounding": 2,
        "sections": [],
        "criteria": [
            {"id": "a", "type": "scale", "weight": 15, "na_allowed": False,
             "scale": [{"value": 10}, {"value": 7}, {"value": 4}, {"value": 0}]},
            {"id": "b", "type": "binary", "weight": 65, "na_allowed": False},
        ],
    }
    assert score(checklist, {"a": 7, "b": "no"})["total"] == 13.13


# --- валидация ответов ------------------------------------------------------------------------------
@pytest.mark.parametrize("cid,bad", [
    ("c01", "na"),       # N/A не разрешён
    ("c05", "na"),
    ("c08", "na"),       # flag без N/A
    ("c04", 12),         # нет такой ступени
    ("c06", 5),
    ("c01", 1),          # binary ждёт yes/no
    ("c04", "yes"),      # scale ждёт число
    ("c04", True),
])
def test_invalid_answers_raise(default_checklist, cid, bad):
    with pytest.raises(ScoringError):
        score(default_checklist, {**ALL_YES, cid: bad})


def test_missing_and_unknown_criteria_raise(default_checklist):
    answers = dict(ALL_YES)
    del answers["c03"]
    with pytest.raises(ScoringError, match="c03"):
        score(default_checklist, answers)
    with pytest.raises(ScoringError, match="c99"):
        score(default_checklist, {**ALL_YES, "c99": "yes"})


def test_answers_are_normalized(default_checklist):
    r = score(default_checklist, {**ALL_YES, "c01": " YES ", "c04": "15", "c02": "NA"})
    rows = by_id(r)
    assert rows["c01"]["answer"] == "yes" and rows["c04"]["answer"] == 15 and rows["c02"]["answer"] == "na"


# --- апелляции и смена чек-листа --------------------------------------------------------------------
def test_appeal_override_recomputes_total(default_checklist):
    answers = {**ALL_YES, "c05": "no"}
    before = score(default_checklist, answers)["total"]
    after = score(default_checklist, apply_overrides(answers, [{"criterion_id": "c05", "from": "no", "to": "yes"}]))
    assert before == 85.0 and after["total"] == 100.0


def test_override_to_na_redistributes(default_checklist):
    answers = {**ALL_YES, "c07": "no"}
    r = score(default_checklist, apply_overrides(answers, [{"criterion_id": "c07", "to": "na"}]))
    assert r["total"] == 100.0


def test_replaced_criterion_works_dynamically(default_checklist):
    # Сценарий защиты: заменили критерий c05 на новый c09 со своей шкалой
    cl = dict(default_checklist)
    cl["criteria"] = [c for c in default_checklist["criteria"] if c["id"] != "c05"] + [{
        "id": "c09", "section_id": "s3", "title": "Персонализация", "type": "scale", "weight": 15,
        "na_allowed": True, "na_condition": "...", "scale": [{"value": 3}, {"value": 2}, {"value": 0}],
    }]
    answers = {k: v for k, v in ALL_YES.items() if k != "c05"}
    assert score(cl, {**answers, "c09": 2})["total"] == 95.0     # 85 + 15 * 2/3 = 95
    assert score(cl, {**answers, "c09": "na"})["total"] == 100.0
