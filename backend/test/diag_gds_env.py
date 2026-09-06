"""GDS 环境自检：版本 / 过程签名 / 投影模板渲染正确性（不出网、不建投影，秒级）。

背景（2026-09 图计算联调踩坑记录）：
  1. 投影模板用 str.format 渲染，Cypher 自身花括号必须双写 {{ }} 转义，
     否则 {category_id: '{cid}'} 被当占位符解析 → KeyError: 'category_id'
  2. GDS 2.13 的 gds.graph.project.cypher 无 orientation 配置键，
     configuration 里传 relationshipQuery 报 Unexpected configuration key；
     UNDIRECTED 语义由关系查询的无向模式 -[r]- 实现（每边双向两行）

运行（在 backend 目录下）：
    python test/diag_gds_env.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.graph_store import gds  # noqa: E402

# 联调用过的真实 category（航图 20 万节点），可用第一个参数覆盖
DEFAULT_CID = "0b9891ae7fab"


def main() -> int:
    cid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CID
    ok = True

    # 1) GDS 版本与 project.cypher 签名
    drv = gds._driver()
    try:
        with gds._session(drv) as s:
            ver = s.run("RETURN gds.version() AS v").single()["v"]
            print("GDS version:", ver)
            rec = s.run(
                "SHOW PROCEDURES YIELD name, signature "
                "WHERE name = 'gds.graph.project.cypher' RETURN signature"
            ).single()
            print("signature:", rec["signature"] if rec else "NOT FOUND")
    finally:
        drv.close()

    # 2) 投影模板渲染：占位符必须全部展开，Cypher 花括号必须保留
    nq = gds._NODE_QUERY_TPL.format(cid=cid)
    rq = gds._REL_QUERY_TPL.format(cid=cid)
    print("NODE_Q:", nq)
    print("REL_Q :", rq)
    for label, q in (("node", nq), ("rel", rq)):
        if "{cid}" in q or "{{" in q or "}}" in q:
            print(f"[FAIL] {label} query 渲染残留占位符/转义符")
            ok = False
        elif f"category_id: '{cid}'" not in q:
            print(f"[FAIL] {label} query 未注入 category_id")
            ok = False
        else:
            print(f"[OK] {label} query 渲染正确")

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
