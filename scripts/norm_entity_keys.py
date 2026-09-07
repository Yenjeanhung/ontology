"""把实体 properties 里以「属性中文名」为键的字段，统一改为「属性编码」为键。
中文名仍由本体属性定义的 name 承载，不动实体值。
"""
import json, re, urllib.request

BASE = "http://localhost:8000"
CJK = re.compile(r"[\u4e00-\u9fff]")


def get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as r:
        return json.loads(r.read().decode())


def req(path, method, data):
    body = json.dumps(data, ensure_ascii=False).encode()
    r = urllib.request.Request(f"{BASE}{path}", data=body, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


def rename_map(attrs):
    """属性中文名 -> 属性编码（仅名称含中文且与编码不同时）"""
    m = {}
    for a in attrs:
        name = (a.get("name") or "").strip()
        code = (a.get("code") or "").strip()
        if name and code and name != code and CJK.search(name):
            m[name] = code
    return m


total_changed = 0
for cat in get("/api/ontology-categories"):
    cid = cat["id"]
    for o in get(f"/api/ontology-categories/{cid}/ontologies"):
        oid = o["id"]
        attrs = get(f"/api/ontology-categories/{cid}/ontologies/{oid}/attributes")
        rm = rename_map(attrs)
        if not rm:
            continue
        page, changed = 1, 0
        while True:
            data = get(f"/api/entities?ontology_id={oid}&page={page}&page_size=200")
            items = data.get("items") or []
            if not items:
                break
            for it in items:
                props = it.get("properties") or {}
                if not props:
                    continue
                new, diff = {}, False
                for k, v in props.items():
                    nk = rm.get((k or "").strip(), k)
                    if nk != k:
                        diff = True
                    if nk in new:
                        # 编码键已存在：保留非空值
                        if new[nk] in (None, "") and v not in (None, ""):
                            new[nk] = v
                    else:
                        new[nk] = v
                if diff:
                    req(f"/api/entities/{it['id']}", "PUT", {"properties": new})
                    changed += 1
            if len(items) < 200:
                break
            page += 1
        if changed:
            print(f"[{cat['name']}] {o['name']}: 规范化 {changed} 个实体")
            total_changed += changed

print("合计更新:", total_changed)
