# API-контракт CQA (фронт ↔ бэк)

> Единый источник правды для участников A и B. Меняется только по договорённости обоих.
> Базовый адрес: `/api` (фронт и API на одном origin, раздаёт FastAPI). Все ответы — JSON, UTF-8.
> Ошибки: HTTP 4xx/5xx + `{"error": "текст"}`.

## 1. Сущности

### Conversation (диалог)
```json
{
  "conversation_id": "p1-ru-001",
  "group_id": "DEV",
  "channel": "agent chat",            // "agent chat" | "voice call"
  "language": "RU",                   // "RU" | "KZ" | "EN"
  "audio_url": null,                  // ссылка у звонков
  "transcript": [
    {
      "index": 0,                     // ID реплики = индекс в массиве; якорь во фронте: #msg-0
      "timestamp": "00:00",           // исходная метка (MM:SS у чатов, ISO 8601 у звонков) — не менять
      "offset_sec": 0,                // секунды от начала диалога (считает бэк) — для плеера и сортировки
      "speaker": "client",            // "client" | "operator" | "bot" | "system"
      "text": "Где посмотреть мой тариф?",
      "on_hold": false                // true для реплик во время удержания (звонки) — клиент их не слышит
    }
  ]
}
```

### ConversationListItem (элемент списка)
```json
{
  "conversation_id": "p1-ru-022", "group_id": "DEV", "channel": "agent chat", "language": "RU",
  "turns": 13, "operator_name": "Кирилл",
  "evaluation": {                     // null, если ещё не оценён
    "evaluation_id": "ev_123", "total": 12.5, "final_total": 12.5,
    "gross_error": true, "needs_review": false, "checklist_version": 1, "status": "done"
  },
  "appeal_status": null               // null | "open" | "resolved"
}
```

### Checklist (чек-лист, версионируемый)
```json
{
  "checklist_id": "default",
  "version": 1,                        // PUT создаёт version+1; старые версии не удаляются
  "name": "Чек-лист качества чатов",
  "is_active": true,
  "rounding": 2,
  "sections": [ { "id": "s1", "title": "Начало и понимание запроса" } ],
  "criteria": [
    {
      "id": "c04",
      "section_id": "s2",
      "title": "Корректная и полная консультация",
      "description": "Оцените ответ или следующий шаг по шкале.",   // правило «Да»/суть критерия — идёт в промпт
      "type": "scale",                 // "binary" (да/нет) | "scale" (ступени) | "flag" (без баллов, напр. грубые ошибки)
      "weight": 20,                    // для "flag" = 0
      "na_allowed": true,
      "na_condition": "Чат закончился до того, как клиент сформулировал запрос.",
      "scale": [                       // только для "scale"; верхняя ступень = полный вес
        { "value": 20, "description": "Все существенные вопросы закрыты или дан корректный следующий шаг." },
        { "value": 15, "description": "..." }
      ],
      "flag_types": null               // только для "flag": ["insult","private_service","confidential_disclosure"]
    }
  ]
}
```
Полный стартовый чек-лист: `data/checklists/default.json`.

### Evaluation (результат оценки)
```json
{
  "evaluation_id": "ev_123",
  "conversation_id": "p1-ru-022",
  "checklist_id": "default", "checklist_version": 1,
  "status": "done",                    // "queued" | "running" | "done" | "error"
  "model": { "provider": "openai", "name": "gpt-6.1-sol" },
  "usage": { "input_tokens": 3100, "output_tokens": 900, "cost_usd": 0.0012, "duration_ms": 4200 },
  "created_at": "2026-11-07T18:00:00+05:00",
  "criteria": [
    {
      "criterion_id": "c01",
      "answer": "yes",                 // "yes" | "no" | "na" для binary; число-ступень для scale; "yes"|"no" для flag
      "raw_points": 10,                // баллы до перераспределения (вес × доля)
      "points": 11.76,                 // баллы после перераспределения N/A (итог = сумма points)
      "weight": 10, "effective_weight": 11.76,
      "explanation": "Оператор поздоровался на русском и представился в первом ответе.",
      "evidence": [ { "index": 9, "quote": "Здравствуйте, я Кирилл." } ],
      "expected_range": null,          // для пропущенного действия: { "from": 9, "to": 11 } — где ожидалось
      "na_reason": null,               // для N/A: какое условие выполнено
      "evidence_valid": true,          // бэк проверил: индекс есть и цитата найдена в реплике
      "confidence": "high",            // "high" | "low"
      "source": "llm"                  // "llm" | "rules" | "llm+rules"
    }
  ],
  "sections": [ { "section_id": "s1", "points": 20.5, "max_points": 29.41 } ],
  "total": 12.5,                       // null, если применимых критериев с весом нет
  "gross_error": {
    "flag": true,
    "types": ["private_service"],
    "evidence": [ { "index": 9, "quote": "Скиньте деньги мне на карту" } ],
    "rules_agree": true                // совпал ли сигнал правил с LLM
  },
  "needs_review": false,               // true: невалидное доказательство / низкая уверенность / LLM ≠ правила
  "timeline": [                        // события для ленты (строит бэк из system/ролей)
    { "type": "escalation", "index": 8 }, { "type": "operator_greeting", "index": 9 },
    { "type": "identity_verified", "index": null }, { "type": "data_disclosure", "index": null },
    { "type": "end", "index": 13, "kind": "operator_closed" }   // kind: "farewell" | "client_left" | "connection_lost" | "operator_closed"
  ],
  "bot_context": { "from": 0, "to": 8, "summary": "Клиент спрашивает, что купить из облигаций; бот предложил поиск." },
  "final": {                           // с учётом решений по апелляциям; без апелляций = как выше
    "total": 12.5,
    "overrides": [ { "criterion_id": "c05", "from": "no", "to": "yes", "appeal_id": "ap_1" } ]
  }
}
```

