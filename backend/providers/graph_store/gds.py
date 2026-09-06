"""M0 GDS 基础层：图计算（GDS）与图迁入共享的投影管理工具。

设计来源：《图迁入与图计算推理_功能设计》3 节（与《GDS实体链接与知识补全》共建）。
本模块是 **Neo4j 专属能力**，不属于 GraphStoreAdapter 抽象（Kùzu 无 GDS），
调用方（graph_sync_service / 未来的 graph_analysis_service）需自行确认
`settings.GRAPH_STORE_PROVIDER == "neo4j"`。

要点
====
- 投影 scope 泛化：``cat_<category_id>``（本体类别维度，分析图单元）。
  姊妹设计中的 ``kg_<kb_id>``（KB 维度）共用同一套白名单与创建逻辑。
- cypher 投影不支持参数化 → ``category_id`` 必须过 ``^[a-f0-9]{12}$`` 白名单后内联。
- 社区版 GDS 并发 = 1 → 重建类操作（drop/create projection）统一持 ``_gds_lock`` 串行；
  只读算法（stream）不持锁，由 GDS 自身排队。
- 通用投影（中心性/社区/相似度）使用 UNDIRECTED 全语义关系；
  传播/路径类分析不走投影，直接 Cypher 可变长路径（见设计 3.3）。
"""
from __future__ import annotations

import json
import logging
import re
import threading

from config import settings

logger = logging.getLogger(__name__)

# 分析图节点/关系上的类别标记属性（迁入服务写入，投影按它圈范围）
CATEGORY_PROP = "category_id"

# category_id / kb_id 白名单：12 位小写 hex（uuid4().hex[:12]），防 cypher 注入
_SCOPE_ID_RE = re.compile(r"^[a-f0-9]{12}$")

# 社区版 GDS 并发 = 1：重建投影类重操作全局串行
_gds_lock = threading.Lock()

# GDS 可用性探测缓存（None = 未探测）
_gds_available: bool | None = None


class GdsUnavailableError(RuntimeError):
    """Neo4j 未安装 GDS 插件（或图库不是 Neo4j）。"""


def validate_scope_id(scope_id: str) -> str:
    """校验投影 scope id（category_id / kb_id），防 cypher 注入。

    cypher 投影的查询串不支持绑定参数，id 必须内联，因此过 hex 白名单。
    """
    if not _SCOPE_ID_RE.match(scope_id or ""):
        raise ValueError(f"非法的 scope id：{scope_id!r}（需 12 位小写 hex）")
    return scope_id


def category_projection_name(category_id: str) -> str:
    """本体类别维度的投影名：cat_<category_id>。"""
    return f"cat_{validate_scope_id(category_id)}"


def _driver():
    if settings.GRAPH_STORE_PROVIDER != "neo4j":
        raise GdsUnavailableError("图存储不是 Neo4j，无法使用 GDS")
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )


def _session(driver):
    return driver.session(database=settings.NEO4J_DATABASE)


def gds_available(refresh: bool = False) -> bool:
    """探测 GDS 插件是否可用（结果缓存；连接失败按不可用处理）。"""
    global _gds_available
    if _gds_available is not None and not refresh:
        return _gds_available
    if settings.GRAPH_STORE_PROVIDER != "neo4j":
        _gds_available = False
        return False
    try:
        driver = _driver()
        try:
            with _session(driver) as s:
                cnt = s.run(
                    "SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'gds.' "
                    "RETURN count(*) AS c"
                ).single()["c"]
            _gds_available = cnt > 0
        finally:
            driver.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("GDS 探测失败（按不可用处理）：%s", exc)
        _gds_available = False
    return _gds_available


def projection_exists(name: str) -> bool:
    driver = _driver()
    try:
        with _session(driver) as s:
            rec = s.run(
                "CALL gds.graph.exists($name) YIELD exists RETURN exists", name=name
            ).single()
        return bool(rec and rec["exists"])
    finally:
        driver.close()


