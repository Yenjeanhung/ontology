# -*- coding: utf-8 -*-
"""查询意图路由 SFT 数据集生成器。

用法：python gen_router_dataset.py [目标条数，默认 5000]
输出：doc/模型微调/finetune/data/ontology_router.json       （90% 训练集）
      doc/模型微调/finetune/data/ontology_router_eval.json  （10% 留出验证集）

4 分类：kb(知识库) / graph(图谱) / data(台账) / chat(寒暄杂询)，支持多标签。
instruction 与《训练与部署手册.md》§2 示例逐字一致（即将来
services/query_router.py ROUTER_PROMPT 的指令段）——训练与线上必须同源。
seed 固定可复现；output 全部经 JSON 合法性校验。
"""
import json
import random
import sys
from pathlib import Path

try:  # Windows 控制台中文输出保险
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from alarm_specs import AIRLINES, AIRPORTS, AIRCRAFT

random.seed(20260919)
BASE = Path(__file__).parent
OUT = BASE.parent / "finetune" / "data"
TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
EVAL_RATIO = 0.10

LABELS = ("kb", "graph", "data", "chat")
INSTRUCTION = (
    '判断用户查询的意图类别，从 kb(知识库)/graph(图谱)/data(台账数据)/chat(寒暄杂询) '
    '中选择，输出 JSON：{"labels":[],"confidence":0.0}'
)

APTS = [c for c, _ in AIRPORTS]
TIMES = ["今天", "昨天", "前天", "明天", "本周", "上周", "本月", "上月",
         "近7天", "近30天", "9月18日", "9月以来", "国庆期间", "中秋假期",
         "昨天下午", "今天凌晨", "今天早高峰", "昨日全天", ""]
METRICS = ["航班正常率", "放行正常率", "始发航班正常率", "起飞正常率", "延误架次",
           "平均延误时长", "长时间延误架次", "取消架次", "取消率", "返航架次",
           "备降架次", "滑回次数", "执行率", "保障班次", "平均过站时长", "客座率",
           "告警总数", "告警处置时长", "未闭环告警数"]
KB_TERMS = ["航班正常率", "放行正常率", "始发航班正常率", "大面积延误", "延误等级划分",
            "除冰程序", "防冰运行", "二次放行", "不可预期燃油", "时刻协调",
            "航班时刻管理", "过站保障", "过站时间标准", "告警阈值", "值班交接",
            "交接班流程", "应急预案", "补班规定", "签派放行", "MEL放行",
            "航班跟踪", "航路规划", "油量政策", "载重平衡", "机组值勤时间",
            "备降场选择", "返航决策", "最低天气标准", "运行控制", "机坪运行"]
CHAT = ["你好", "您好呀", "在吗", "你是谁", "你叫什么名字", "你能做什么", "你会哪些功能",
        "谢谢啦", "辛苦了", "再见", "拜拜", "讲个笑话", "今天天气怎么样", "明天会下雨吗",
        "帮我写个周报", "推荐一部电影", "1加1等于几", "中午吃什么好", "好无聊啊",
        "测试一下", "hello", "你能听懂人话吗", "现在是几点", "帮我订一张去上海的机票",
        "说点什么", "你觉得AI会取代人类吗", "帮我算个账", "周日是几号"]

# 边界难例（防混样本）：指标名+定义问法是 kb 不是 data；实体泛查询给低置信度
EDGE = [
    ("航班正常率的定义是什么", ["kb"], 0.93),
    ("正常率统计标准在哪个文件里", ["kb"], 0.90),
    ("MU5307今天晚点了吗", ["data"], 0.94),
    ("CZ3456起飞了没", ["data"], 0.90),
    ("这个数据准不准", ["chat"], 0.60),
    ("MU5307是什么", ["graph"], 0.66),
    ("今天运行情况如何", ["data"], 0.78),
    ("延误和取消的区别", ["kb"], 0.91),
]


def _fno():
    return random.choice(AIRLINES) + str(random.randint(100, 8999))


