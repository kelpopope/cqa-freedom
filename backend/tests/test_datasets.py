"""Загрузка наборов из файла / JSON / адреса и выгрузка (набор, CSV)."""
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services import datasets
from backend.app.services.fetcher import FetchError

CHAT = {"conversation_id": "t-001", "channel": "agent chat", "language": "RU", "transcript": [
    {"timestamp": "00:01", "speaker": "client", "text": "Здравствуйте, не вижу облигации"},
    {"timestamp": "00:40", "speaker": "operator", "text": "Здравствуйте! Меня зовут Анна. Откройте раздел «Рынок»."}]}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    with TestClient(app) as c:
        yield c


def test_parse_formats():
    org = {"isSuccess": True, "data": {"dataset_group_id": "CTRL", "records": [CHAT]}}
    assert datasets.parse_payload(org) == ([CHAT], "CTRL")
    assert datasets.parse_payload({"records": [CHAT]})[0] == [CHAT]
    assert datasets.parse_payload([CHAT])[0] == [CHAT]
    assert datasets.parse_payload(CHAT)[0] == [CHAT]
    with pytest.raises(FetchError):
        datasets.parse_payload({"isSuccess": False, "error": "нет доступа"})
    with pytest.raises(FetchError):
        datasets.parse_payload("{не json")


def test_detect_language():
    assert datasets.detect_language([{"speaker": "client", "text": "Сәлеметсіз бе, шотымды көре алмаймын"}]) == "KZ"
    assert datasets.detect_language([{"speaker": "client", "text": "Hello, I cannot log in"}]) == "EN"
    assert datasets.detect_language([{"speaker": "client", "text": "Не могу войти"}]) == "RU"


def test_upload_fills_gaps_skips_bad_and_never_overwrites_other_sets(client):
    r = client.post("/api/datasets/upload", json={"group_id": "MY TEST", "payload": [
        {"transcript": [{"speaker": "client", "text": "Hello, where is my report?"}]},   # без id и языка
        {"conversation_id": "bad", "transcript": []},                                     # пустой
        CHAT]}).json()
    assert r["group_id"] == "MY-TEST" and r["imported"] == 2 and r["skipped"][0]["error"] == "пустой transcript"
    items = {i["conversation_id"]: i for i in client.get("/api/conversations", params={"group_id": "MY-TEST"}).json()}
    assert items["my-test-001"]["language"] == "EN" and "t-001" in items
    # тот же id в другом наборе получает префикс набора, а не перетирает диалог MY-TEST
    r2 = client.post("/api/datasets/upload", json={"group_id": "OTHER", "payload": {"records": [CHAT]}}).json()
    assert r2["renamed"] == [{"from": "t-001", "to": "other-t-001"}]
    assert client.get("/api/conversations/t-001").json()["conversation"]["group_id"] == "MY-TEST"
    groups = {g["group_id"]: g for g in client.get("/api/datasets").json()}
    assert groups["MY-TEST"]["count"] == 2 and groups["OTHER"]["evaluated"] == 0
    assert client.post("/api/datasets/upload", json={"payload": {"foo": 1}}).status_code == 422


def test_fetch_url_and_exports(client, monkeypatch):
    monkeypatch.setattr(datasets, "fetch_url", lambda url: {"isSuccess": True, "data": {"dataset_group_id": "API1", "records": [CHAT]}})
    r = client.post("/api/datasets/fetch-url", json={"url": "https://example.org/data.json"}).json()
    assert r["group_id"] == "API1" and r["imported"] == 1
    doc = client.get("/api/datasets/API1/export").json()
    assert doc["data"]["records"][0]["transcript"][1]["speaker"] == "operator"
    again = client.post("/api/datasets/upload", json={"group_id": "API1", "payload": doc}).json()   # выгрузка загружается обратно
    assert again["imported"] == 1 and again["renamed"] == []
    csv = client.get("/api/results/export.csv", params={"group_id": "API1"})
    assert csv.status_code == 200 and "t-001" in csv.text and "не оценён" in csv.text
    assert client.get("/api/datasets/NOPE/export").status_code == 404


def test_fetch_url_rejects_non_http():
    with pytest.raises(FetchError):
        datasets.fetch_url("file:///etc/passwd")
