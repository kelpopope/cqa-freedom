from backend.app.services.sla import first_response, summarize


def conv(turns, channel="agent chat"):
    return {"channel": channel, "transcript": [
        {"index": i, "speaker": s, "text": t, "offset_sec": o, "on_hold": h} for i, (s, t, o, h) in enumerate(turns)]}


BASE = [("client", "Помогите", 0, False), ("system", "Эскалация на оператора.", 51, False)]


def test_wait_from_escalation_to_first_operator_reply():
    r = first_response(conv(BASE + [("operator", "Здравствуйте, я Егор", 185, False)]))
    assert r["wait_sec"] == 134 and r["breached"] is False and r["first_reply_index"] == 2


def test_breach_over_three_minutes_and_hold_replies_ignored():
    r = first_response(conv(BASE + [("operator", "коллеге", 100, True), ("operator", "Здравствуйте", 240, False)]))
    assert r["wait_sec"] == 189 and r["breached"] is True and r["first_reply_index"] == 3


def test_no_reply_calls_and_no_escalation():
    assert first_response(conv(BASE + [("system", "Клиент покинул чат.", 90, False)]))["wait_sec"] is None
    assert first_response(conv(BASE, channel="voice call")) is None
    assert first_response(conv([("client", "x", 0, False), ("operator", "y", 5, False)])) is None
    assert first_response(conv([("client", "x", 0, False), ("system", "Chat transferred to an agent.", 10, False),
                                ("operator", "Hello", 30, False)]))["wait_sec"] == 20


def test_summary():
    s = summarize([{"wait_sec": 60, "breached": False}, {"wait_sec": 200, "breached": True}, None,
                   {"wait_sec": None, "breached": None}])
    assert s == {"threshold_sec": 180, "measured": 2, "breached": 1, "avg_wait_sec": 130.0, "max_wait_sec": 200}