def gen_data():
    """台账统计/数值类：指标 × 主体 × 时间 × 句式。"""
    r = random.random()
    if r < 0.40:
        s = _fno()
    elif r < 0.55:
        s = random.choice(AIRLINES)
    elif r < 0.70:
        s = f"{random.choice(AIRLINES)}在{random.choice(APTS)}"
    elif r < 0.82:
        s = random.choice(APTS)
    elif r < 0.90:
        s = f"{random.choice(AIRCRAFT)}机队"
    else:
        s = "全公司"
    t = random.choice(TIMES)
    m = random.choice(METRICS)
    q = random.choice([
        "{t}{s}的{m}是多少", "{t}{s}的{m}情况怎么样", "查一下{t}{s}的{m}",
        "{t}{s}{m}多少", "统计一下{t}{s}的{m}", "{t}{s}的{m}和昨天比怎么样",
        "帮我看看{t}{s}的{m}数据", "{t}{s}的{m}趋势如何", "{s}最近的{m}怎么样",
    ]).format(t=t, s=s, m=m)
    conf = random.uniform(0.86, 0.97) if t else random.uniform(0.75, 0.88)
    return q, ["data"], conf


def gen_graph():
    """图谱实体/关系类：执飞、归属、航线、衔接、关联告警等。"""
    f1, f2 = _fno(), _fno()
    a1, a2 = random.sample(APTS, 2)
    al, ac = random.choice(AIRLINES), random.choice(AIRCRAFT)
    q = random.choice([
        f"{f1}执飞的什么机型", f"{f1}属于哪个航司", f"{f1}由谁执飞",
        f"{f1}经过哪些机场", f"{f1}和{f2}是什么关系",
        f"{random.choice(APTS)}都有哪些航线", f"{al}在{random.choice(APTS)}涉及哪些航班",
        f"{f1}的上下游衔接航班有哪些", f"{f1}关联的告警有哪些",
        f"{al}机队有哪些{ac}飞机", f"{f1}的机组都有谁", f"查一下{f1}的代码共享航班",
        f"{a1}和{a2}之间有哪些直飞航班", f"{al}的过夜航班分布在哪些机场",
    ])
    return q, ["graph"], random.uniform(0.84, 0.96)


def gen_kb():
    """知识库/规章文档类：定义、口径、流程、找文档（术语×句式×来源×语气组合）。"""
    term = random.choice(KB_TERMS)
    src = random.choice(["运行手册", "知识库", "规章", "操作手册", "SOP", "运维文档", ""])
    tpl = random.choice([
        "什么是{t}", "{t}是怎么定义的", "{t}的统计口径是什么",
        "{s}里关于{t}是怎么规定的", "{t}的标准是多少",
        "知识库里有没有{t}相关的资料", "找一下{t}相关的文档",
        "{t}的流程是什么", "{t}在哪个文件里说明", "如何办理{t}",
        "{s}里{t}的依据是什么", "帮我查查{t}的规定",
        "{t}具体指什么", "{t}相关材料在哪能看到",
        "{s}里{t}的要点有哪些", "{t}的适用范围是什么",
        "{s}对{t}有什么要求", "{t}是谁负责编写的",
    ])
    q = tpl.format(t=term, s=src).strip()
    # 少量自然语气尾巴——仅限名词性/陈述句式，祈使句（如何办理/帮我查查/找一下）不加
    if random.random() < 0.30 and not tpl.startswith(("如何", "帮我", "找一下")):
        q += random.choice(["？", "吗", "呢"])
    return q, ["kb"], random.uniform(0.84, 0.96)


def gen_chat():
    """寒暄/业务外杂询：基础句池 × 修饰变体，保证句式多样性不重样。"""
    pre = random.choice(["", "", "", "哈喽，", "请问，", "请问一下，", "小助手，", "你好，", "嗨，"])
    suf = random.choice(["", "", "", "呀", "呢", "~", "！", "？", "。"])
    return f"{pre}{random.choice(CHAT)}{suf}", ["chat"], random.uniform(0.88, 0.98)


