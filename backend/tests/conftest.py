import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def default_checklist():
    return json.loads((ROOT / "data" / "checklists" / "default.json").read_text(encoding="utf-8"))
