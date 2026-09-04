# -*- coding: utf-8 -*-
"""将生成的民航维修实体灌入 Neo4j (节点 + 关系)"""
import os, json
from neo4j import GraphDatabase

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "generated", "entities.json")
URI = "bolt://localhost:7687"
AUTH = ("neo4j", "ontology123")

BATCH = 2000

def main():
    d = json.load(open(DATA, encoding="utf-8"))
    drv = GraphDatabase.driver(URI, auth=AUTH)
    with drv.session() as s:
        # 约束
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:AircraftModel) REQUIRE n.code IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:ATASystem) REQUIRE n.chapter IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Component) REQUIRE n.comp_id IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Aircraft) REQUIRE n.acid IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:MaintenanceTask) REQUIRE n.taskid IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:AirworthinessDirective) REQUIRE n.ad_number IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Operator) REQUIRE n.oid IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Parameter) REQUIRE n.pid IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:FailureMode) REQUIRE n.fid IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Engine) REQUIRE n.name IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:FaultCode) REQUIRE n.fc_id IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:RootCause) REQUIRE n.rc_id IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Symptom) REQUIRE n.sym_id IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Condition) REQUIRE n.cond_id IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Action) REQUIRE n.act_id IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Tool) REQUIRE n.tool_id IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Manual) REQUIRE n.man_id IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Personnel) REQUIRE n.per_id IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:Station) REQUIRE n.st_id IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:MELDeferral) REQUIRE n.mel_id IS UNIQUE")
        s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (n:SparePart) REQUIRE n.sp_id IS UNIQUE")
        print("constraints created")

        def batch_run(q, items):
            for i in range(0, len(items), BATCH):
                s.run(q, {"rows": items[i:i+BATCH]})

        # 节点
        batch_run("UNWIND $rows AS r MERGE (n:Operator {oid:r.oid}) SET n.name=r.name", d["operators"])
        batch_run("UNWIND $rows AS r MERGE (n:Engine {name:r.name})", [{"name": e} for e in d["engines"]])
        batch_run("UNWIND $rows AS r MERGE (n:AircraftModel {code:r.code}) SET n.family=r.family,n.manufacturer=r.manufacturer,n.engine=r.engine,n.type=r.type", d["models"])
        batch_run("UNWIND $rows AS r MERGE (n:ATASystem {chapter:r.chapter}) SET n.name=r.name", d["ata"])
        batch_run("UNWIND $rows AS r MERGE (n:Component {comp_id:r.comp_id}) SET n.name=r.name,n.pn=r.pn,n.ata_chapter=r.ata_chapter,n.model=r.model,n.manufacturer=r.manufacturer,n.mtbf_hours=r.mtbf_hours", d["components"])
        batch_run("UNWIND $rows AS r MERGE (n:Parameter {pid:r.pid}) SET n.name=r.name,n.symbol=r.symbol", d["parameters"])
        batch_run("UNWIND $rows AS r MERGE (n:FailureMode {fid:r.fid}) SET n.name=r.name", d["failure_modes"])
        batch_run("UNWIND $rows AS r MERGE (n:MaintenanceTask {taskid:r.taskid}) SET n.name=r.name,n.tail=r.tail,n.model=r.model,n.ata_chapter=r.ata_chapter,n.task_type=r.task_type,n.comp_name=r.comp_name,n.ad_number=r.ad_number,n.interval_days=r.interval_days,n.due_date=r.due_date,n.status=r.status,n.manhours=r.manhours,n.acid=r.acid,n.comp_id=r.comp_id", d["tasks"])
        # 飞机 (含 operator / model 关系在后面)
        batch_run("UNWIND $rows AS r MERGE (n:Aircraft {acid:r.acid}) SET n.tail=r.tail,n.flight_no=r.flight_no,n.model=r.model,n.manufacturer=r.manufacturer,n.engine=r.engine,n.msn=r.msn,n.operator=r.operator,n.entry_year=r.entry_year,n.total_cycles=r.total_cycles,n.total_fh=r.total_fh", d["aircraft"])
        # AD (真实)
        batch_run("UNWIND $rows AS r MERGE (n:AirworthinessDirective {ad_number:r.ad_number}) SET n.title=r.title,n.ata_chapter=r.ata_chapter,n.applicability=r.applicability,n.status=r.status,n.source_model=r.source_model", d["ads"])
        # 新增本体实体
        batch_run("UNWIND $rows AS r MERGE (n:FaultCode {fc_id:r.fc_id}) SET n.code=r.code,n.name=r.name,n.ata_chapter=r.ata_chapter,n.model=r.model,n.comp_id=r.comp_id,n.severity=r.severity", d.get("fault_codes", []))
        batch_run("UNWIND $rows AS r MERGE (n:RootCause {rc_id:r.rc_id}) SET n.name=r.name,n.category=r.category", d.get("root_causes", []))
        batch_run("UNWIND $rows AS r MERGE (n:Symptom {sym_id:r.sym_id}) SET n.name=r.name,n.level=r.level", d.get("symptoms", []))
        batch_run("UNWIND $rows AS r MERGE (n:Condition {cond_id:r.cond_id}) SET n.name=r.name,n.description=r.description", d.get("conditions", []))
        batch_run("UNWIND $rows AS r MERGE (n:Action {act_id:r.act_id}) SET n.name=r.name,n.action_type=r.action_type", d.get("actions", []))
        batch_run("UNWIND $rows AS r MERGE (n:Tool {tool_id:r.tool_id}) SET n.name=r.name,n.tool_type=r.tool_type,n.calibration_required=r.calibration_required", d.get("tools", []))
        batch_run("UNWIND $rows AS r MERGE (n:Manual {man_id:r.man_id}) SET n.doc_type=r.doc_type,n.model=r.model,n.name=r.name,n.revision=r.revision", d.get("manuals", []))
        batch_run("UNWIND $rows AS r MERGE (n:Personnel {per_id:r.per_id}) SET n.name=r.name,n.role=r.role,n.license=r.license,n.station=r.station", d.get("personnel", []))
        batch_run("UNWIND $rows AS r MERGE (n:Station {st_id:r.st_id}) SET n.name=r.name,n.station_type=r.station_type,n.city=r.city", d.get("stations", []))
        batch_run("UNWIND $rows AS r MERGE (n:MELDeferral {mel_id:r.mel_id}) SET n.deferral_code=r.deferral_code,n.item=r.item,n.category=r.category,n.tail=r.tail,n.due_date=r.due_date", d.get("mel_deferrals", []))
        batch_run("UNWIND $rows AS r MERGE (n:SparePart {sp_id:r.sp_id}) SET n.name=r.name,n.pn=r.pn,n.ata_chapter=r.ata_chapter,n.model=r.model", d.get("spare_parts", []))
        print("nodes imported")

        # 关系 (使用中文名，严格对齐本体三元组约束)
        batch_run("UNWIND $rows AS r MATCH (a:Aircraft {acid:r.acid}) MATCH (o:Operator {oid:r.operator}) MERGE (a)-[:运营方]->(o)", d["aircraft"])
        batch_run("UNWIND $rows AS r MATCH (a:Aircraft {acid:r.acid}) MATCH (m:AircraftModel {code:r.model}) MERGE (a)-[:属于机型]->(m)", d["aircraft"])
        model_systems = []
        for mm in d["models"]:
            chs = set(c["ata_chapter"] for c in d["components"] if c["model"] == mm["code"])
            for ch in chs:
                model_systems.append({"code": mm["code"], "ata": ch})
        batch_run("UNWIND $rows AS r MATCH (m:AircraftModel {code:r.code}) MATCH (s:ATASystem {chapter:r.ata}) MERGE (m)-[:包含系统]->(s)", model_systems)
        # 部件->组成->系统 (符合本体约束: 部件 组成 系统)
        batch_run("UNWIND $rows AS r MATCH (c:Component {comp_id:r.comp_id}) MATCH (s:ATASystem {chapter:r.ata_chapter}) MERGE (c)-[:组成]->(s)", d["components"])
        # 飞机->包含部件->部件
        seen_ac_comp = set()
        ac_comp_rows = []
        for t in d["tasks"]:
            key = (t["acid"], t["comp_id"])
            if key not in seen_ac_comp:
                seen_ac_comp.add(key)
                ac_comp_rows.append({"acid": t["acid"], "comp_id": t["comp_id"]})
        batch_run("UNWIND $rows AS r MATCH (a:Aircraft {acid:r.acid}) MATCH (c:Component {comp_id:r.comp_id}) MERGE (a)-[:包含部件]->(c)", ac_comp_rows)
        powered = []
        for m in d["models"]:
            for eng in m["engine"].split("/"):
                powered.append({"code": m["code"], "engine": eng})
        batch_run("UNWIND $rows AS r MATCH (a:AircraftModel {code:r.code}) MATCH (e:Engine {name:r.engine}) MERGE (a)-[:使用发动机]->(e)", powered)
        # 工卡关系
        batch_run("UNWIND $rows AS r MATCH (t:MaintenanceTask {taskid:r.taskid}) MATCH (a:Aircraft {acid:r.acid}) MERGE (t)-[:发生于航空器]->(a)", d["tasks"])
        batch_run("UNWIND $rows AS r MATCH (t:MaintenanceTask {taskid:r.taskid}) MATCH (s:ATASystem {chapter:r.ata_chapter}) MERGE (t)-[:针对系统]->(s)", d["tasks"])
        batch_run("UNWIND $rows AS r MATCH (t:MaintenanceTask {taskid:r.taskid}) MATCH (c:Component {comp_id:r.comp_id}) MERGE (t)-[:涉及部件]->(c)", d["tasks"])
        # AD -> 机型 (按 source_model 映射)
        batch_run("UNWIND $rows AS r MATCH (ad:AirworthinessDirective {ad_number:r.ad_number}) MATCH (m:AircraftModel) WHERE m.family CONTAINS r.source_model OR m.code CONTAINS r.source_model MERGE (ad)-[:适用于]->(m)", d["ads"])
        # 新增关系
        rels = d.get("relations", [])
        if rels:
            # 按关系类型批量导入，避免单条 Cypher 过长
            rels_by_type = {}
            for r in rels:
                rels_by_type.setdefault(r["rel_type"], []).append({
                    "start_id": r["start_id"], "end_id": r["end_id"]
                })
            for rel_type, rows in rels_by_type.items():
                label_map = {
                    "WorkOrder": "MaintenanceTask",
                    "ATASystem": "ATASystem",
                    "Component": "Component",
                    "Aircraft": "Aircraft",
                    "FaultCode": "FaultCode",
                    "RootCause": "RootCause",
                    "Symptom": "Symptom",
                    "Condition": "Condition",
                    "Action": "Action",
                    "Tool": "Tool",
                    "Manual": "Manual",
                    "Personnel": "Personnel",
                    "Station": "Station",
                    "MELDeferral": "MELDeferral",
                    "SparePart": "SparePart",
                }
                # 按 start/end label 分组
                groups = {}
                for r in rels:
                    if r["rel_type"] != rel_type:
                        continue
                    key = (label_map.get(r["start_type"], r["start_type"]), label_map.get(r["end_type"], r["end_type"]))
                    groups.setdefault(key, []).append({"start_id": r["start_id"], "end_id": r["end_id"]})
                for (sl, el), g_rows in groups.items():
                    start_field = "taskid" if sl == "MaintenanceTask" else "fc_id" if sl == "FaultCode" else "rc_id" if sl == "RootCause" else "sym_id" if sl == "Symptom" else "cond_id" if sl == "Condition" else "act_id" if sl == "Action" else "tool_id" if sl == "Tool" else "man_id" if sl == "Manual" else "per_id" if sl == "Personnel" else "st_id" if sl == "Station" else "mel_id" if sl == "MELDeferral" else "sp_id" if sl == "SparePart" else "acid" if sl == "Aircraft" else "chapter" if sl == "ATASystem" else "comp_id" if sl == "Component" else "id"
                    end_field = "taskid" if el == "MaintenanceTask" else "fc_id" if el == "FaultCode" else "rc_id" if el == "RootCause" else "sym_id" if el == "Symptom" else "cond_id" if el == "Condition" else "act_id" if el == "Action" else "tool_id" if el == "Tool" else "man_id" if el == "Manual" else "per_id" if el == "Personnel" else "st_id" if el == "Station" else "mel_id" if el == "MELDeferral" else "sp_id" if el == "SparePart" else "acid" if el == "Aircraft" else "chapter" if el == "ATASystem" else "comp_id" if el == "Component" else "id"
                    batch_run(
                        f"UNWIND $rows AS r MATCH (a:{sl} {{{start_field}:r.start_id}}) MATCH (b:{el} {{{end_field}:r.end_id}}) MERGE (a)-[:{rel_type}]->(b)",
                        g_rows,
                    )
        print("relationships imported")

        # 统计
        stats = s.run("MATCH (n) RETURN count(n) AS total").single()["total"]
        rels = s.run("MATCH ()-[r]->() RETURN count(r) AS total").single()["total"]
        print(f"TOTAL NODES: {stats}  TOTAL RELS: {rels}")
    drv.close()

if __name__ == "__main__":
    main()
