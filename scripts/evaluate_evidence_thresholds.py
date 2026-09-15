"""重放已记录的最高相似度分数；不连接数据库或模型。"""

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evidence_gate_metrics import evaluate_thresholds  # noqa: E402


def main() -> None:
    cases_path = PROJECT_ROOT / "evaluation_data" / "evidence_gate_score_cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    results = evaluate_thresholds(cases, [0.50, 0.55, 0.57, 0.60])
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
