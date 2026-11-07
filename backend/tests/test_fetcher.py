import httpx

from backend.app.services import fetcher
from backend.app.services.fetcher import compute_offsets, extract_operator_name, normalize_conversation, normalize_transcript


def T(ts, speaker, text):
    return {"timestamp": ts, "speaker": speaker, "text": text}


def test_chat_offsets_mmss():
    assert compute_offsets(["00:00", "00:56", "02:50", "14:05"]) == [0, 56, 170, 845]


def test_call_offsets_iso_from_earliest_even_if_unordered():
    ts = ["2026-11-03T15:53:24.5120868+00:00", "2026-11-03T15:53:20.2112291+00:00", "2026-11-03T15:54:20.2112291+00:00"]
    assert compute_offsets(ts) == [4.3, 0.0, 60.0]


def test_hold_segment_marks_only_inner_turns():
    tr = normalize_transcript([
        T("00:00", "operator", "Прошу ожидать на линии."),
        T("00:01", "system", "Клиент находится на удержании."),
        T("00:02", "operator", "Коллега, подскажи по бумаге."),
        T("00:03", "operator", "Спасибо."),
        T("00:04", "system", "Клиент больше не находится на удержании."),
        T("00:05", "operator", "Благодарю за ожидание."),
    ])
    assert [t["on_hold"] for t in tr] == [False, False, True, True, False, False]
    assert [t["index"] for t in tr] == list(range(6))


def test_duplicates_only_same_speaker_text_and_time():
    tr = normalize_transcript([
        T("2026-11-03T13:11:40.99+00:00", "client", "Я хотела вот."),
        T("2026-11-03T13:11:40.99+00:00", "client", "Я хотела вот."),
        T("2026-11-03T13:11:45.00+00:00", "client", "[Персональные данные скрыты]"),
        T("2026-11-03T13:11:47.00+00:00", "client", "[Персональные данные скрыты]"),
    ])
    assert [t["duplicate"] for t in tr] == [False, True, False, False]


def test_speaker_normalization():
    tr = normalize_transcript([T("00:00", "Agent", "a"), T("00:01", "CUSTOMER", "b"), T("00:02", "???", "c")])
    assert [t["speaker"] for t in tr] == ["operator", "client", "system"]


def test_operator_names_ru_kz_en_and_mask():
    def name(text):
        return extract_operator_name([{"speaker": "operator", "text": text}])
    assert name("Здравствуйте, я Кирилл. Скиньте деньги") == "Кирилл"
    assert name("Добрый день, меня зовут Нина.") == "Нина"
    assert name("Сәлеметсіз бе! Мен Нұрланмын.") == "Нұрлан"
    assert name("Сәлеметсіз бе, мен Дана.") == "Дана"
    assert name("Сәлеметсіз бе, менің атым Аружан.") == "Аружан"
    assert name("Hi, I'm Maya from support.") == "Maya"
    assert name("Здравствуйте, Фридом брокер меня зовут [Имя скрыто]") is None
    assert name("Привет, где именно ищете?") is None
    assert name("Сейчас я проверю, я Олег.") == "Олег"


def test_normalize_conversation_shape():
    conv = normalize_conversation({"conversation_id": "x-1", "channel": "agent chat", "language": "ru",
                                   "transcript": [T("00:00", "client", " Привет ")], "audio_url": None}, "DEV")
    assert conv["language"] == "RU" and conv["group_id"] == "DEV"
    assert conv["transcript"][0] == {"index": 0, "timestamp": "00:00", "offset_sec": 0, "speaker": "client",
                                     "text": "Привет", "on_hold": False, "duplicate": False}


def test_fetch_appends_group_id_without_dropping_signature(monkeypatch, tmp_path):
    seen = {}

    def fake_get(url, timeout):
        seen["url"] = url
        return httpx.Response(200, json={"isSuccess": True, "error": "", "data": {"records": [{"conversation_id": "a"}]}})

    monkeypatch.setenv("ORG_API_URL", "https://example.test/run?api-version=1&code=SECRET")
    monkeypatch.setattr(fetcher, "RAW_DIR", tmp_path)
    monkeypatch.setattr(fetcher.httpx, "get", fake_get)
    records, source = fetcher.fetch_dataset("CTRL 1")
    assert seen["url"] == "https://example.test/run?api-version=1&code=SECRET&group_id=CTRL%201"
    assert records == [{"conversation_id": "a"}] and source == "api"
    assert (tmp_path / "CTRL_1.json").exists()


def test_fetch_falls_back_to_cache(monkeypatch, tmp_path):
    (tmp_path / "G.json").write_text('{"data": {"records": [{"conversation_id": "c"}]}}', encoding="utf-8")

    def broken_get(url, timeout):
        raise httpx.ConnectError("down")

    monkeypatch.setenv("ORG_API_URL", "https://example.test/run?code=x")
    monkeypatch.setattr(fetcher, "RAW_DIR", tmp_path)
    monkeypatch.setattr(fetcher.httpx, "get", broken_get)
    assert fetcher.fetch_dataset("G") == ([{"conversation_id": "c"}], "cache")
