import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from providers.graph_store import gds

CID = "0b9891ae7fab"
P = f"cat_{CID}"

drv = gds._driver()
with gds._session(drv) as s:
    # 0.2 投影 drop + create（手册逐字）
    s.run("CALL gds.graph.drop($n, false)", n=P)
    rec = s.run(
        "CALL gds.graph.project.cypher($n, $nq, $rq) "
        "YIELD nodeCount, relationshipCount",
        n=P,
        nq=f"MATCH (e:Entity {{category_id: '{CID}'}}) RETURN id(e) AS id",
        rq=(f"MATCH (a:Entity {{category_id: '{CID}'}})-[r]-(b:Entity) "
            "WHERE NOT type(r) IN ['RELATES','MENTIONS','HAS_RELATION','RELATION_SOURCE',"
            "'RELATION_TARGET','HAS_CHUNK','HAS_DOCUMENT','NEXT_CHUNK'] "
            f"AND b.category_id = '{CID}' "
            "RETURN id(a) AS source, id(b) AS target, type(r) AS type"),
    ).single()
    print("0.2 projection:", dict(rec))

    # 1.1 pagerank stream
    rows = s.run(
        f"CALL gds.pageRank.stream('{P}') YIELD nodeId, score "
        "WITH gds.util.asNode(nodeId) AS n, score "
        "RETURN n.name AS name, score ORDER BY score DESC LIMIT 3").data()
    print("1.1 pagerank:", rows)

    # 2.2 betweenness 近似 stream
    rows = s.run(
        f"CALL gds.betweenness.stream('{P}', {{samplingSize: 10000, samplingSeed: 42}}) "
        "YIELD nodeId, score WITH gds.util.asNode(nodeId) AS n, score "
        "RETURN n.name AS name, score ORDER BY score DESC LIMIT 3").data()
    print("2.2 betweenness(approx):", rows)

    # 3.1 louvain 社区聚合
    rows = s.run(
        f"CALL gds.louvain.stream('{P}') YIELD nodeId, communityId "
        "WITH gds.util.asNode(nodeId) AS n, communityId "
        "RETURN communityId, count(*) AS size, collect(n.name)[..3] AS sample "
        "ORDER BY size DESC LIMIT 3").data()
    print("3.1 louvain:", rows)

    # 5 degree 纯 Cypher
    rows = s.run(
        "MATCH (e:Entity {category_id: $cid})-[r]-(o) "
        "WHERE NOT type(r) IN ['RELATES','MENTIONS','HAS_RELATION','RELATION_SOURCE',"
        "'RELATION_TARGET','HAS_CHUNK','HAS_DOCUMENT','NEXT_CHUNK'] "
        "AND o.category_id = $cid "
        "RETURN e.name AS name, count(r) AS degree ORDER BY degree DESC LIMIT 3",
        cid=CID).data()
    print("5. degree:", rows)
drv.close()
print("ALL MANUAL CYPHER VERIFIED")
