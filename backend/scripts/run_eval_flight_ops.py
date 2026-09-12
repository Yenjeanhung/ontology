#!/usr/bin/env python3
"""一键评测「航班运行处置建议」知识库（评测集行内自带 kb_name，自动按名称定位知识库）。

用法（在 backend/ 目录下，或任意位置均可——脚本内部自动定位）：
  python scripts/run_eval_flight_ops.py                 # 完整链路评测
  python scripts/run_eval_flight_ops.py --limit 5       # 冒烟：只跑前 5 题
  python scripts/run_eval_flight_ops.py --ablation      # 完整链路 + baseline + no_bm25 对比
  python scripts/run_eval_flight_ops.py --eval-model deepseek-chat --eval-base-url https://api.deepseek.com
"""
import argparse
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
EVAL_SCRIPT = BACKEND_DIR / "scripts" / "eval_rag_ragas.py"
DEFAULT_TESTSET = BACKEND_DIR / "scripts" / "eval_data" / "flight_ops_advisory_golden.jsonl"


def run_eval(tag: str, args, extra):
    print(f"\n==== RAG eval run: {tag} ====")
    cmd = [
        sys.executable, str(EVAL_SCRIPT), "run",
        "--testset", str(args.testset),
        "--tag", tag,
        "--llm-temperature", "0.1",
    ] + list(extra)
    if args.limit > 0:
        cmd += ["--limit", str(args.limit)]
    if args.eval_model:
        cmd += ["--eval-model", args.eval_model]
    if args.eval_base_url:
        cmd += ["--eval-base-url", args.eval_base_url]
    proc = subprocess.run(cmd, cwd=str(BACKEND_DIR))
    if proc.returncode != 0:
        sys.exit(f"[FAIL] 评测组 '{tag}' 失败（退出码 {proc.returncode}）")


def main():
    parser = argparse.ArgumentParser(description="一键评测「航班运行处置建议」知识库 RAG 链路")
    parser.add_argument("--testset", default=str(DEFAULT_TESTSET), help="评测集 JSONL 路径")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（0=全部）")
    parser.add_argument("--ablation", action="store_true",
                        help="额外跑 baseline（纯向量）与 no_bm25 两组消融对比")
    parser.add_argument("--eval-model", default="", help="RAGAS 评估模型（默认复用项目 LLM）")
    parser.add_argument("--eval-base-url", default="", help="RAGAS 评估模型 base_url")
    args = parser.parse_args()

    run_eval("full", args, [])
    if args.ablation:
        # baseline：纯向量召回
        run_eval("baseline", args, ["--no-bm25", "--no-rewrite", "--no-rerank"])
        # 混合检索去掉 BM25（保留改写 + 重排）
        run_eval("no_bm25", args, ["--no-bm25"])

    print("\n完成。逐条得分报告在 backend/eval_out/report_*.csv")


if __name__ == "__main__":
    main()
