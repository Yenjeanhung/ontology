import json, urllib.request, re

BASE = "http://localhost:8000"
CID = "0b9891ae7fab"

def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=20) as r:
        return json.loads(r.read().decode())

CJK = re.compile(r"[\u4e00-\u9fff]")
onts = get(f"/api/ontology-categories/{CID}/ontologies")
print("本体数:", len(onts))
for o in onts:
    attrs = get(f"/api/ontology-categories/{CID}/ontologies/{o['id']}/attributes")
    bad = [a for a in attrs if CJK.search(a.get("code") or "")]
    if not attrs:
        continue
    print(f"\n【{o['name']}】{o['id']}  属性数={len(attrs)}  编码含中文的={len(bad)}")
    for a in attrs:
        flag = "  <== 编码含中文" if CJK.search(a.get("code") or "") else ""
        print(f"   {a.get('name')} / {a.get('code')}{flag}")
