# -*- coding: utf-8 -*-
"""
把 gen_data.py 生成的实体同步到后端 SQLite (entities / relations 表)。
同步后实体管理页面才能显示数据；Neo4j/Milvus 已含数据，后端会按需读取图库/向量库。
"""
import os, json, uuid, sqlite3
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(__file__))
DATA = os.path.join(BASE, "data", "generated", "entities.json")
DB_PATH = os.path.join(BASE, "data", "knowsource.db")

CATEGORY_ID = "0b9891ae7fab"  # 民航维修领域本体

# 使用 Unicode 转义避免 Windows 源文件编码问题
t = lambda *a: ''.join(chr(int(x, 16)) for x in a)

TYPE_TO_ONTOLOGY = {
    "AircraftModel": t("673a", "578b"),        # 机型
    "Aircraft": t("822a", "7a7a", "5668"),     # 航空器
    "ATASystem": t("7cfb", "7edf"),            # 系统
    "Component": t("90e8", "4ef6"),            # 部件
    "Parameter": t("76d1", "63a7", "53c2", "6570"),  # 监控参数
    "FailureMode": t("6545", "969c", "6a21", "5f0f"),  # 故障模式
    "AirworthinessDirective": t("9002", "822a", "6587", "4ef6"),  # 适航文件
    "MaintenanceTask": t("7ef4", "4fee", "5de5", "5355"),  # 维修工单
    "Operator": t("5382", "5546"),             # 厂商
    "Engine": t("822a", "6750"),               # 航材
    "FaultCode": t("6545", "969c", "4ee3", "7801"),        # 故障代码
    "RootCause": t("6545", "969c", "539f", "56e0"),          # 故障原因
    "Symptom": t("6545", "969c", "5f81", "5146"),            # 故障征兆
    "Condition": t("5de5", "51b5"),                          # 工况
    "Action": t("7ef4", "4fee", "63aa", "65bd"),            # 维修措施
    "Tool": t("5de5", "88c5", "5de5", "5177"),               # 工装工具
    "Manual": t("624b", "518c"),                              # 手册
    "Personnel": t("4eba", "5458"),                           # 人员
    "Station": t("7ef4", "4fee", "7ad9"),                     # 维修站
    "MELDeferral": t("004d", "0045", "004c", "4fdd", "7559"),  # MEL保留
    "SparePart": t("822a", "6750"),                          # 航材
}

NAME_FIELD = {
    "AircraftModel": "code",
    "Aircraft": "tail",
    "ATASystem": "chapter",
    "Component": "name",
    "Parameter": "name",
    "FailureMode": "name",
    "AirworthinessDirective": "ad_number",
    "MaintenanceTask": "taskid",
    "Operator": "name",
    "Engine": "name",
    "FaultCode": "fc_id",
    "RootCause": "rc_id",
    "Symptom": "sym_id",
    "Condition": "cond_id",
    "Action": "act_id",
    "Tool": "tool_id",
    "Manual": "man_id",
    "Personnel": "per_id",
    "Station": "st_id",
    "MELDeferral": "mel_id",
    "SparePart": "sp_id",
}

def now():
    return datetime.now().isoformat()


