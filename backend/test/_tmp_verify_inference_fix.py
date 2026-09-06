import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.graph_inference_service import _collect_rule_candidates
from providers.graph_store import gds

drv = gds._driver()
with gds._session(drv) as s:
    rows = s.run(
        "MATCH ()-[r]->() WHERE r.category_id = '0b9891ae7fab' "
        "RETURN type(r) AS t, count(*) AS c ORDER BY c DESC LIMIT 3"
    ).data()
drv.close()
print("top rel types:", [(r["t"], r["c"]) for r in rows])

r1, r2 = rows[0]["t"], rows[1]["t"]
rule = {"hops": [r1, r2], "rel": r1, "src_type": "ANY_TYPE_NOT_EXIST_XYZ"}
cands = _collect_rule_candidates("0b9891ae7fab", rule)
print("query OK, candidates:", len(cands))
