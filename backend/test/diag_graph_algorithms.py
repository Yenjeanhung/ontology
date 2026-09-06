"""图计算 5 算法全量诊断：直接调 _execute_algorithm 走真实投影 + GDS（绕过 API 层）。

覆盖：pagerank / betweenness / louvain / node_similarity / degree
预期耗时参考（20 万节点 / 77.6 万关系真实图，2026-09 实测）：
  pagerank 5.6s | betweenness 22.8s（>5 万节点自动切近似采样）| louvain 14.5s
  | node_similarity ~230s | degree 2.5s

运行（在 backend 目录下；需 Neo4j+GDS 与已迁入的数据）：
    python test/diag_graph_algorithms.py                     # 全部算法
    python test/diag_graph_algorithms.py pagerank degree     # 指定算法
    python test/diag_graph_algorithms.py --cid <category_id> # 指定图谱
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.graph_analysis_service import _execute_algorithm  # noqa: E402

DEFAULT_CID = "0b9891ae7fab"
ALL_ALGOS = ["pagerank", "betweenness", "louvain", "node_similarity", "degree"]

PARAMS = {
    "pagerank": {"top_n": 20, "write_back": False, "label_filter": ""},
    "betweenness": {"top_n": 20, "write_back": False, "label_filter": ""},
    "louvain": {"top_n": 10, "write_back": False, "label_filter": ""},
    "node_similarity": {"top_n": 10, "write_back": False, "label_filter": "",
                        "similarity_cutoff": 0.5},
    "degree": {"top_n": 10, "write_back": False, "label_filter": ""},
}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cid = DEFAULT_CID
    for a in sys.argv[1:]:
        if a.startswith("--cid="):
            cid = a.split("=", 1)[1]
    algos = args or ALL_ALGOS
    bad = [a for a in algos if a not in PARAMS]
    if bad:
        print("未知算法:", bad, "| 可选:", ALL_ALGOS)
        return 2

    failed = []
    for algo in algos:
        t0 = time.time()
        try:
            stats, results = _execute_algorithm(cid, algo, dict(PARAMS[algo]))
            rows = results.get("rows") or []
            print("[%s] OK in %.1fs stats=%s first=%s"
                  % (algo, time.time() - t0, stats,
                     str(rows[0])[:160] if rows else "empty"))
        except Exception:
            failed.append(algo)
            print("[%s] FAILED in %.1fs" % (algo, time.time() - t0))
            traceback.print_exc()
        sys.stdout.flush()

    print("RESULT:", "PASS" if not failed else f"FAIL {failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
