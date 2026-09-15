"""运行人工构造的证据充分性边界集；不调用数据库或模型。"""

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evidence_sufficiency_evaluation import evaluate_evidence_sufficiency  # noqa: E402


def main() -> None:
    cases_path = PROJECT_ROOT / "evaluation_data" / "evidence_sufficiency_cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    report = evaluate_evidence_sufficiency(cases)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
