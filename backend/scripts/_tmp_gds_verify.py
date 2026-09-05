# -*- coding: utf-8 -*-
"""临时脚本：重建 GDS 投影并跑 PageRank / Louvain 验证。用完即删。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neo4j import GraphDatabase

from config import settings

NAME = 'aviationMaintenance'
CORE_LABELS = ['部件', '故障模式', '故障原因', '系统']
# 归属系统/发生于用 NATURAL，组成反向（子->父），把父子链也纳入
CORE_RELS = {
    '表现为': 'NATURAL',
    '征兆为': 'NATURAL',
    '发生于': 'NATURAL',
    '归属系统': 'NATURAL',
    '组成': 'REVERSE',
    '上报代码': 'NATURAL',
}

drv = GraphDatabase.driver(
    settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
)
with drv.session(database=settings.NEO4J_DATABASE) as s:
    if s.run('CALL gds.graph.exists($n) YIELD exists', n=NAME).single()['exists']:
        s.run('CALL gds.graph.drop($n)', n=NAME)
        print('已删除旧投影')

    rel_map = ', '.join(
        '`%s`: {orientation: "%s"}' % (k, v) for k, v in CORE_RELS.items()
    )
    s.run(
        'CALL gds.graph.project($n, $labels, {%s})' % rel_map,
        n=NAME, labels=CORE_LABELS,
    )
    info = s.run(
        'CALL gds.graph.list($n) YIELD nodeCount, relationshipCount '
        'RETURN nodeCount, relationshipCount', n=NAME
    ).single()
    print('投影 %s：%s 节点 / %s 关系' % (NAME, f"{info['nodeCount']:,}", f"{info['relationshipCount']:,}"))

    print()
    print('== PageRank Top10（图中最"关键"的部件/故障模式）==')
    for rec in s.run(
        "CALL gds.pageRank.stream($n) YIELD nodeId, score "
        "RETURN gds.util.asNode(nodeId).name AS node, "
        "       [l IN labels(gds.util.asNode(nodeId)) WHERE l <> 'Entity'][0] AS typ, score "
        "ORDER BY score DESC LIMIT 10", n=NAME
    ):
        print('  %-28s %-10s %.4f' % (rec['node'][:28], rec['typ'], rec['score']))

    print()
    print('== Louvain 社区发现（故障模式聚类）==')
    for rec in s.run(
        "CALL gds.louvain.stream($n) YIELD nodeId, communityId "
        "WITH communityId, count(*) AS size, "
        "     collect(DISTINCT [l IN labels(gds.util.asNode(nodeId)) WHERE l <> 'Entity'][0])[0..3] AS types "
        "WHERE size > 3 "
        "RETURN communityId, size, types ORDER BY size DESC LIMIT 8", n=NAME
    ):
        print('  社区 %-8d %5d 节点  构成: %s' % (rec['communityId'], rec['size'], '/'.join(rec['types'])))

    print()
    print('== 度中心性 Top5（连接最密的实体）==')
    for rec in s.run(
        "MATCH (n) WHERE n.kb_id = '72f1ecec567e' "
        "OPTIONAL MATCH (n)-[r]-() "
        "RETURN n.name AS node, "
        "       [l IN labels(n) WHERE l <> 'Entity'][0] AS typ, count(r) AS deg "
        "ORDER BY deg DESC LIMIT 5"
    ):
        print('  %-32s %-10s %d 条边' % (str(rec['node'])[:32], rec['typ'], rec['deg']))

drv.close()
