"""GDS 能力盘点：列出本环境实际可用的 GDS 过程，按算法族分组打印。

用途：
  设计"要不要上某高级算法"前先实测——GDS 分社区版(CE)/企业版(EE)，
  且同一版本不同族的可用过程差异很大，凭记忆写文档容易写进不存在的过程。

运行（cwd=backend）：
    python test/diag_gds_catalog.py                 # 全量分组打印
    python test/diag_gds_catalog.py embedding path  # 只看指定族/关键词
    python test/diag_gds_catalog.py --all           # 打印 gds.list() 原始全量

输出：每族一行 `[有]/[无] + 过程名`，末尾给出 EE 授权状态提示。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.graph_store import gds  # noqa: E402

# 族名 -> 关注的过程关键字（小写匹配，命中任一即算该过程可用）
FAMILIES = {
    "节点嵌入 (embedding)": ["fastrp", "node2vec", "graphsage", "hashgnn"],
    "向量近邻 (knn)": ["knn"],
    "相似度 (similarity)": ["nodesimilarity", "similarity"],
    "路径搜索 (path)": [
        "shortestpath", "allshortestpaths", "dijkstra", "astar", "yens",
        "randomwalk", "bfs", "dfs", "spanningtree", "steinerTree",
    ],
    "中心性 (centrality)": [
        "pagerank", "articlerank", "eigenvector", "hits", "closeness",
        "harmonic", "betweenness", "degree", "celf", "influence",
    ],
    "社区发现 (community)": [
        "louvain", "leiden", "labelpropagation", "lpa", "wcc", "scc",
        "trianglecount", "triangle", "localclusteringcoefficient",
        "k1coloring", "modularityoptimization", "sllpa", "conductance",
        "kmeans", "kcore",
    ],
    "链路预测 (link prediction)": [
        "adamicadar", "commonneighbors", "preferentialattachment",
        "totalneighbors", "resourceallocation", "samecommunity",
    ],
    "图机器学习 (pipeline)": [
        "nodeclassification", "linkprediction", "pipeline", "nodemodel",
    ],
    "图管理 (catalog)": [
        "graph.project", "graph.create", "graph.drop", "graph.list",
        "graph.exists", "graph.sample", "subgraph", "graph.export",
        "graph.streamnodeproperty", "graph.removerelationship",
    ],
    "辅助 (util)": ["util.asnode", "util.astensor", "util.nodemetric", "scale"],
}

# 明确属于 EE 的功能（社区版即使过程存在，调用也会报授权错）
EE_ONLY_HINT = {
    "graphsage": "EE（训练为 EE；CE 通常不可用）",
    "hashgnn": "EE",
    "leiden": "EE（部分版本 alpha 可用，实测为准）",
    "nodeclassification": "EE",
    "linkprediction": "EE（pipeline 版；单指标函数 CE 可用）",
    "graph.export": "EE",
    "kcore": "EE",
}


def main() -> int:
    args = sys.argv[1:]
    show_all = "--all" in args
    keywords = [a.lower() for a in args if not a.startswith("--")]

    drv = gds._driver()
    try:
        with gds._session(drv) as s:
            ver = s.run("RETURN gds.version() AS v").single()["v"]
            print("GDS version:", ver)
            try:
                lic = s.run("RETURN gds.isLicensed() AS l").single()["l"]
                print("企业版授权 (gds.isLicensed):", lic)
            except Exception as e:  # 过程不存在也算信息
                print("企业版授权: 无法探测(%s)" % type(e).__name__)

            rows = list(s.run(
                "CALL gds.list() YIELD name, description "
                "RETURN name, description ORDER BY name"
            ))
    finally:
        drv.close()

    names = [r["name"].lower() for r in rows]
    print("可用过程总数:", len(names))
    print()

    if show_all:
        for r in rows:
            print(" -", r["name"], "|", (r["description"] or "").splitlines()[0][:90])
        return 0

    if "--probe" in args:
        return probe(keywords[0] if keywords else None)
    if "--wcc" in args:
        return wcc_detail(keywords[0] if keywords else None)

    for fam, keys in FAMILIES.items():
        if keywords and not any(k in fam.lower() for k in keywords):
            # 也允许用族内关键字过滤
            if not any(k in " ".join(keys) for k in keywords):
                continue
        hits = sorted({n for n in names if any(k in n for k in keys)})
        print(f"## {fam}  ({len(hits)})")
        if not hits:
            print("   [无] 该族过程在本环境不可用")
        for n in hits[:40]:
            mark = ""
            for ee_key, note in EE_ONLY_HINT.items():
                if ee_key in n:
                    mark = "   <-- " + note
                    break
            print("   [有]", n + mark)
        print()

    # 关键能力速判
    def has(kw):
        return any(kw in n for n in names)

    print("== 速判（过程是否存在，不代表可调用） ==")
    for label, kw in [
        ("节点嵌入可算（FastRP/node2vec）", "fastrp"),
        ("KNN 向量近邻", "knn"),
        ("加权最短路 Dijkstra", "dijkstra"),
        ("K 最短路 Yen", "yens"),
        ("随机游走采样", "randomwalk"),
        ("弱连通分量 WCC", "wcc"),
        ("链路预测 Adamic-Adar", "adamicadar"),
        ("三角计数", "trianglecount"),
        ("局部聚类系数", "localclusteringcoefficient"),
        ("Leiden", "leiden"),
        ("子图抽取 subgraph", "subgraph"),
        ("示例采样 rwr", "graph.sample"),
    ]:
        print(f"  {'可用' if has(kw) else '不可用':<6} {label}")
    print()

    if "--probe" in args:
        return probe(keywords[0] if keywords else None)
    print("提示：加 --probe [图名] 可实测这些过程在社区版是否真能调用")
    print("RESULT: PASS")
    return 0


def wcc_detail(graph=None) -> int:
    """图连通性精确统计（只读 stream，不写库）：算出孤立点数量与主分量覆盖率。

    wcc.stats 只给分位数（p999=1 只能推算），这里按分量聚合成精确分布。
    运行：python test/diag_gds_catalog.py --wcc [图名]
    """
    drv = gds._driver()
    try:
        with gds._session(drv) as s:
            graphs = [r["graphName"] for r in s.run(
                "CALL gds.graph.list() YIELD graphName RETURN graphName")]
            if not graphs:
                print("[SKIP] 无投影")
                return 0
            g = graph or graphs[0]
            total = s.run("CALL gds.graph.list() YIELD graphName, nodeCount "
                          "WHERE graphName = $g RETURN nodeCount", g=g).single()["nodeCount"]
            print("投影:", g, "总节点:", total)

            rows = list(s.run(
                "CALL gds.wcc.stream($g) YIELD nodeId, componentId "
                "WITH componentId, count(*) AS sz "
                "RETURN sz, count(*) AS cnt ORDER BY sz DESC", g=g))
            comps = sum(r["cnt"] for r in rows)
            isolated_cnt = next((r["cnt"] for r in rows if r["sz"] == 1), 0)
            largest = rows[0]["sz"] if rows else 0
            print("分量总数:", comps)
            print("最大分量节点数:", largest, f"({largest / total:.2%} 覆盖率)")
            print("孤立点（size=1 分量）:", isolated_cnt, f"({isolated_cnt / total:.2%})")
            print("\n分量大小分布（sz=分量大小, cnt=该大小的分量个数）：")
            for r in rows[:10]:
                print(f"   sz={r['sz']:<8} cnt={r['cnt']}")
            print("\n非单点分量中的最大 5 个：",
                  [(r["sz"], r["cnt"]) for r in rows if r["sz"] > 1][:5])
    finally:
        drv.close()
    print("WCC DETAIL DONE")
    return 0


# --------------------------------------------------------------------------
# 可调用性探针：GDS 2.x 会把企业版过程也注册进 gds.list()，名字存在 != 可调用
# --------------------------------------------------------------------------
PROBES = [
    ("WCC 弱连通分量", "CALL gds.wcc.stats($g) YIELD componentCount, componentDistribution"),
    ("三角计数", "CALL gds.triangleCount.stats($g) YIELD *"),
    ("局部聚类系数", "CALL gds.localClusteringCoefficient.stats($g) YIELD *"),
    ("Leiden 社区", "CALL gds.leiden.stats($g) YIELD *"),
    # 注意：GDS 2.13 注册名是 gds.fastRP（RP 大写），gds.list() 里显示的小写名调用会报 ProcedureNotFound
    ("FastRP 嵌入（stream）", "CALL gds.fastRP.stream($g, {embeddingDimension: 8}) YIELD nodeId RETURN count(*) AS c"),
    ("FastRP 嵌入（mutate）", "CALL gds.fastRP.mutate($g, {embeddingDimension: 8, mutateProperty: 'fastrpEmb'}) YIELD nodePropertiesWritten"),
    # 注：randomSeed 与 concurrency>1 不能同时给（GDS 会直接拒绝）
    ("KNN 向量近邻（基于上面嵌入）",
     "CALL gds.knn.stream($g, {nodeProperties: ['fastrpEmb'], topK: 5, sampleRate: 0.02, "
     "deltaThreshold: 0.1, maxIterations: 5, concurrency: 4}) "
     "YIELD node1, node2, similarity RETURN count(*) AS pairs, max(similarity) AS maxSim"),
    ("最短路 Dijkstra（无权重）", "CALL gds.shortestPath.dijkstra.stream($g, {sourceNode: $a, targetNode: $b}) YIELD path RETURN length(path) AS len"),
    ("K 最短路 Yen", "CALL gds.shortestPath.yens.stream($g, {sourceNode: $a, targetNode: $b, k: 3}) YIELD index RETURN count(*) AS c"),
    ("链路预测 Adamic-Adar（单对函数）",
     "MATCH (x),(y) WHERE id(x) = $a AND id(y) = $b "
     "RETURN gds.alpha.linkprediction.adamicAdar(x, y, "
     "{relationshipQuery: 'MATCH (a)-[r]->(b) RETURN id(a) AS source, id(b) AS target', "
     "direction: 'BOTH'}) AS score"),
]


def probe(graph=None) -> int:
    drv = gds._driver()
    try:
        with gds._session(drv) as s:
            graphs = [r["graphName"] for r in s.run(
                "CALL gds.graph.list() YIELD graphName RETURN graphName")]
            print("已有 GDS 投影:", graphs or "（无）")
            if not graphs:
                print("[SKIP] 无投影可测，先跑迁入或 load_graph_to_neo4j.py")
                return 0
            g = graph or graphs[0]
            print("使用投影:", g)

            # 投影 schema：是否 UNDIRECTED、是否带属性（决定 Dijkstra 权重/KNN 能否用）
            detail = None
            for r in s.run("CALL gds.graph.list() YIELD *"):
                d = dict(r)
                if d.get("graphName") == g:
                    detail = d
                    break
            if detail:
                print("节点数/关系数:", detail.get("nodeCount"), "/", detail.get("relationshipCount"))
                for k in ("nodeProperties", "relationshipProperties", "schema", "schemaWithOrientation"):
                    if k in detail:
                        v = detail[k]
                        print(f"{k}:", (list(v.keys()) if isinstance(v, dict) else v))
                print("可用字段:", sorted(detail.keys()))

            # FastRP 的真实注册名（gds.list() 与实际注册可能不一致）
            reg = [r["name"] for r in s.run(
                "SHOW PROCEDURES YIELD name WHERE toLower(name) CONTAINS 'fastrp' "
                "OR toLower(name) CONTAINS 'node2vec' RETURN name")]
            print("嵌入类实际注册过程:", reg or "（无）")
            print()
            # 投影内两个真实节点，供路径类算法使用
            pair = s.run(
                "MATCH (n) WHERE n.name IS NOT NULL "
                "RETURN id(n) AS a LIMIT 1"
            ).single()
            a = pair["a"]
            b = s.run("MATCH (n) WHERE id(n) <> $a RETURN id(n) AS b LIMIT 1", a=a).single()["b"]
            print()
            for label, cy in PROBES:
                try:
                    rec = s.run(cy, g=g, a=a, b=b).single()
                    print(f"  [可调用] {label}: {dict(rec) if rec else 'ok（无返回）'}")
                except Exception as e:
                    msg = " ".join(str(e).split())[:260]
                    low = msg.lower()
                    if "license" in low or "enterprise" in low:
                        kind = "授权限制(EE)"
                    elif "not found" in low or "no value" in low or "unknown procedure output" in low:
                        kind = "参数/前提不满足"
                    else:
                        kind = "其它"
                    print(f"  [{kind}] {label}: {msg}")
    finally:
        drv.close()
    print("PROBE DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
