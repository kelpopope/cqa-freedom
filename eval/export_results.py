"""Официальная выгрузка результатов: results/dev/results.json и results/prod/results.json.

Из корня репозитория:
  python eval/export_results.py --set dev  --group DEV --run          # оценить недостающие и выгрузить 65 диалогов
  python eval/export_results.py --set prod --group <id_от_организаторов> --run --expect 10
  python eval/export_results.py --set prod --group <id> --check       # только проверить готовый файл

Формат — CQA_Student_Guide_Appendix_Results (JSON 1.0), проверяется перед записью (см. results_export.validate).
--run импортирует набор из API организаторов (исходные conversation_id) и оценивает диалоги без готовой оценки
активной версией чек-листа; --rerun — переоценить все.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import db  # noqa: E402
from backend.app.services import checklists as checklist_store  # noqa: E402
from backend.app.services import conversations as conversation_store  # noqa: E402
from backend.app.services import results_export  # noqa: E402
from backend.app.services.evaluator import EvaluationError, evaluate  # noqa: E402
from backend.app.services.fetcher import fetch_dataset  # noqa: E402
from backend.app.services.settings import runtime_settings  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, choices=["dev", "prod"], help="папка results/<set>/")
    ap.add_argument("--group", required=True, help="group_id набора в API организаторов")
    ap.add_argument("--run", action="store_true", help="импортировать набор и оценить диалоги без оценки")
    ap.add_argument("--rerun", action="store_true", help="переоценить все диалоги набора")
    ap.add_argument("--check", action="store_true", help="только проверить существующий results.json")
    ap.add_argument("--expect", type=int, default=None, help="ожидаемое число записей (prod: 10)")
    ap.add_argument("--team", default=os.getenv("TEAM_NAME", "CQA Freedom"))
    ap.add_argument("--checklist", default="default")
    args = ap.parse_args()

    db.init_db()
    out = ROOT / "results" / args.set / "results.json"
    expected = 10 if args.set == "prod" and args.expect is None else args.expect

    if args.run or args.rerun:
        records, source = fetch_dataset(args.group)
        print(f"Импорт {args.group}: {conversation_store.import_records(records, args.group)} диалогов ({source})")
    ids = [i["conversation_id"] for i in conversation_store.list_items(group_id=args.group)]
    if not ids:
        sys.exit(f"Нет диалогов группы {args.group} — запустите с --run")
    transcripts = {cid: len(conversation_store.get(cid)["transcript"]) for cid in ids}

    if args.check:
        doc = json.loads(out.read_text(encoding="utf-8"))
        problems = results_export.validate(doc, ids, expected, transcripts)
        print("OK — можно сдавать" if not problems else "\n".join(problems))
        sys.exit(1 if problems else 0)

    checklist = checklist_store.get(args.checklist)
    if args.run or args.rerun:
        todo = [cid for cid in ids if args.rerun or not (conversation_store.latest_evaluation(cid) or {}).get("status") == "done"]
        if todo:
            started = time.time()

            def one(cid):
                try:
                    return evaluate(cid, checklist["checklist_id"], checklist["version"])
                except EvaluationError as e:
                    print(f"  ! {cid}: {e}", file=sys.stderr)
                    return None
            with ThreadPoolExecutor(runtime_settings()["llm_concurrency"]) as ex:
                done = [e for e in ex.map(one, todo) if e]
            cost = sum(e["usage"]["cost_usd"] for e in done)
            print(f"Оценено {len(done)}/{len(todo)} за {time.time() - started:.0f} с, ${cost:.3f}")

    doc = results_export.build(ids, args.team, runtime_settings()["model"], checklist["checklist_id"], checklist["version"])
    problems = results_export.validate(doc, ids, expected, transcripts)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = sum(r["status"] == "ok" for r in doc["results"])
    print(f"Записано {out.relative_to(ROOT)}: {len(doc['results'])} записей, ok={ok}, error={len(doc['results']) - ok}")
    if problems:
        print("ПРОБЛЕМЫ ФОРМАТА:\n  " + "\n  ".join(problems))
        sys.exit(1)
    print("Формат проверен — можно коммитить и пушить.")


if __name__ == "__main__":
    main()
