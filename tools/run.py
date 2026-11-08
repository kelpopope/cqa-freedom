#!/usr/bin/env python3
"""Запуск CQA в один клик (Windows и macOS).

Что делает:
  1. Проверяет Python (нужен 3.11+).
  2. Создаёт окружение .venv и ставит зависимости — только при первом запуске или если поменялся requirements.txt.
  3. Создаёт .env из .env.example, если его нет, и предупреждает, если не заполнен ключ OpenAI.
  4. Находит свободный порт (8000, 8001, …), запускает сервер и сам открывает браузер, когда сайт готов.

Флаги:
  --reset        стереть базу (cqa.db): запуск «с чистого листа» — пустая база и чек-лист версии 1
  --yes          не спрашивать подтверждение при --reset
  --no-browser   не открывать браузер
  --port N       начать поиск свободного порта с N (по умолчанию 8000)

Остановить сервер: закрыть окно или нажать Ctrl+C.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQ = ROOT / "backend" / "requirements.txt"
STAMP = VENV / ".requirements.sha256"
ENV = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def say(msg: str) -> None:
    print(f"[CQA] {msg}", flush=True)


def fail(msg: str) -> None:
    say(f"ОШИБКА: {msg}")
    sys.exit(1)


def read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def check_python() -> None:
    if sys.version_info < (3, 11):
        fail(f"нужен Python 3.11 или новее, сейчас {sys.version.split()[0]}. Установите с https://www.python.org/downloads/")


def ensure_venv() -> None:
    if PY.exists():
        return
    say("Первый запуск: создаю окружение .venv (около минуты)…")
    venv.EnvBuilder(with_pip=True).create(VENV)
    if not PY.exists():
        fail("не удалось создать окружение .venv")


def ensure_deps() -> None:
    digest = hashlib.sha256(REQ.read_bytes()).hexdigest()
    if STAMP.exists() and STAMP.read_text().strip() == digest:
        return
    say("Устанавливаю зависимости (нужен интернет, 1–2 минуты)…")
    code = subprocess.call([str(PY), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(REQ)], cwd=ROOT)
    if code != 0:
        fail("не удалось установить зависимости — проверьте интернет и запустите ещё раз")
    STAMP.write_text(digest)


def ensure_env() -> None:
    if not ENV.exists():
        shutil.copy(ENV_EXAMPLE, ENV)
        say("Создан файл .env из .env.example.")
    env = read_env()
    if not env.get("OPENAI_API_KEY"):
        say("ВНИМАНИЕ: в .env не заполнен OPENAI_API_KEY — уже оценённые диалоги откроются, но новая оценка не пойдёт.")
    if not env.get("ORG_API_URL"):
        say("ВНИМАНИЕ: в .env не заполнен ORG_API_URL — загрузка по group_id от организаторов не сработает (файл и JSON — сработают).")


def db_files() -> list[Path]:
    p = Path(read_env().get("DB_PATH") or "cqa.db")
    p = p if p.is_absolute() else ROOT / p
    return [p, p.with_name(p.name + "-wal"), p.with_name(p.name + "-shm"), p.with_name(p.name + "-journal")]


def reset_db(confirm: bool) -> None:
    files = [f for f in db_files() if f.exists()]
    if not files:
        say("База уже пустая — будет создана заново.")
        return
    if confirm:
        ans = input(f"[CQA] Стереть базу {files[0].name}? Пропадут загруженные диалоги, оценки и апелляции. Введите «да»: ").strip().lower()
        if ans not in ("да", "y", "yes", "д"):
            say("Отменено, база не тронута.")
            return
    for f in files:
        try:
            f.unlink()
        except PermissionError:
            fail(f"файл {f.name} занят — закройте другое окно с запущенным сервером и повторите")
    say("База стёрта. Сервер создаст пустую базу и чек-лист версии 1.")


def free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    fail(f"не нашёл свободный порт в диапазоне {start}–{start + 19}")
    return start


def open_when_ready(url: str, server: subprocess.Popen) -> None:
    for _ in range(120):
        if server.poll() is not None:
            return
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=1) as r:
                if r.status == 200:
                    say(f"Сайт готов: {url}")
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.5)
    say(f"Сервер долго не отвечает — откройте вручную: {url}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Запуск CQA одним кликом")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    os.chdir(ROOT)
    for stream in (sys.stdout, sys.stderr):  # кириллица в консоли Windows при любой кодовой странице
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    say("Контроль качества диалогов — Freedom Broker")
    check_python()
    ensure_venv()
    ensure_deps()
    ensure_env()
    if args.reset:
        reset_db(confirm=not args.yes)

    port = free_port(args.port)
    url = f"http://localhost:{port}"
    if port != args.port:
        say(f"Порт {args.port} занят — запускаю на {port}.")
    say(f"Запускаю сервер на {url} … Остановить: закрыть окно или Ctrl+C.")
    server = subprocess.Popen([str(PY), "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url, server), daemon=True).start()
    try:
        code = server.wait()
    except KeyboardInterrupt:
        say("Останавливаю сервер…")
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        say("Сервер остановлен.")
        return
    if code != 0:
        fail(f"сервер завершился с ошибкой (код {code}) — сообщения выше подскажут причину")


if __name__ == "__main__":
    main()
