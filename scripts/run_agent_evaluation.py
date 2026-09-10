"""从命令行批量运行 OpsPilot Agent 评测集。"""

import argparse
import sys
from pathlib import Path


# 脚本位于 scripts/，把项目根目录加入模块搜索路径后才能导入业务模块。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_evaluation_runner import (  # noqa: E402
    build_langgraph_runner,
    load_evaluation_cases,
    run_evaluation_cases,
)


DEFAULT_CASES_FILE = PROJECT_ROOT / "evaluation_data" / "agent_task_cases.json"


def parse_args() -> argparse.Namespace:
    """解析评测文件、模型模式和运行环境；默认完全离线。"""
    parser = argparse.ArgumentParser(description="运行 OpsPilot Agent 批量评测")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_FILE)
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument(
        "--runtime",
        choices=("isolated", "app"),
        default="isolated",
        help="isolated 使用固定本地数据；app 使用当前应用基础设施",
    )
    return parser.parse_args()


def main() -> int:
    """加载标准用例、调用现有 Agent 图并打印 JSON 报告。"""
    args = parse_args()
    if args.runtime == "isolated":
        # 默认基线不依赖 Docker，适合开发机和 CI 稳定复现。
        from agent_evaluation_runtime import build_isolated_evaluation_graph

        graph = build_isolated_evaluation_graph()
    else:
        # app 模式才初始化 Redis、PostgreSQL 和 Java 网关，用于完整集成验收。
        from main import AGENT_GRAPH

        graph = AGENT_GRAPH

    cases = load_evaluation_cases(args.cases)
    runner = build_langgraph_runner(graph, mode=args.mode)
    summary = run_evaluation_cases(cases, runner)
    print(summary.model_dump_json(indent=2))
    # 失败用例返回非零退出码，后续可直接接入 CI 质量门禁。
    return 0 if summary.failed_cases == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
