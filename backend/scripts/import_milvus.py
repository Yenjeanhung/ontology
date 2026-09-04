# -*- coding: utf-8 -*-
"""将民航维修实体向量化并灌入 Milvus"""
import os, json, random
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from sentence_transformers import SentenceTransformer
from pymilvus import (
    connections, Collection, FieldSchema, CollectionSchema, DataType, utility
)

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "generated", "entities.json")
HOST, PORT = "localhost", "19530"
DIM = 512
COLL = "aviation_maintenance_entities"

def build_texts(d):
    """为每个实体构造一段检索文本 -> (id, entity_type, type_id, text)"""
    rows = []
    for r in d["operators"]:
        rows.append(("Operator", r["oid"], f"航空公司 {r['name']} 运营人机队维修管理"))
    for r in d["models"]:
        rows.append(("AircraftModel", r["code"], f"机型 {r['code']} 制造商{r['manufacturer']} 发动机{r['engine']} 类别{r['type']} 系列{r['family']}"))
    for r in d["ata"]:
        rows.append(("ATASystem", r["chapter"], f"ATA系统章节 {r['chapter']} {r['name']} 飞机维修系统"))
    for r in d["components"]:
        rows.append(("Component", r["comp_id"], f"部件 {r['name']} 件号{r['pn']} 属于ATA{r['ata_chapter']} 适用机型{r['model']} 制造商{r['manufacturer']} 平均故障间隔{r['mtbf_hours']}小时"))
    for r in d["parameters"]:
        rows.append(("Parameter", r["pid"], f"监测参数 {r['name']} 符号{r['symbol']} 飞机系统状态监控"))
    for r in d["failure_modes"]:
        rows.append(("FailureMode", r["fid"], f"故障模式 {r['name']} 民航维修典型失效形式"))
    for r in d["aircraft"]:
        rows.append(("Aircraft", r["acid"], f"飞机 机号{r['tail']} 航班号{r['flight_no']} 机型{r['model']} 制造{r['manufacturer']} 发动机{r['engine']} 序列号{r['msn']} 运营中国国航 出厂{r['entry_year']} 循环{r['total_cycles']} 飞行小时{r['total_fh']}"))
    for r in d["ads"]:
        rows.append(("AirworthinessDirective", r["ad_number"], f"适航指令 {r['ad_number']} 标题{r['title']} ATA{r['ata_chapter']} 适用{r['applicability']} 状态{r['status']}"))
    for r in d["tasks"]:
        extra = f" 关联适航指令{r['ad_number']}" if r["ad_number"] else ""
        rows.append(("MaintenanceTask", r["taskid"], f"维修任务工卡 {r['name']} 编号{r['taskid']} 机号{r['tail']} 机型{r['model']} ATA{r['ata_chapter']} 类型{r['task_type']} 部件{r['comp_name']} 周期{r['interval_days']}天 到期{r['due_date']} 状态{r['status']} 工时{r['manhours']}{extra}"))
    for r in d.get("fault_codes", []):
        rows.append(("FaultCode", r["fc_id"], f"故障代码 {r['code']} 名称{r['name']} ATA{r['ata_chapter']} 机型{r['model']} 严重度{r['severity']}"))
    for r in d.get("root_causes", []):
        rows.append(("RootCause", r["rc_id"], f"故障原因 {r['name']} 类别{r['category']}"))
    for r in d.get("symptoms", []):
        rows.append(("Symptom", r["sym_id"], f"故障征兆 {r['name']} 等级{r['level']}"))
    for r in d.get("conditions", []):
        rows.append(("Condition", r["cond_id"], f"工况 {r['name']} {r['description']}"))
    for r in d.get("actions", []):
        rows.append(("Action", r["act_id"], f"维修措施 {r['name']} 类型{r['action_type']}"))
    for r in d.get("tools", []):
        rows.append(("Tool", r["tool_id"], f"工装工具 {r['name']} 类型{r['tool_type']} 需校准{r['calibration_required']}"))
    for r in d.get("manuals", []):
        rows.append(("Manual", r["man_id"], f"手册 {r['doc_type']} {r['name']} 版本{r['revision']}"))
    for r in d.get("personnel", []):
        rows.append(("Personnel", r["per_id"], f"人员 {r['name']} 岗位{r['role']} 执照{r['license']} 站点{r['station']}"))
    for r in d.get("stations", []):
        rows.append(("Station", r["st_id"], f"维修站 {r['name']} 类型{r['station_type']} 城市{r['city']}"))
    for r in d.get("mel_deferrals", []):
        rows.append(("MELDeferral", r["mel_id"], f"MEL保留 {r['deferral_code']} 项目{r['item']} 类别{r['category']} 机号{r['tail']} 到期{r['due_date']}"))
    for r in d.get("spare_parts", []):
        rows.append(("SparePart", r["sp_id"], f"航材 {r['name']} 件号{r['pn']} ATA{r['ata_chapter']} 机型{r['model']}"))
    return rows

def main():
    d = json.load(open(DATA, encoding="utf-8"))
    rows = build_texts(d)
    print("total entities to embed:", len(rows))

    model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    print("encoding ...")
    texts = [r[2] for r in rows]
    vecs = model.encode(texts, batch_size=256, show_progress_bar=True, normalize_embeddings=True)
    print("encoded dim:", vecs.shape)

    connections.connect(alias="default", host=HOST, port=PORT)
    if utility.has_collection(COLL):
        utility.drop_collection(COLL)
    fields = [
        FieldSchema("pk", DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema("entity_type", DataType.VARCHAR, max_length=64),
        FieldSchema("entity_id", DataType.VARCHAR, max_length=64),
        FieldSchema("content", DataType.VARCHAR, max_length=1024),
        FieldSchema("embedding", DataType.FLOAT_VECTOR, dim=DIM),
    ]
    schema = CollectionSchema(fields, description="民航维修实体向量库")
    col = Collection(COLL, schema)
    col.create_index("embedding", {"index_type": "HNSW", "metric_type": "COSINE", "params": {"M": 8, "efConstruction": 200}})
    print("collection created & indexed")

    B = 2000
    for i in range(0, len(rows), B):
        chunk = rows[i:i+B]
        col.insert([
            [r[0] for r in chunk],
            [r[1] for r in chunk],
            [r[2] for r in chunk],
            vecs[i:i+B].tolist(),
        ])
    col.flush()
    col.load()
    print("INSERTED:", col.num_entities)

if __name__ == "__main__":
    main()
