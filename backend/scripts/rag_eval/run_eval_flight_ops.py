#!/usr/bin/env python3
"""一键评测「航班运行处置建议」知识库（RAGAS）。

用法（在 backend/ 目录下，或任意位置均可——脚本内部自动定位）：
  python scripts/rag_eval/run_eval_flight_ops.py                 # 完整链路评测
  python scripts/rag_eval/run_eval_flight_ops.py --limit 5       # 冒烟：只跑前 5 题
  python scripts/rag_eval/run_eval_flight_ops.py --ablation      # 完整链路 + baseline + no_bm25 对比
  python scripts/rag_eval/run_eval_flight_ops.py --eval-model deepseek-chat --eval-base-url https://api.deepseek.com

知识库定位：--kb-name 默认「航班运行处置建议」，按名称模糊匹配（唯一命中才通过）；
评测集行内若写了 kb_name/kb_id，则行内优先于该缺省值。
"""
from subprocess import CompletedProcess


import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent.parent
EVAL_SCRIPT = SCRIPT_DIR / "eval_rag_ragas.py"
DEFAULT_KB_NAME = "航班运行处置建议"
DEFAULT_TESTSET = SCRIPT_DIR / "eval_data" / "flight_ops_advisory_golden.jsonl"


def run_eval(tag: str, args, extra):
    print(f"\n==== RAG eval run: {tag} ====")
    cmd = [
        sys.executable, str(EVAL_SCRIPT), "run",
        "--testset", str(args.testset),
        "--tag", tag,
        "--llm-temperature", "0.1",
    ] + list(extra)
    if args.kb_name:
        cmd += ["--kb-name", args.kb_name]
    if args.limit > 0:
        cmd += ["--limit", str(args.limit)]
    if args.eval_model:
        cmd += ["--eval-model", args.eval_model]
    if args.eval_base_url:
        cmd += ["--eval-base-url", args.eval_base_url]
    proc: CompletedProcess[bytes] = subprocess.run(cmd, cwd=str(BACKEND_DIR))
    if proc.returncode != 0:
        sys.exit(f"[FAIL] 评测组 '{tag}' 失败（退出码 {proc.returncode}）")


def main() -> None:
    parser = argparse.ArgumentParser(description="一键评测「航班运行处置建议」知识库 RAG 链路")
    parser.add_argument("--kb-name", default=DEFAULT_KB_NAME,
                        help=f"缺省知识库名称（默认：{DEFAULT_KB_NAME}，评测集行内优先）")
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

    print("\n完成。逐条得分报告在 backend/scripts/rag_eval/eval_out/report_*.csv")


if __name__ == "__main__":
    main()
