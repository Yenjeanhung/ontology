#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LangSmith 评估脚本：多智能体取证系统回归评估（数据集 + 评估器 + 实验对比）。

对 services/multi_agent 通用智能体团队（UniversalScenario）做数据集化回归评估：
每条 Example 的 task 喂给引擎跑完整流水线，评估器对产出的 conclusion/facts 打分，
结果上报 LangSmith 形成 Experiment，UI 中可跨轮对比、看图表与错误率。

设计对齐 doc/智能体/智能体评估/LangSmith接入方案.md §4：
  评估器① citation_valid   引用可溯源（确定性校验，零成本，防幻觉引用）
  评估器② factuality_judge 事实正确性（LLM-as-judge，对照人工标注 reference）

Examples 结构（LangSmith UI「Datasets & Experiments」中维护，或 --seed 播种）：
  inputs  = {"task": "任务文本"}            ← key 必须是 task，与 target 对齐
  outputs = {"reference": "标准答案"}       ← judge 需要；确定性评估器不依赖

用法（脚本自动定位 backend/，任意目录可执行）：
  python scripts/langsmith/eval_langsmith.py                          # 全量跑一轮回归
  python scripts/langsmith/eval_langsmith.py --limit 3                # 只取前 3 条（控额度）
  python scripts/langsmith/eval_langsmith.py --dataset 我的数据集名    # 指定数据集
  python scripts/langsmith/eval_langsmith.py --seed                   # 数据集不存在时播种示例

前置：
  scripts/.env.scripts 已配置 LLM（factuality judge 用，模板见 .env.scripts.example）；
  backend/.env 已配置 LANGSMITH_TRACING / LANGSMITH_API_KEY / LANGSMITH_PROJECT；
  引擎运行所需的知识库 / 图谱 / 台账数据已就绪（未就绪时节点自行降级，仍可跑通）。

额度纪律：每次引擎运行产生 10~50 条 trace，10 条数据集一轮 ≈ 数百 traces
（免费计划 5k/月）。调试期用 --limit 控制规模。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
# 向上自动定位 backend/（特征：含 models.py + database.py）；脚本放任意层级子目录都健壮
_BACKEND_DIR = next(
    (p for p in _SCRIPT_DIR.parents
     if (p / "models.py").is_file() and (p / "database.py").is_file()),
    _SCRIPT_DIR.parent.parent,
)
sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_SCRIPT_DIR.parent))   # scripts/ 根：公共 script_env 模块
import os  # noqa: E402

os.chdir(_BACKEND_DIR)

# 脚本专用配置层（scripts/.env.scripts，与服务端 backend/.env 分离）：
# 必须先于 config/providers 导入加载，注入 os.environ 后自然压过 backend/.env 同名项
from script_env import load_script_env  # noqa: E402

load_script_env()  # noqa: E402

# Windows GBK 控制台兜底
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import langsmith as _ls  # noqa: F401  (保留：实验元信息归属 langsmith 平台，显式依赖声明)  # noqa: E402
from langsmith import Client, aevaluate  # noqa: E402

from services.multi_agent.scenarios.universal import UniversalScenario  # noqa: E402

DEFAULT_DATASET = "myTest"   # UI 已建数据集；建议改为语义化名称（如 multi-agent-取证回归）

# 播种示例（仅当数据集不存在且指定 --seed 时创建；reference 请对照知识库人工修订）
SEED_SAMPLES = [
    {
        "task": "结合知识库，写一份签派放行前天气条件核查要点的简报。",
        "reference": "放行前需核查目的地与备降机场天气标准：METAR/TAF 中能见度、云底高、"
                     "风向风速是否满足放行天气标准；注意除冰保持时间与最低油量约束，"
                     "不满足条件时应延误或取消并通知相关部门。",
    },
    {
        "task": "研判：巡航阶段到达延误持续扩大，应启动哪些处置动作？",
        "reference": "应评估延误等级并按预案升级：通知运控值班跟踪，评估航路绕行或高度变更，"
                     "核对机组执勤时限余量，必要时启动备份机组或调整后续航班衔接，"
                     "并向 AOC 与签派同步处置进展。",
    },
]


# ── 1. 数据集：不存在时播种（已存在则直接复用，UI 维护为准） ──────────
def ensure_dataset(client: Client, name: str, *, reseed: bool = False) -> None:
    if client.has_dataset(dataset_name=name):
        if not reseed:
            print(f"[dataset] 复用已有数据集：{name}")
            return
        for ds in client.list_datasets(dataset_name=name):
            client.delete_dataset(dataset_id=ds.id)
        print(f"[dataset] 已删除旧数据集：{name}（--reseed）")
    ds = client.create_dataset(dataset_name=name, description="多智能体取证系统回归集")
    client.create_examples(
        dataset_id=ds.id,
        inputs=[{"task": s["task"]} for s in SEED_SAMPLES],
        outputs=[{"reference": s["reference"]} for s in SEED_SAMPLES],
    )
    print(f"[dataset] 已播种数据集 {name}（{len(SEED_SAMPLES)} 条示例；"
          f"reference 为示例种子，请对照知识库人工修订）")


# ── 2. target：包装引擎为可评估函数（非流式取终态） ───────────────────
def _extract_task(inputs) -> str:
    """宽容提取任务描述：标准键 task，兼容 question/input/text/query；
    task 允许直接为字符串或 {"task": ...} 一层嵌套。"""
    if not isinstance(inputs, dict):
        raw = inputs
    else:
        raw = inputs.get("task")
        if raw is None:
            for k in ("question", "input", "text", "query"):
                if inputs.get(k):
                    raw = inputs[k]
                    break
    if isinstance(raw, dict):
        raw = raw.get("task") or raw.get("text") or ""
    return str(raw or "").strip()


