# CQA — Conversation Quality Analytics

Прототип системы оценки качества диалогов операторов поддержки Freedom Broker по настраиваемому чек-листу с помощью LLM.
Хакатон Data Challenge'26 (Nazarbayev University × Freedom Broker).

## Возможности
- Настраиваемый чек-лист с версиями: критерии, веса, шкалы, N/A.
- Автооценка диалогов RU / KZ / EN: ответ, баллы, объяснение и реплика-доказательство по каждому критерию.
- Расчёт итога с перераспределением весов при N/A, отдельный флаг грубых ошибок.
- Апелляции: оператор оспаривает критерий → проверяющий подтверждает или меняет → пересчёт.

## Модель и стоимость
- OpenAI `gpt-6.1-sol`, Structured Outputs.
- Примерная стоимость одной оценки: ~$0,07 (уточним по факту).

## Запуск
> Будет дополнено по мере готовности.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; на macOS/Linux: source .venv/bin/activate
pip install -r backend/requirements.txt
copy .env.example .env          # заполнить OPENAI_API_KEY и ORG_API_URL
uvicorn backend.app.main:app --reload
```
Открыть http://localhost:8000

### Только интерфейс, без сервера (демо-данные)
```bash
python tools/dev_server.py
```
Открыть http://localhost:5173/frontend/index.html — интерфейс работает на встроенных демо-диалогах.
Переключатели режима: `?mock=1` — демо-данные, `?live=1` — сервер (`/api`).

## Структура
- `backend/` — FastAPI, оценка, расчёт баллов, апелляции.
- `frontend/` — интерфейс на HTML/CSS/JS (встраиваемый компонент).
- `data/checklists/default.json` — стартовый чек-лист.
- `docs/` — план, контракт API, ресерч.

## Команда
- Абука — бэкенд и LLM.
- Участник B — интерфейс.
