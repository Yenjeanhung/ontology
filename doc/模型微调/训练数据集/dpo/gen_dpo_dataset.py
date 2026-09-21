# -*- coding: utf-8 -*-
"""意图路由 DPO 偏好对数据集生成器。

用法：python gen_dpo_dataset.py [目标条数，默认 1000]（在本 dpo/ 目录内执行）
输出：doc/模型微调/finetune/dpo/ontology_router_dpo.json（与 dpo yaml 同目录）
依赖：复用上一级「训练数据集/」的 gen_router_dataset.py（SFT 同分布生成函数与词库）。

定位：SFT（gen_router_dataset.py → ontology_router.json）教会模型「输出什么」，
DPO 在 SFT 之上修正其仍残留的系统性错误模式。rejected 全部映射线上路由服务
（services/multi_agent/router_service.py）的真实兜底分支，六类失败模式：

  wrong_confident    错标签+高置信  → 错而自信最伤：合法标签误分类会被置信门控放行，
                                     按错误 mode 精简组合（误判 chat 更是整轮编排被跳过）
  conf_miscalibrated 签对置信失准   → 门控失真：该高给低误触发全组合兜底（白调一次），
                                     该低给高则放行低质量输入的错误组合
  enum_drift         枚举漂移       → database/台账 等非法标签 → 非法 mode 回落全组合
  format_polluted    格式污染       → ```json 围栏 / 客套前缀 / <think> 残留 → JSON 解析失败
  key_drift          键名漂移       → label/category 等未兼容键 → 解析失败回落
  multi_object       多 JSON 并列   → 贪婪正则跨对象 json.loads 失败 → 回落

chosen 与 SFT output 严格同构（{"labels":[],"confidence":0.0}），query 与 SFT
同分布（复用同一组生成函数，换随机种子取新样本）。固定 seed 可复现。
"""
import json
import random
import sys
from pathlib import Path

try:  # Windows 控制台中文输出保险
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(__file__).parent                       # .../训练数据集/dpo
sys.path.insert(0, str(BASE.parent))               # gen_router_dataset.py 在上一级「训练数据集/」
from gen_router_dataset import GENS, QUOTA, EDGE, INSTRUCTION

random.seed(20260920)
OUT = BASE.parent.parent / "finetune" / "dpo"      # .../模型微调/finetune/dpo（与 dpo yaml 同目录）
TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 1000

# 六类 rejected 配比：语义层（R1/R2）为主——这是 DPO 相对 SFT 的真正增量；
# 格式层（R3~R6）SFT 已解决大半，保留少量作巩固，防止过训回退。
RATIO = {
    "wrong_confident": 0.30,
    "conf_miscalibrated": 0.25,
    "enum_drift": 0.15,
    "format_polluted": 0.12,
    "key_drift": 0.10,
    "multi_object": 0.08,
}

