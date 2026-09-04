# -*- coding: utf-8 -*-
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from neo4j import GraphDatabase
from pymilvus import connections, Collection
from sentence_transformers import SentenceTransformer

print("="*60)
print("1) Neo4j 多跳查询: 国航某机的工卡->系统->部件")
URI, AUTH = "bolt://localhost:7687", ("neo4j", "ontology123")
drv = GraphDatabase.driver(URI, auth=AUTH)
with drv.session() as s:
    r = s.run("""
      MATCH (op:Operator {name:'中国国航'})<-[:OPERATED_BY]-(a:Aircraft)
      MATCH (a)-[:HAS_MODEL]->(m:AircraftModel)
      MATCH (a)-[:HAS_COMPONENT]->(c:Component)
      RETURN a.tail AS tail, m.code AS model, count(DISTINCT c) AS comps
      ORDER BY comps DESC LIMIT 3
    """).data()
    for x in r: print("  ", x)
    # GDS 图计算: PageRank on MaintenanceTask->Component
    print("\n2) GDS 图计算示例 (PageRank 部件重要度)")
    s.run("CALL gds.graph.drop('g', false) YIELD graphName")
    s.run("""
      CALL gds.graph.project('g',
        'Component',
        {USES_COMPONENT: {type:'USES_COMPONENT', orientation:'REVERSE'}})
    """)
    pr = s.run("""
      CALL gds.pageRank.stream('g', {maxIterations:20, dampingFactor:0.85})
      YIELD nodeId, score
      RETURN gds.util.asNode(nodeId).name AS comp, gds.util.asNode(nodeId).pn AS pn, score
      ORDER BY score DESC LIMIT 5
    """).data()
    for x in pr: print("  ", x)
    s.run("CALL gds.graph.drop('g', false) YIELD graphName")
drv.close()

print("\n3) Milvus 语义检索: '发动机振动超限故障'")
connections.connect(alias="default", host="localhost", port="19530")
col = Collection("aviation_maintenance_entities"); col.load()
model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
q = model.encode(["飞机发动机振动超限故障诊断维修"], normalize_embeddings=True).tolist()
res = col.search(q, "embedding", {"metric_type":"COSINE","params":{"ef":64}}, limit=5,
                 output_fields=["entity_type","entity_id","content"])
for hit in res[0]:
    print(f"  score={hit.score:.3f} [{hit.entity_type}] {hit.entity_id}: {hit.content[:50]}")
print("\nALL VERIFIED OK")
