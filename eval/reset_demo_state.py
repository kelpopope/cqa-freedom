"""Очистить следы репетиций перед контрольной оценкой: апелляции, правки проверяющего, подтверждения.

Зачем: решённые апелляции становятся прецедентами в промпте. Тестовые решения на репетиции
не должны влиять на оценку контрольного набора.

  python eval/reset_demo_state.py            # показать, что будет удалено
  python eval/reset_demo_state.py --yes      # удалить
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app import db  # noqa: E402
from backend.app.services import checklists  # noqa: E402
from backend.app.services.evaluator import get_evaluation, recompute_final, save  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    db.init_db()
    with db.connect() as conn:
        appeals = [db.loads(r["data"]) for r in conn.execute("SELECT data FROM appeals")]
        reviewed = [db.loads(r["data"]) for r in conn.execute("SELECT data FROM evaluations")]
    reviewed = [e for e in reviewed if e.get("review") or (e.get("final") or {}).get("overrides")]
    print(f"Апелляций и правок: {len(appeals)}; оценок с подтверждением или правками: {len(reviewed)}")
    for a in appeals:
        print(f"  {a['appeal_id']} {a['conversation_id']} {a['criterion_id']} ({a.get('author_role')})")
    if not args.yes:
        print("Ничего не удалено. Запустите с --yes.")
        return
    with db.connect() as conn:
        conn.execute("DELETE FROM appeals")
    for ev in reviewed:
        ev = get_evaluation(ev["evaluation_id"])
        ev["review"] = None
        ev["final"]["overrides"] = []
        recompute_final(ev, checklists.get(ev["checklist_id"], ev["checklist_version"]))
        save(ev)
    print("Очищено.")


if __name__ == "__main__":
    main()