# 错标签映射：模拟真实边界混淆方向（定义问法→当统计、寒暄→当知识库检索等）
WRONG_OF = {
    "kb": ["data", "graph"],
    "data": ["kb", "graph"],
    "graph": ["data", "kb"],
    "chat": ["kb", "data"],
}
BAD_ENUMS = ["database", "data_query", "knowledge", "social", "台账", "图谱", "知识库"]


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def make_rejected(labels: list, conf: float) -> tuple[str, str]:
    """按配比抽一种失败模式，构造 rejected 文本。返回 (kind, rejected)。"""
    kind = random.choices(list(RATIO), weights=RATIO.values())[0]
    good = {"labels": labels, "confidence": round(conf, 2)}

    if kind == "wrong_confident":
        # 错标签 + 高置信：EDGE 难例的典型误判方向（"定义是什么"→data 等）
        wrong = random.choice(WRONG_OF.get(labels[0], ["data", "kb"]))
        return kind, _dumps({"labels": [wrong], "confidence": round(random.uniform(0.90, 0.98), 2)})

    if kind == "conf_miscalibrated":
        # 标签对、置信度反向：标准输入压到门控阈值附近（白调兜底）；模糊输入自信化
        if conf >= 0.86:
            conf_r = round(random.uniform(0.55, 0.68), 2)
        else:
            conf_r = round(random.uniform(0.90, 0.98), 2)
        return kind, _dumps({"labels": labels, "confidence": conf_r})

    if kind == "enum_drift":
        # 非法枚举：线上 mode 校验不通过 → 回落全组合（router_service 非法 mode 分支）
        bad = random.choice(BAD_ENUMS)
        return kind, _dumps({"labels": [bad], "confidence": round(random.uniform(0.82, 0.94), 2)})

    if kind == "format_polluted":
        # 格式污染：贪婪正则虽可容忍纯围栏，但客套前缀/思考残留会把首个 { 前的
        # 文本一并卷入，json.loads 失败 → 回落全组合（_parse_json_block 失败分支）
        g = _dumps(good)
        polluted = random.choice([
            f"```json\n{g}\n```",
            f"好的，{g}希望对你有帮助！",
            f"<think>\n用户在问运行数据，应当归入台账类。\n</think>\n{g}",
            f"{g}\n以上是分类结果，仅供参考。",
        ])
        return kind, polluted

    if kind == "key_drift":
        # 键名漂移：label/category、conf 等未兼容键 → get 取不到 → 解析失败回落
        drift = random.choice([
            {"label": labels[0], "confidence": round(conf, 2)},
            {"category": labels, "conf": round(conf, 2)},
            {"labels": labels, "score_level": "high"},
        ])
        return kind, _dumps(drift)

    # multi_object：两个 JSON 连排 → 正则 [\[{].*[\]}] 贪婪跨对象 → loads 失败
    second = _dumps({"labels": ["chat"], "confidence": 0.5})
    sep = random.choice(["", "\n", " "])
    return "multi_object", f"{_dumps(good)}{sep}{second}"


def build(total: int):
    """与 SFT 同分布抽 query（含 EDGE 难例），chosen 同构、rejected 按失败模式构造。"""
    rows, seen = [], set()
    kinds, tries = {}, 0
    max_tries = total * 200 + 5000
    while len(rows) < total and tries < max_tries:
        tries += 1
        if random.random() < 0.10:                       # EDGE 难例加密（DPO 主战场）
            q, labels, conf = random.choice(EDGE)
        else:
            kind = random.choices(list(QUOTA), weights=QUOTA.values())[0]
            q, labels, conf = GENS[kind]()
        q = q.strip()
        if not q or q in seen:
            continue
        seen.add(q)
        r_kind, rejected = make_rejected(labels, conf)
        kinds[r_kind] = kinds.get(r_kind, 0) + 1
        rows.append({"instruction": INSTRUCTION, "input": q,
                     "chosen": _dumps({"labels": labels, "confidence": round(conf, 2)}),
                     "rejected": rejected, "_kind": r_kind})
    assert len(rows) >= total, f"生成空间不足：need={total} got={len(rows)}"
    random.shuffle(rows)
    return rows, kinds


def main():
    rows, kinds = build(TARGET)
    # 校验：chosen 必须是合法且与 SFT 同构的 JSON；rejected 逐条带 kind 统计
    for r in rows:
        c = json.loads(r["chosen"])
        assert isinstance(c.get("labels"), list) and isinstance(c.get("confidence"), float), r
    stat = "  ".join(f"{k}:{v}" for k, v in sorted(kinds.items(), key=lambda x: -x[1]))
    print(f"目标 {TARGET} | 去重后 {len(rows)} 条偏好对")
    print(f"rejected 失败模式分布：{stat}")
    for r in rows:
        r.pop("_kind", None)
    (OUT / "ontology_router_dpo.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"-> {OUT / 'ontology_router_dpo.json'}")
    print("chosen 合法性校验通过（rejected 特意保留失败形态）")


if __name__ == "__main__":
    main()