def gen_multi():
    """多标签组合句（~6%）：一问跨两类能力。"""
    f1, al, term = _fno(), random.choice(AIRLINES), random.choice(KB_TERMS)
    q, labels = random.choice([
        (f"查一下{f1}今天的正常率，顺便看看它执飞的机型", ["data", "graph"]),
        (f"{f1}的航线经过哪些机场？今天的正常率也看一下", ["data", "graph"]),
        ("延误处置的流程规定是什么？另外看下今天延误了多少架次", ["kb", "data"]),
        (f"告警规则在哪个文档里？还有{f1}关联了哪些告警", ["kb", "graph"]),
        (f"看看{al}本月取消了多少班，手册里大面积延误预案怎么规定的", ["data", "kb"]),
        (f"{f1}是什么机型？找一下机型放行标准的手册规定", ["graph", "kb"]),
        (f"{term}的定义是什么？今天相关告警有多少条", ["kb", "data"]),
    ])
    return q, labels, random.uniform(0.72, 0.86)


GENS = {"data": gen_data, "graph": gen_graph, "kb": gen_kb,
        "chat": gen_chat, "multi": gen_multi}
QUOTA = {"data": 0.39, "graph": 0.24, "kb": 0.20, "chat": 0.11, "multi": 0.06}


def build(total):
    rows, seen = [], set()

    def emit(q, labels, conf, kind):
        q = q.strip()
        if not q or q in seen:
            return False
        assert labels and all(x in LABELS for x in labels), labels
        assert 0.0 < conf <= 1.0, conf
        seen.add(q)
        rows.append({"_kind": kind, "instruction": INSTRUCTION, "input": q,
                     "output": json.dumps({"labels": labels, "confidence": round(conf, 2)},
                                          ensure_ascii=False, separators=(",", ":"))})
        return True

    for kind, ratio in QUOTA.items():
        need, got, tries = round(total * ratio), 0, 0
        max_tries = need * 200 + 5000                    # 重试保险：生成空间意外耗尽时退出
        while got < need and tries < max_tries:
            tries += 1
            if random.random() < 0.04:                   # ~4% 边界难例
                q, labels, conf = random.choice(EDGE)
            else:
                q, labels, conf = GENS[kind]()
            got += emit(q, labels, conf, kind)
        assert got >= need, f"{kind} 类生成空间不足：need={need} got={got}"
    return rows


def split_eval(rows):
    """分层切分：每类内部 shuffle 后取 10% 进验证集，再各自打乱。"""
    by_kind = {"data": [], "graph": [], "kb": [], "chat": [], "multi": []}
    for r in rows:
        by_kind[r["_kind"]].append(r)
    train, ev = [], []
    for lst in by_kind.values():
        random.shuffle(lst)
        k = max(1, round(len(lst) * EVAL_RATIO))
        ev += lst[:k]
        train += lst[k:]
    random.shuffle(train)
    random.shuffle(ev)
    return train, ev


def main():
    rows = build(TARGET)
    train, ev = split_eval(rows)

    def stat(lst):
        c = {}
        for r in lst:
            for lb in json.loads(r["output"])["labels"]:
                c[lb] = c.get(lb, 0) + 1
        return "  ".join(f"{k}:{v}" for k, v in sorted(c.items()))

    print(f"目标 {TARGET} | 去重后 {len(rows)} 条")
    print(f"train {len(train)} 条 -> {OUT / 'ontology_router.json'}   标签分布 {stat(train)}")
    print(f"eval  {len(ev)} 条 -> {OUT / 'ontology_router_eval.json'}   标签分布 {stat(ev)}")
    for name, data in (("ontology_router.json", train), ("ontology_router_eval.json", ev)):
        for r in data:
            r.pop("_kind", None)
            json.loads(r["output"])                       # 落盘前再校验一次
        (OUT / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("JSON 合法性校验通过")


if __name__ == "__main__":
    main()