async def target(inputs: dict) -> dict:
    task = _extract_task(inputs)
    if not task:
        keys = list(inputs.keys()) if isinstance(inputs, dict) else type(inputs).__name__
        raise ValueError(
            f"Example inputs 缺少任务描述（期望 {{\"task\": \"...\"}}，实际 keys={keys}）。"
            f"请在 LangSmith UI 修正该 Example 的 inputs，或删除数据集后用 --seed 重新播种。"
        )
    scenario = UniversalScenario()
    engine = await scenario.build_engine_from_task(task)
    final = await engine.run()          # engine.py: run() -> 终态 dict
    return {
        "conclusion": final.get("conclusion", ""),
        "facts": final.get("facts", []),
        "verdict": final.get("verdict", {}),
        "elapsed_ms": final.get("elapsed_ms"),
    }


# ── 3. evaluator ①：引用可溯源（确定性，零成本） ─────────────────────
def citation_valid(run, example) -> dict:
    outputs = run.outputs or {}
    answer, facts = outputs.get("conclusion", ""), outputs.get("facts", [])
    cited = {int(n) for n in re.findall(r"\[事实(\d+)\]", answer)}
    if not facts:
        ok = not cited                                  # 无事实卡时不允许出现引用
    else:
        ok = bool(cited) and max(cited) <= len(facts)
    return {"key": "citation_valid", "score": int(ok)}


# ── 4. evaluator ②：事实正确性（LLM-as-judge，复用项目 LLM 配置） ────
async def factuality_judge(run, example) -> dict:
    outputs = run.outputs or {}
    reference = (example.outputs or {}).get("reference", "")
    if not reference:
        return {"key": "factuality", "score": 0, "comment": "该 Example 未标注 reference，跳过 judge"}
    from providers import create_llm                    # 与业务同一 LLM 工厂
    llm = create_llm()
    if llm is None:                                     # 工厂可返回 None（未配置时）
        return {"key": "factuality", "score": 0, "comment": "create_llm() 返回空，跳过 judge"}
    prompt = (
        "判断【结论】与【参考答案】是否事实一致（允许表述不同，不允许事实相悖）。\n"
        f"【参考答案】\n{reference}\n\n"
        f"【结论】\n{outputs.get('conclusion', '')}\n\n"
        "只回答 JSON：{\"consistent\": true/false, \"reason\": \"一句话理由\"}"
    )
    try:
        resp = await asyncio.wait_for(llm.ainvoke(prompt), timeout=60)
        verdict = re.search(r"\{.*\}", resp.content, re.S).group()
        data = json.loads(verdict)
        return {
            "key": "factuality",
            "score": int(bool(data.get("consistent"))),
            "comment": str(data.get("reason", "")),
        }
    except Exception as e:
        return {"key": "factuality", "score": 0, "comment": f"judge 失败: {e}"}


# ── 5. 主流程 ────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="LangSmith 多智能体回归评估")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="数据集名称")
    parser.add_argument("--limit", type=int, default=0, help="只取前 N 条（0=全量），控额度用")
    parser.add_argument("--seed", action="store_true", help="数据集不存在时播种示例")
    parser.add_argument("--reseed", action="store_true",
                        help="删除并重建数据集（现有 Example 结构不对时用，如 UI 手建数据）")
    parser.add_argument("--prefix", default="regression", help="实验名前缀（跨轮对比按前缀分组）")
    args = parser.parse_args()

    client = Client()
    if args.reseed or args.seed or not client.has_dataset(dataset_name=args.dataset):
        ensure_dataset(client, args.dataset, reseed=args.reseed)

    examples = list(client.list_examples(dataset_name=args.dataset))
    if not examples:
        sys.exit(f"[error] 数据集 {args.dataset} 为空：先在 LangSmith UI 添加 Examples，"
                 f"inputs 结构 {{\"task\": \"...\"}}")
    if args.limit > 0:
        examples = examples[: args.limit]

    # 脏数据防御：inputs 提取不到 task 的 Example 直接跳过，不让单条坏数据打断整轮评估
    valid = [e for e in examples if _extract_task(e.inputs)]
    for e in examples:
        if not _extract_task(e.inputs):
            print(f"[warn] 跳过结构不合法的 Example {e.id}："
                  f"inputs keys={list((e.inputs or {}).keys())}（期望 {{\"task\": ...}}）；"
                  f"可在 UI 修正，或 --reseed 重建数据集")
    if not valid:
        sys.exit("[error] 数据集内无任何合法 Example（全部缺 task）："
                 "请执行 --reseed 重建，或在 UI 修正 inputs")
    examples = valid

    n_ref = sum(1 for e in examples if (e.outputs or {}).get("reference"))
    print(f"[eval] 数据集 {args.dataset}：{len(examples)} 条（含 reference {n_ref} 条）｜"
          f"评估器 citation_valid + factuality_judge｜max_concurrency=2")
    print(f"[eval] 额度提示：预计产生 {len(examples) * 10}~{len(examples) * 50} 条 trace")

    t0 = time.monotonic()
    # target 是 async 函数（引擎走 ainvoke/astream），langsmith 要求用 aevaluate 而非 evaluate
    results = asyncio.run(
        aevaluate(
            target,
            data=examples,
            evaluators=[citation_valid, factuality_judge],
            max_concurrency=2,          # 控并发：保护免费额度与下游 LLM 限流
            experiment_prefix=args.prefix,
        )
    )
    print(f"[done] 用时 {time.monotonic() - t0:.0f}s，实验：{results.experiment_name}")
    print("[done] 打开 LangSmith → Datasets & Experiments → 该数据集 → Experiments 页签查看得分、"
          "跨轮对比与图表；截图沉淀到 doc/智能体/智能体评估/")


if __name__ == "__main__":
    main()