def ensure_relation_defs(cur, ont_id):
    """
    补齐民航维修本体中缺失的关系定义与三元组约束。
    实体关系必须受本体关系约束，否则前端/后端校验会报错。
    """
    # 已存在的关系名 -> id
    rows = cur.execute(
        "SELECT id, name FROM ontology_relations WHERE category_id=?", (CATEGORY_ID,)
    ).fetchall()
    rel_name_to_id = {n: i for i, n in rows}

    def get_rel_id(name):
        if name not in rel_name_to_id:
            rid = uuid.uuid4().hex[:12]
            cur.execute(
                "INSERT INTO ontology_relations (id, category_id, name, description, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (rid, CATEGORY_ID, name, "", now(), now()),
            )
            rel_name_to_id[name] = rid
        return rel_name_to_id[name]

    # 已存在的约束 (source, relation, target)
    rows = cur.execute(
        "SELECT source_ontology_id, relation_id, target_ontology_id FROM ontology_relation_constraints WHERE category_id=?",
        (CATEGORY_ID,)
    ).fetchall()
    existing = {(s, r, t) for s, r, t in rows}

    # 需要的关系约束（补充缺失的）
    # 注意：中文关系名必须与 import_neo4j.py 完全一致
    required = [
        ("航空器", "运营方", "厂商"),
        ("航空器", "属于机型", "机型"),
        ("机型", "包含系统", "系统"),
        ("部件", "组成", "系统"),
        ("航空器", "包含部件", "部件"),
        ("机型", "使用发动机", "航材"),
        ("维修工单", "发生于航空器", "航空器"),
        ("维修工单", "针对系统", "系统"),
        ("维修工单", "涉及部件", "部件"),
        ("适航文件", "适用于", "机型"),
        # 新增
        ("维修工单", "报告故障", "故障代码"),
        ("维修工单", "执行措施", "维修措施"),
        ("维修工单", "使用工具", "工装工具"),
        ("维修工单", "指派给", "人员"),
        ("故障代码", "属于系统", "系统"),
        ("故障代码", "涉及部件", "部件"),
        ("故障代码", "由原因导致", "故障原因"),
        ("故障代码", "发生于工况", "工况"),
        ("故障代码", "表现为征兆", "故障征兆"),
        ("维修措施", "使用工具", "工装工具"),
        ("部件", "参考手册", "手册"),
        ("航空器", "驻场", "维修站"),
        ("航空器", "有保留项", "MEL保留"),
        ("人员", "所属站点", "维修站"),
        ("MEL保留", "关联故障", "故障代码"),
        ("航材", "替换", "部件"),
    ]
    added = 0
    for src_ont, rel_name, tgt_ont in required:
        sid = ont_id.get(src_ont)
        tid = ont_id.get(tgt_ont)
        rid = get_rel_id(rel_name)
        if not sid or not tid:
            print(f"SKIP constraint (missing ontology): {src_ont}-{rel_name}-{tgt_ont}")
            continue
        if (sid, rid, tid) not in existing:
            cur.execute(
                "INSERT INTO ontology_relation_constraints (id, category_id, source_ontology_id, relation_id, target_ontology_id, created_at) VALUES (?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], CATEGORY_ID, sid, rid, tid, now()),
            )
            added += 1
    print(f"ensured relation defs, added {added} new constraints")


