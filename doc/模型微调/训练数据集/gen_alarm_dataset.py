# -*- coding: utf-8 -*-
"""航班运行告警处置 SFT 数据集生成器。
用法：python gen_alarm_dataset.py [目标条数，默认4200]
输出：alarm_dispose_train.json / alarm_dispose_val.json（alpaca 格式）
"""
import json
import random
import sys
from pathlib import Path

import pandas as pd

from alarm_specs import AL, LEVEL_NAMES, rnd_flight, rnd_route, route_card

random.seed(20260918)
BASE = Path(__file__).parent
XLSX = BASE / "告警类型.xlsx"
TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 4200

# ---------- 0. 与 xlsx 告警清单做覆盖校验 ----------
master = pd.read_excel(XLSX, sheet_name="告警主规则", header=0)
xlsx_names = [str(x).strip() for x in master.iloc[:, 0].dropna().tolist()]
COVER = {"飞机故障监控": "飞机故障-发动机火警", "飞机滑回告警": "飞机滑回", "二次开门告警": "二次开门",
         "大面积延误提醒": "大面积延误", "航班各保障节点": "航班保障节点", "时刻偏差告警": "时刻偏差",
         "飞机实时可用高度提醒": "飞机实时可用高度", "返航备降告警": "返航备降",
         "不可预期燃油3%航班的运行监控": "不可预期燃油3%"}
missing = []
for n in xlsx_names:
    key = COVER.get(n, n)
    if key not in AL and not any(k.endswith(f"-{n.split('-')[-1]}") for k in AL):
        if n not in ("飞机故障监控",) and key not in AL:
            missing.append(n)
# 故障子类归并校验：主规则中"飞机故障监控"对应 11 个子类 spec
assert not [m for m in missing if m != "飞机故障监控"], f"xlsx 中未覆盖的告警: {missing}"
print(f"xlsx 告警清单覆盖校验通过：{len(xlsx_names)} 条规则 -> {len(AL)} 个生成类型")

# ---------- 1. 指令模板 ----------
INSTR = {
    "dispose": [
        "你是航班运行监控席位的告警处置助手。根据告警信息，识别告警类型与等级，分析可能原因，给出规范处置动作与通知对象，以JSON输出。",
        "根据以下航班运行告警，输出处置建议JSON（含task/alarm_type/level/level_name/summary/possible_causes/actions/notify）。",
        "作为运控告警处置助手，分析该告警并按JSON格式给出处置方案（类型、等级、原因、动作、通知对象）。",
    ],
    "identify": [
        "根据现象描述判断告警类型，输出JSON（task=identify，含alarm_type/level/level_name/basis判断依据）。",
        "你是运控告警识别助手。根据描述判断属于哪类告警，输出JSON：alarm_type、level、basis。",
    ],
    "condition": [
        "回答该告警的触发条件与关键阈值，输出JSON（task=condition，含alarm_type/condition/thresholds）。",
        "你是告警规则助手。说明该告警的触发逻辑与阈值，输出JSON：alarm_type、condition、thresholds。",
    ],
    "operation": [
        "回答运行监控系统功能与操作问题，输出JSON（task=operation，含item/function/steps）。",
        "你是运行监控系统使用助手。说明该功能用途与操作要点，输出JSON：item、function、steps。",
    ],
}

def dispose_input(name, s, ctx):
    r = random.random()
    if r < 0.45:  # 系统告警卡
        return f"{route_card(ctx)}，{s['summ'].format(**ctx)}，请处置。"
    if r < 0.8:   # 值班口语
        return f"值班席位：{random.choice(s['phen']).format(**ctx)}，这个怎么处置？"
    return f"{ctx['fno']} 触发{name}告警，给出处置建议。"

def build(name, s, is_func):
    """生成一条样本，返回 (task, sample) 或 None"""
    ctx = {"name": name, **(s["ctxfn"]() or {})}
    if is_func:
        return ("operation", {
            "instruction": random.choice(INSTR["operation"]),
            "input": random.choice(s["phen"]).format(**ctx),
            "output": json.dumps({"task": "operation", "item": name, "function": s["cond"],
                                  "steps": s["actions"] or s["thr"]}, ensure_ascii=False)})
    r = random.random()
    if r < 0.78:  # dispose
        k = min(len(s["causes"]), random.choice([2, 3]))
        causes = random.sample(s["causes"], k=k)
        # 告警文案中已给出的原因必须出现在可能原因里，保持自洽
        if ctx.get("reason") and ctx["reason"] not in causes:
            causes = ([ctx["reason"]] + causes)[:max(k, 2)]
        return ("dispose", {
            "instruction": random.choice(INSTR["dispose"]),
            "input": dispose_input(name, s, ctx),
            "output": json.dumps({"task": "dispose", "alarm_type": name,
                                  "level": s["level"], "level_name": LEVEL_NAMES[s["level"]],
                                  "summary": s["summ"].format(**ctx),
                                  "possible_causes": causes, "actions": s["actions"],
                                  "notify": s["notify"]}, ensure_ascii=False)})
    if r < 0.90:  # identify
        return ("identify", {
            "instruction": random.choice(INSTR["identify"]),
            "input": random.choice(s["phen"]).format(**ctx),
            "output": json.dumps({"task": "identify", "alarm_type": name,
                                  "level": s["level"], "level_name": LEVEL_NAMES[s["level"]],
                                  "basis": s["cond"]}, ensure_ascii=False)})
    q = random.choice([f"{name}告警的触发条件是什么？", f"什么情况下会触发{name}？", f"{name}的告警阈值是多少？"])
    return ("condition", {
        "instruction": random.choice(INSTR["condition"]),
        "input": q,
        "output": json.dumps({"task": "condition", "alarm_type": name,
                              "condition": s["cond"], "thresholds": s["thr"]}, ensure_ascii=False)})

# ---------- 2. 按权重缩放到目标条数并生成 ----------
total_w = sum(v["weight"] for v in AL.values())

samples, seen = [], set()
stats = {"task": {}, "level": {}, "type": {}}
for name, s in AL.items():
    is_func = name.startswith("功能-")
    n = max(30, round(TARGET * s["weight"] / total_w))
    got = 0
    attempts = 0
    while got < n and attempts < n * 50:
        attempts += 1
        item = build(name, s, is_func)
        if item is None:
            continue
        _, sample = item
        if sample["input"] in seen:
            continue
        seen.add(sample["input"])
        samples.append(sample)
        stats["task"][item[0]] = stats["task"].get(item[0], 0) + 1
        lv = LEVEL_NAMES[s["level"]]
        stats["level"][lv] = stats["level"].get(lv, 0) + 1
        stats["type"][name] = stats["type"].get(name, 0) + 1
        got += 1

random.shuffle(samples)
val_n = max(150, len(samples) // 20)
val, train = samples[:val_n], samples[val_n:]

for path, data in [(BASE / "alarm_dispose_train.json", train), (BASE / "alarm_dispose_val.json", val)]:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

print(f"总数 {len(samples)}（目标 {TARGET}） -> train {len(train)} / val {len(val)}")
print("按任务:", stats["task"])
print("按等级:", stats["level"])
top = sorted(stats["type"].items(), key=lambda x: -x[1])
print("类型数:", len(top), "| Top5:", top[:5], "| Bottom3:", top[-3:])
