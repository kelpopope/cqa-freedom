# Бэкенд: что готово и чем API_CONTRACT дополнен

> Для участника B (интерфейс). Всё ниже — **дополнения** к `API_CONTRACT.md`: существующие поля и эндпоинты не менялись.
> Запуск: `uvicorn backend.app.main:app --reload` → http://localhost:8000 (FastAPI раздаёт `frontend/`, режим live).

## Новые эндпоинты

| Метод | Путь | Зачем (сценарий защиты) |
|---|---|---|
| GET | `/api/conversations?gross=true` | «Покажите все диалоги с грубыми ошибками» — только грубые (с учётом решений по апелляциям). `needs_review=true` — только требующие проверки. `risk=true` — как раньше: грубые **или** needs_review. |
| GET | `/api/datasets` | Загруженные группы: `[{group_id, count}]`. |
| GET | `/api/checklists/{id}/versions` | Список версий: `[{version, is_active, created_at, name, criteria: [id]}]` — для выбора «было/стало». |
| GET | `/api/jobs` | Последние задачи пакетной оценки. |
| GET | `/api/evaluations/{evaluation_id}` | Оценка по id. |
| GET | `/api/conversations/{id}/evaluations` | История оценок диалога (версии чек-листа, повторы): кратко, новые сверху. |
| GET | `/api/appeals/{appeal_id}` | Одна апелляция с `context`. |
| GET | `/api/analytics?group_id=&version=` | Индикатор для доработки скриптов: `criteria[]` (`fail_rate`, `answers`, `failed_conversations`), `by_language`, `by_operator`, `gross_errors`, `needs_review`, `avg_total`. Отсортировано: самые проваливаемые критерии сверху. |
| GET/PUT | `/api/settings` | `model`, `reasoning_effort` (low/medium/high/xhigh/max), `self_consistency` (1–7), `llm_concurrency`. |
| GET | `/api/metrics/accuracy?group_id=DEV&details=true` | Совпадение с нашим эталоном DEV (95,4 %). |

## Новые поля

**Evaluation.criteria[i]**: `title`; `flag_types` (у критерия-флага); `votes` и `agreement` — ответы трёх прогонов модели и доля согласия (например `["no","no","yes"]`, `0.67`) — можно показать бейджем «3/3», «2/3».

**Evaluation**: `review_reasons` (почему `needs_review`), `client_request` (одно предложение — с чем пришёл клиент), `model.reasoning_effort|prompt_version|samples`, `usage.cached_tokens|reasoning_tokens`.

**Evaluation.final** (после апелляций; без апелляций = как ИИ):
```json
{ "total": 100.0,
  "overrides": [{ "criterion_id": "c07", "from": "no", "to": "yes", "appeal_id": "ap_…", "reason": "…",
                  "decided_at": "…", "carried_from": "ev_…" }],
  "criteria": [{ "criterion_id": "c07", "answer": "yes", "raw_points": 5, "points": 5.0, "effective_weight": 5.0, "overridden": true }],
  "sections": [...], "gross_error": false }
```
- ИИ-оценка (`criteria`, `total`) **никогда не перезаписывается** → «исходная → решение → финал» видно всегда.
- `carried_from`: при переоценке (например, после смены чек-листа) решение проверяющего переносится в новую оценку, если этот критерий не менялся.

**Appeal.original**: дополнительно `ai_answer`, `ai_total`, `explanation`, `evidence`. **Appeal.criterion_title**.
Ошибки апелляций: `409` — уже есть открытая по этому критерию / решение уже принято; `422` — пустой комментарий или причина, недопустимый `new_answer`.

**Job** (`GET /api/jobs/{id}`): `status` = queued | running | done | done_with_errors | error | interrupted; плюс `cost_usd`, `checklist_version`, `evaluation_ids`, `errors`.

**Compare** (`GET /api/evaluations/compare`) — массив, как в контракте: `[{conversation_id, from_total, to_total, changed_criteria: ["c05","c09"], delta, changes: [{criterion_id, from, to}], added: {c09: "yes"}, removed: {c05: "no"}}]`. `changed_criteria` — id изменившихся, добавленных и удалённых критериев.

**timeline**: только произошедшие события (без `index: null`). Нет `identity_verified` — идентификации не было.

**Turn**: `duplicate: true` — дубль реплики автотранскрипции (можно приглушить).

## Время ответа
Одна оценка ≈ 15–20 с (три прогона идут параллельно). «Оценить все» на 10 диалогах ≈ 30–40 с — показывайте прогресс из `GET /api/jobs/{id}` (`done/total`).

## Сценарий «изменить чек-лист» целиком
1. `PUT /api/checklists/default` (новая версия, сразу активна; вес/формулировка/новый критерий).
2. `POST /api/evaluate/batch {"group_id": "<контрольный>"}` → опрос `GET /api/jobs/{id}`.
3. `GET /api/evaluations/compare?from_version=1&to_version=2&group_id=…` → экран «было → стало».