def main():
    d = json.load(open(DATA, encoding="utf-8"))
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = lambda x: x.decode('utf-8', errors='ignore')
    conn.execute("PRAGMA foreign_keys = OFF")
    cur = conn.cursor()

    # 1. 获取 ontology 映射
    rows = cur.execute(
        "SELECT id, name FROM ontologies WHERE category_id=?", (CATEGORY_ID,)
    ).fetchall()
    ont_name_to_id = {n: i for i, n in rows}
    type_to_ontid = {et: ont_name_to_id[oname] for et, oname in TYPE_TO_ONTOLOGY.items()}

    # 2. 补齐本体关系约束
    ensure_relation_defs(cur, ont_name_to_id)

    # 3. 清理旧数据
    cur.execute("DELETE FROM relations WHERE source_entity_id IN (SELECT id FROM entities WHERE ontology_id IN (SELECT id FROM ontologies WHERE category_id=?))", (CATEGORY_ID,))
    cur.execute("DELETE FROM entities WHERE ontology_id IN (SELECT id FROM ontologies WHERE category_id=?)", (CATEGORY_ID,))
    print("cleaned old entities/relations")

    # 4. 实体插入
    entity_rows = []
    type_id_map = {}  # (type, source_id) -> db_id
    _name_counts = {}  # (etype, name) -> count，用于中文名去重

    def insert_items(etype, items, name_key=None):
        key = name_key or NAME_FIELD[etype]
        for it in items:
            eid = uuid.uuid4().hex[:12]
            if isinstance(it, str):
                display_name = it
                props = {}
                source_id = it
            else:
                display_name = it.get(key, "") or it.get("name", "")
                props = {k: v for k, v in it.items() if k != key}
                if name_key:
                    source_id = it[name_key]
                else:
                    source_id = it.get("acid") or it.get("taskid") or it.get("ad_number") or it.get("comp_id") or it.get("chapter") or it.get("code") or it.get("fid") or it.get("oid") or display_name
            # name 用全局唯一 source_id，避免 SQLite 唯一键冲突；维修工单用中文名作为实体名，重复时加序号
            name = str(source_id)
            if etype == "MaintenanceTask" and isinstance(it, dict) and it.get("name"):
                name = it["name"]
                cnt = _name_counts.get((etype, name), 0) + 1
                _name_counts[(etype, name)] = cnt
                if cnt > 1:
                    name = f"{name} ({cnt})"
            desc = it.get("name", "") if isinstance(it, dict) and "name" in it else (display_name if not isinstance(it, str) else "")
            entity_rows.append((
                eid, "", type_to_ontid[etype], etype, str(name),
                desc,
                json.dumps(props, ensure_ascii=False),
                "", "", now(), now()
            ))
            type_id_map[(etype, source_id)] = eid

    insert_items("Operator", d["operators"], name_key="oid")
    # Engine 去重
    insert_items("Engine", sorted(set(d["engines"])))
    insert_items("AircraftModel", d["models"])
    insert_items("ATASystem", d["ata"])
    insert_items("Component", d["components"])
    insert_items("Parameter", d["parameters"])
    insert_items("FailureMode", d["failure_modes"])
    insert_items("Aircraft", d["aircraft"])
    # AD 按编号去重 (同一AD可能出现在多个机型目录)
    seen_ad = set()
    ads_unique = []
    for a in d["ads"]:
        if a["ad_number"] not in seen_ad:
            seen_ad.add(a["ad_number"])
            ads_unique.append(a)
    insert_items("AirworthinessDirective", ads_unique)
    # MaintenanceTask 用 taskid 作为唯一标识，description 用中文 name
    insert_items("MaintenanceTask", d["tasks"], name_key="taskid")
    # 新增本体实体
    insert_items("FaultCode", d.get("fault_codes", []), name_key="fc_id")
    insert_items("RootCause", d.get("root_causes", []), name_key="rc_id")
    insert_items("Symptom", d.get("symptoms", []), name_key="sym_id")
    insert_items("Condition", d.get("conditions", []), name_key="cond_id")
    insert_items("Action", d.get("actions", []), name_key="act_id")
    insert_items("Tool", d.get("tools", []), name_key="tool_id")
    insert_items("Manual", d.get("manuals", []), name_key="man_id")
    insert_items("Personnel", d.get("personnel", []), name_key="per_id")
    insert_items("Station", d.get("stations", []), name_key="st_id")
    insert_items("MELDeferral", d.get("mel_deferrals", []), name_key="mel_id")
    insert_items("SparePart", d.get("spare_parts", []), name_key="sp_id")

    cur.executemany(
        """
        INSERT INTO entities (id, kb_id, ontology_id, entity_type, name, description, properties, source_file_id, source_chunk_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        entity_rows,
    )
    print(f"inserted {len(entity_rows)} entities")

    # 5. 关系插入（核心关系，避免过于膨胀）
    relation_rows = []
    rid = 0
    def add(sid, tid, desc, rtype):
        nonlocal rid
        if sid and tid:
            rid += 1
            relation_rows.append({
                "id": f"R{rid:08d}",
                "kb_id": "",
                "relation_def_id": "",
                "relation_type": rtype,
                "source_entity_id": sid,
                "target_entity_id": tid,
                "description": desc,
                "source_file_id": "",
                "source_chunk_id": "",
                "created_at": now(),
                "updated_at": now(),
            })

    # 飞机->机型, 飞机->厂商
    for r in d["aircraft"]:
        add(type_id_map[("Aircraft", r["acid"])], type_id_map[("AircraftModel", r["model"])], "属于机型", "属于机型")
        add(type_id_map[("Aircraft", r["acid"])], type_id_map[("Operator", "OP_01")], "运营方", "运营方")

    # 机型->系统, 机型->发动机
    seen_ms = set()
    for c in d["components"]:
        sid = type_id_map[("AircraftModel", c["model"])]
        tid = type_id_map[("ATASystem", c["ata_chapter"])]
        if (sid, tid) not in seen_ms:
            seen_ms.add((sid, tid))
            add(sid, tid, "包含系统", "包含系统")
    for m in d["models"]:
        sid = type_id_map[("AircraftModel", m["code"])]
        for eng in m["engine"].split("/"):
            tid = type_id_map[("Engine", eng)]
            add(sid, tid, "使用发动机", "使用发动机")

    # 部件->组成->系统 (符合本体约束)
    seen_comp_sys = set()
    for c in d["components"]:
        sid = type_id_map[("Component", c["comp_id"])]
        tid = type_id_map[("ATASystem", c["ata_chapter"])]
        if (sid, tid) not in seen_comp_sys:
            seen_comp_sys.add((sid, tid))
            add(sid, tid, "组成", "组成")

    # 飞机->包含部件(去重)
    seen_ac = set()
    for t in d["tasks"]:
        sid = type_id_map[("Aircraft", t["acid"])]
        tid = type_id_map[("Component", t["comp_id"])]
        if (sid, tid) not in seen_ac:
            seen_ac.add((sid, tid))
            add(sid, tid, "包含部件", "包含部件")

    # 工卡->飞机/系统/部件 (抽样: 每架飞机最多20条，避免28万关系)
    sampled_by_ac = {}
    for t in d["tasks"]:
        sampled_by_ac.setdefault(t["acid"], []).append(t)
    for acid, tasks in sampled_by_ac.items():
        for t in tasks[:20]:
            sid = type_id_map[("MaintenanceTask", t["taskid"])]
            add(sid, type_id_map[("Aircraft", t["acid"])], "发生于航空器", "发生于航空器")
            add(sid, type_id_map[("ATASystem", t["ata_chapter"])], "针对系统", "针对系统")
            add(sid, type_id_map[("Component", t["comp_id"])], "涉及部件", "涉及部件")

    # AD->机型 (按 source_model 映射，去重)
    seen_ad_model = set()
    for ad in d["ads"]:
        sid = type_id_map[("AirworthinessDirective", ad["ad_number"])]
        for m in d["models"]:
            if ad.get("source_model") and ad["source_model"] in m["family"]:
                tid = type_id_map[("AircraftModel", m["code"])]
                if (sid, tid) not in seen_ad_model:
                    seen_ad_model.add((sid, tid))
                    add(sid, tid, "适用于", "适用于")

    # 新增关系（按本地 ID 映射，去重）
    seen_rel = set()
    for r in d.get("relations", []):
        sid = type_id_map.get((r["start_type"], r["start_id"]))
        tid = type_id_map.get((r["end_type"], r["end_id"]))
        key = (sid, tid, r["rel_type"])
        if sid and tid and key not in seen_rel:
            seen_rel.add(key)
            add(sid, tid, r["rel_type"], r["rel_type"])

    cur.executemany(
        """
        INSERT INTO relations (id, kb_id, relation_def_id, relation_type, source_entity_id, target_entity_id, description, source_file_id, source_chunk_id, created_at, updated_at)
        VALUES (:id, :kb_id, :relation_def_id, :relation_type, :source_entity_id, :target_entity_id, :description, :source_file_id, :source_chunk_id, :created_at, :updated_at)
        """,
        relation_rows,
    )
    print(f"inserted {len(relation_rows)} relations")

    conn.commit()
    conn.close()
    print("sync done")


if __name__ == "__main__":
    main()