def drop_projection(name: str) -> bool:
    """删除投影（存在时）。返回是否实际删除。"""
    driver = _driver()
    try:
        with _session(driver) as s:
            rec = s.run(
                "CALL gds.graph.exists($name) YIELD exists RETURN exists", name=name
            ).single()
            if not (rec and rec["exists"]):
                return False
            s.run("CALL gds.graph.drop($name)", name=name)
            return True
    finally:
        driver.close()


def projection_stats(name: str) -> dict | None:
    """投影统计（不存在返回 None）。"""
    driver = _driver()
    try:
        with _session(driver) as s:
            rec = s.run(
                "CALL gds.graph.exists($name) YIELD exists RETURN exists", name=name
            ).single()
            if not (rec and rec["exists"]):
                return None
            info = s.run(
                "CALL gds.graph.list($name) YIELD nodeCount, relationshipCount "
                "RETURN nodeCount, relationshipCount", name=name,
            ).single()
            return {
                "name": name,
                "node_count": info["nodeCount"],
                "relationship_count": info["relationshipCount"],
            }
    finally:
        driver.close()


# 分析图内部通用关系排除：运行时业务抽取图的关系类型不属于分析图语义关系。
# （业务图节点不带 category_id 属性，本就圈不进来；此处硬编码双保险，防人工双写混入。）
EXCLUDED_REL_TYPES = ("RELATES", "MENTIONS", "HAS_RELATION", "RELATION_SOURCE",
                      "RELATION_TARGET", "HAS_CHUNK", "HAS_DOCUMENT", "NEXT_CHUNK")

_EXCLUDED_LIST = ", ".join(f"'{t}'" for t in EXCLUDED_REL_TYPES)

# cypher 投影不支持参数化：category_id 已过 hex 白名单，安全内联
_NODE_QUERY_TPL = "MATCH (e:Entity {category_id: '{cid}'}) RETURN id(e) AS id"
_REL_QUERY_TPL = (
    "MATCH (a:Entity {category_id: '{cid}'})-[r]->(b:Entity) "
    f"WHERE NOT type(r) IN [{_EXCLUDED_LIST}] AND b.category_id = '{{cid}}' "
    "RETURN id(a) AS source, id(b) AS target, type(r) AS type"
)


def ensure_category_projection(category_id: str) -> dict:
    """重建本体类别维度的 GDS 投影（drop + create），返回统计。

    - 持 ``_gds_lock``：社区版 GDS 并发 = 1，重建类操作必须串行；
    - cypher 投影（gds.graph.project.cypher）：按 ``category_id`` 属性圈节点/关系，
      UNDIRECTED 全语义关系（中心性/社区/相似度通用，见设计 3.3）；
    - scope id 已过白名单，可安全内联。
    """
    if not gds_available():
        raise GdsUnavailableError("Neo4j 未安装 GDS 插件，无法创建投影")
    name = category_projection_name(category_id)
    node_query = _NODE_QUERY_TPL.format(cid=category_id)
    rel_query = _REL_QUERY_TPL.format(cid=category_id)
    with _gds_lock:
        driver = _driver()
        try:
            with _session(driver) as s:
                # exists=false 时 drop 静默跳过（第二参 failIfMissing=false）
                s.run("CALL gds.graph.drop($n, false)", n=name)
                s.run(
                    "CALL gds.graph.project.cypher($n, $node_query, $rel_query, "
                    "{relationshipQuery: 'UNDIRECTED'})",
                    n=name, node_query=node_query, rel_query=rel_query,
                )
                info = s.run(
                    "CALL gds.graph.list($n) YIELD nodeCount, relationshipCount "
                    "RETURN nodeCount, relationshipCount", n=name,
                ).single()
                stats = {
                    "name": name,
                    "node_count": info["nodeCount"],
                    "relationship_count": info["relationshipCount"],
                }
            logger.info("GDS 投影已重建：%s", json.dumps(stats, ensure_ascii=False))
            return stats
        finally:
            driver.close()