### Appeal (апелляция)
```json
{
  "appeal_id": "ap_1",
  "evaluation_id": "ev_123",
  "conversation_id": "p1-ru-022",
  "criterion_id": "c05",
  "comment": "Оператор извинился в реплике 11.",
  "author_role": "operator",
  "created_at": "2026-11-07T19:00:00+05:00",
  "status": "open",                    // "open" | "resolved"
  "original": { "answer": "no", "points": 0, "total": 12.5 },
  "decision": null                     // после решения:
  // { "action": "confirm" | "change", "new_answer": "yes", "reason": "...", "reviewer_role": "reviewer",
  //   "decided_at": "...", "new_total": 27.5 }
}
```

## 2. Эндпоинты

| Метод | Путь | Тело / параметры | Ответ |
|---|---|---|---|
| GET | `/api/health` | — | `{"ok": true, "llm": {"provider","name","mode"}}` |
| POST | `/api/datasets/import` | `{"group_id": "DEV"}` | `{"group_id","imported": 65}` — бэк ходит к API организаторов |
| POST | `/api/conversations/upload` | Conversation без `index/offset_sec` (свой транскрипт) | Conversation |
| GET | `/api/conversations` | `?group_id=&language=&channel=&risk=true&status=&q=` | `ConversationListItem[]` |
| GET | `/api/conversations/{id}` | — | `{ "conversation": Conversation, "evaluation": Evaluation\|null, "appeals": Appeal[] }` |
| GET | `/api/checklists` | — | `Checklist[]` (последние версии) |
| GET | `/api/checklists/{id}` | `?version=` | Checklist |
| POST | `/api/checklists` | Checklist без `version` | Checklist (version 1) |
| PUT | `/api/checklists/{id}` | Checklist | Checklist (version+1) |
| POST | `/api/checklists/{id}/activate` | `{"version": 2}` | Checklist |
| POST | `/api/evaluate` | `{"conversation_id","checklist_id","version"?}` | Evaluation (синхронно) |
| POST | `/api/evaluate/batch` | `{"group_id"?, "conversation_ids"?, "checklist_id", "version"?}` | `{"job_id"}` |
| GET | `/api/jobs/{job_id}` | — | `{"job_id","total","done","failed","status"}` |
| GET | `/api/evaluations/compare` | `?checklist_id=&from_version=&to_version=&group_id=` | `[{"conversation_id","from_total","to_total","changed_criteria":[...]}]` |
| POST | `/api/appeals` | `{"evaluation_id","criterion_id","comment"}` | Appeal |
| GET | `/api/appeals` | `?status=open` | Appeal[] (+ фрагмент: `context: Turn[]`) |
| POST | `/api/appeals/{id}/decision` | `{"action","new_answer"?,"reason"}` | Appeal (с `decision.new_total`) |
| GET | `/api/metrics/accuracy` | `?group_id=DEV` | `{"overall": 0.87, "per_criterion": {"c01": 0.93, ...}, "n": 30}` |
| GET / PUT | `/api/settings` | `{"model": "gpt-6.1-sol", "self_consistency": 1}` | settings |

## 3. Правила расчёта (реализует `scoring.py`, фронт только отображает)
1. Применимость: N/A допустим, только если `na_allowed` и выполнено `na_condition`.
2. `binary`: yes → `weight`, no → 0. `scale`: `weight × value / max(scale.value)`. `flag`: баллов нет.
3. `W = Σ weight` применимых (не N/A, не flag). Если `W == 0` → `total = null`.
4. `points_i = raw_points_i × 100 / W`; `effective_weight_i = weight_i × 100 / W`; `total = round(Σ points_i, 2)`.
5. Секции: сумма `points` / сумма `effective_weight` критериев секции.
6. Грубая ошибка — отдельный флаг, на `total` не влияет.
7. Решение по апелляции подменяет ответ критерия → полный пересчёт по п. 1–5 → `final.total`.

## 4. Моки для фронта
`frontend/mocks/conversations.json`, `conversation_p1-ru-022.json`, `checklists.json`, `appeals.json` — по схемам выше. `api.js`: `const USE_MOCKS = true` до Merge 1.
