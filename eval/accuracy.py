"""Точность оценки против нашего эталона DEV.

Из корня репозитория:
  python eval/accuracy.py                 # метрика по последним сохранённым оценкам
  python eval/accuracy.py --run           # сначала заново оценить все размеченные диалоги (тратит API)
  python eval/accuracy.py --run --only p1-ru-014,p1-kz-008 --effort high --samples 3
  python eval/accuracy.py --out eval/reports/run.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import db  # noqa: E402
from backend.app.config import get_settings  # noqa: E402
from backend.app.services import accuracy  # noqa: E402
from backend.app.services.evaluator import EvaluationError, evaluate  # noqa: E402


def run_evaluations(ids: list[str], effort: str | None, samples: int | None) -> dict[str, dict]:
    def one(cid):
        try:
            return cid, evaluate(cid, reasoning_effort=effort, samples=samples)
        except EvaluationError as e:
            print(f"  ! {cid}: {e}", file=sys.stderr)
            return cid, None

    started, results = time.time(), {}
    with ThreadPoolExecutor(get_settings().llm_concurrency) as ex:
        for cid, ev in ex.map(one, ids):
            if ev:
                results[cid] = ev
    cost = sum(ev["usage"]["cost_usd"] for ev in results.values())
    print(f"Оценено {len(results)}/{len(ids)} за {time.time() - started:.0f} с, ${cost:.4f} "
          f"(≈ ${cost / max(len(results), 1):.4f} за диалог)")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="DEV")
    ap.add_argument("--run", action="store_true", help="заново оценить размеченные диалоги")
    ap.add_argument("--only", default="", help="через запятую: какие диалоги оценивать")
    ap.add_argument("--effort", default=None, help="reasoning effort: low|medium|high|xhigh")
    ap.add_argument("--samples", type=int, default=None, help="прогонов на диалог (голосование)")
    ap.add_argument("--out", default="", help="сохранить отчёт JSON")
    args = ap.parse_args()

    db.init_db()
    gt = accuracy.load_gt(args.group)
    if gt is None:
        sys.exit(f"Нет эталона data/gt/{args.group.lower()}_labels.json")
    ids = [x for x in args.only.split(",") if x] or list(gt["labels"])

    evaluations = None
    if args.run:
        fresh = run_evaluations(ids, args.effort, args.samples)
        evaluations = accuracy.latest_evaluations(list(gt["labels"]), gt["checklist_id"], gt.get("checklist_version"))
        evaluations.update(fresh)
    report = accuracy.compute(args.group, evaluations)

    print(f"\nДиалогов с оценкой: {report['n']}/{report['labeled']}")
    print(f"Совпадение ячеек: {report['overall']}  (без спорных: {report['overall_confident']})")
    print(f"MAE итога: {report['total_mae']}   грубые ошибки: {report['gross']}")
    print("По критериям:  " + "  ".join(f"{k}={v}" for k, v in report["per_criterion"].items()))
    print("Без спорных:   " + "  ".join(f"{k}={v}" for k, v in report["per_criterion_confident"].items()))
    print("По языкам:     " + "  ".join(f"{k}={v}" for k, v in report["per_language"].items()))
    print(f"Каппа Коэна: {report['kappa']}  по критериям: " + "  ".join(f"{k}={v}" for k, v in report["per_criterion_kappa"].items()))
    print("Шкалы, ±1 ступень: " + "  ".join(f"{k}={v}" for k, v in report["scale_within_one_step"].items()))
    print("Доказательства:", report["evidence"])
    print("\nРасхождения (* — спорная ячейка эталона):")
    for m in sorted(report["mismatches"], key=lambda m: (m["criterion_id"], m["conversation_id"])):
        print(f"  {m['criterion_id']} {m['conversation_id']:<10} эталон={m['expected']!s:<4} система={m['got']!s:<4}"
              f"{' *' if m['uncertain'] else ''}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
