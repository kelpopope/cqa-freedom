"""Выгрузка оценок набора в репозиторий — жюри смотрит коммит, а не запускает проект.

Из корня репозитория:
  python eval/export_results.py --group CONTROL_ID          # последние оценки → results/<group>/
  python eval/export_results.py --group CONTROL_ID --run    # сначала импорт + оценка всех диалогов

Создаёт:
  results/<group>/evaluations.json — полные Evaluation (ответы, баллы, объяснения, доказательства)
  results/<group>/summary.csv      — диалог × критерий: ответ; итог; грубая ошибка
  results/<group>/README.md        — таблица для чтения на GitHub
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import db  # noqa: E402
from backend.app.config import get_settings  # noqa: E402
from backend.app.services import checklists as checklist_store  # noqa: E402
from backend.app.services import conversations as conversation_store  # noqa: E402
from backend.app.services.evaluator import EvaluationError, evaluate  # noqa: E402
from backend.app.services.fetcher import fetch_dataset  # noqa: E402

LABEL = {"yes": "Да", "no": "Нет", "na": "N/A"}


def fmt(answer) -> str:
    return LABEL.get(answer, str(answer))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", required=True)
    ap.add_argument("--checklist", default="default")
    ap.add_argument("--version", type=int, default=None, help="версия чек-листа (по умолчанию активная)")
    ap.add_argument("--run", action="store_true", help="импортировать набор и оценить все диалоги")
    ap.add_argument("--samples", type=int, default=None)
    args = ap.parse_args()

    db.init_db()
    checklist = checklist_store.get(args.checklist, args.version)
    if args.run:
        records, source = fetch_dataset(args.group)
        print(f"Импорт {args.group}: {conversation_store.import_records(records, args.group)} диалогов ({source})")
    items = conversation_store.list_items(group_id=args.group)
    ids = [i["conversation_id"] for i in items]
    if not ids:
        sys.exit(f"Нет диалогов группы {args.group} — запустите с --run")

    if args.run:
        started = time.time()

        def one(cid):
            try:
                return evaluate(cid, checklist["checklist_id"], checklist["version"], samples=args.samples)
            except EvaluationError as e:
                print(f"  ! {cid}: {e}", file=sys.stderr)
                return None
        with ThreadPoolExecutor(get_settings().llm_concurrency) as ex:
            done = [e for e in ex.map(one, ids) if e]
        print(f"Оценено {len(done)}/{len(ids)} за {time.time() - started:.0f} с")

    evaluations = []
    for cid in ids:
        ev = conversation_store.latest_evaluation(cid)
        if ev and ev.get("status") == "done":
            evaluations.append(ev)

    out = ROOT / "results" / args.group
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluations.json").write_text(json.dumps(evaluations, ensure_ascii=False, indent=1), encoding="utf-8")

    crit_ids = [c["id"] for c in checklist["criteria"]]
    with open(out / "summary.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["conversation_id", *crit_ids, "total", "final_total", "gross_error", "gross_types",
                    "needs_review", "checklist_version", "cost_usd"])
        for ev in evaluations:
            ans = {c["criterion_id"]: c["answer"] for c in ev["criteria"]}
            w.writerow([ev["conversation_id"], *[ans.get(k, "") for k in crit_ids], ev["total"],
                        ev["final"]["total"], ev["gross_error"]["flag"], " ".join(ev["gross_error"]["types"]),
                        ev["needs_review"], ev["checklist_version"], ev["usage"]["cost_usd"]])

    titles = {c["id"]: c["title"] for c in checklist["criteria"]}
    cost = sum(ev["usage"]["cost_usd"] for ev in evaluations)
    lines = [f"# Результаты оценки: {args.group}", "",
             f"Чек-лист «{checklist['name']}», версия {checklist['version']}. Модель: "
             f"{evaluations[0]['model']['name'] if evaluations else '—'}. Диалогов: {len(evaluations)}. "
             f"Стоимость: ${cost:.4f}.", "",
             "| Диалог | " + " | ".join(crit_ids) + " | Итог | Грубая ошибка |",
             "|---|" + "---|" * len(crit_ids) + "---|---|"]
    for ev in evaluations:
        ans = {c["criterion_id"]: c["answer"] for c in ev["criteria"]}
        gross = ", ".join(ev["gross_error"]["types"]) or ("да" if ev["gross_error"]["flag"] else "—")
        lines.append(f"| {ev['conversation_id']} | " + " | ".join(fmt(ans.get(k, "")) for k in crit_ids)
                     + f" | {ev['final']['total']} | {gross} |")
    lines += ["", "Критерии: " + "; ".join(f"{k} — {v}" for k, v in titles.items()), "",
              "Полные результаты с объяснениями и репликами-доказательствами: `evaluations.json`."]
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Записано в {out.relative_to(ROOT)}/ ({len(evaluations)} оценок)")


if __name__ == "__main__":
    main()
