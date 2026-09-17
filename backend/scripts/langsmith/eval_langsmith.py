#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LangSmith 评估脚本：多智能体取证系统回归评估（数据集 + 评估器 + 实验对比）。

对 services/multi_agent 通用智能体团队（UniversalScenario）做数据集化回归评估：
每条 Example 的 task 喂给引擎跑完整流水线，评估器对产出的 conclusion/facts 打分，
结果上报 LangSmith 形成 Experiment，UI 中可跨轮对比、看图表与错误率。

设计对齐 doc/智能体/智能体评估/LangSmith接入方案.md §4：
  评估器① citation_valid   引用可溯源（确定性校验，零成本，防幻觉引用）
  评估器② factuality_judge 事实正确性（LLM-as-judge，对照人工标注 reference；
          用 openevals 的 create_async_llm_as_judge 实现，judge 复用项目 LLM 实例；
          未安装 openevals 时自动回退本地手写 judge，保证脚本随时可跑）
  评估器③ trajectory_valid 执行轨迹合理性（agentevals 轨迹 judge，把引擎事件流
          映射成 agent 轨迹，评"过程"；未安装 agentevals 时自动跳过）

Examples 结构（LangSmith UI「Datasets & Experiments」中维护，或 --seed 播种）：
  inputs  = {"task": "任务文本"}            ← key 必须是 task，与 target 对齐
  outputs = {"reference": "标准答案"}       ← judge 需要；确定性评估器不依赖

用法（脚本自动定位 backend/，任意目录可执行）：
  python scripts/langsmith/eval_langsmith.py                          # 全量跑一轮回归
  python scripts/langsmith/eval_langsmith.py --limit 3                # 只取前 3 条（控额度）
  python scripts/langsmith/eval_langsmith.py --dataset 我的数据集名    # 指定数据集
  python scripts/langsmith/eval_langsmith.py --seed                   # 数据集不存在时播种示例

前置：
  pip install openevals agentevals（judge 用；未装时 factuality 回退本地手写、
          trajectory 自动跳过，均不阻断跑批）；
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
        "trajectory": build_trajectory(engine, task, final.get("conclusion", "")),
    }


def build_trajectory(engine, task: str, conclusion: str) -> list[dict]:
    """把引擎事件流还原成 agentevals 轨迹（OpenAI messages 形态）。

    引擎把过程事件推入 engine.q（node_start / node_done / evidence / fact…，
    跑批时无人消费，队列里就是完整过程）。这里映射成：
      user               = 用户任务
      assistant+tool_call = 节点启动（工具名=节点名，入参=子任务目标）
      tool               = 节点完成（返回=节点产出摘要）
      assistant          = 最终结论
    这样 judge 看到的不是"结论对不对"，而是"这条执行路径合不合理"。
    """
    msgs: list[dict] = [{"role": "user", "content": task}]
    while True:
        try:
            evt = engine.q.get_nowait()
        except asyncio.QueueEmpty:
            break
        kind = evt.get("type")
        if kind == "node_start":
            msgs.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": evt.get("node", ""),
                        "arguments": json.dumps(
                            {"role": evt.get("role", ""), "goal": evt.get("goal", "")},
                            ensure_ascii=False,
                        ),
                    }
                }],
            })
        elif kind == "node_done":
            msgs.append({
                "role": "tool",
                "tool_call_id": evt.get("node", ""),
                "content": evt.get("summary", ""),
            })
    msgs.append({"role": "assistant", "content": (conclusion or "")[:1200]})
    return msgs


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


# ── 4. evaluator ②：事实正确性（LLM-as-judge） ──────────────────────
# 首选 openevals 的 create_async_llm_as_judge：结构化输出取分（不用手写正则解析 JSON）、
# 自带 reasoning 便于 UI 里看判分理由；judge 直接复用 providers.create_llm() 的 LLM 实例，
# 因此与业务走同一套 .env 配置（含自定义 base_url / 国内兼容模型）。
# 注意：prompt 是 str 模板，openevals 用 str.format 填充，除 {inputs}/{outputs}/
# {reference_outputs} 外不能再出现花括号。
FACTUALITY_PROMPT = (
    "你是航空运行领域的评分裁判，判断【系统结论】与【参考答案】是否事实一致。\n"
    "判分口径：允许表述不同，不允许事实相悖；结论中参考答案未覆盖的内容，"
    "只要不与参考答案冲突，不算相悖。\n\n"
    "【用户任务】\n{inputs}\n\n"
    "【参考答案】\n{reference_outputs}\n\n"
    "【系统结论】\n{outputs}\n\n"
    "只输出一个 JSON 对象，不要 Markdown 代码块和多余文字，格式如下：\n"
    "{{\"reasoning\": \"一句话判分理由\", \"score\": true 或 false}}\n"
    "score 为 true 表示事实一致。"
)

_FACTUALITY_JUDGE = None      # main() 里构造；为 None 时走本地回退


def _build_judge_llm():
    """judge 专用 LLM：与业务同配置，但把结构化输出锁定为 json_mode。

    openevals 内部固定调用 judge.with_structured_output(schema)，langchain 默认走
    OpenAI 严格 json_schema；本项目的 GLM / DeepSeek 等兼容端点对该模式支持不完整
    （实测会退化成自由文本，JsonOutputParser 直接抛错）。这里用子类把 method 锁成
    json_mode（response_format=json_object，已验证端点支持）。
    """
    from config import settings
    from langchain_openai import ChatOpenAI
    from providers.llm import build_llm

    class _JsonModeChatOpenAI(ChatOpenAI):
        def with_structured_output(self, schema, *, include_raw=False, **kwargs):
            kwargs.pop("method", None)
            return super().with_structured_output(
                schema, method="json_mode", include_raw=include_raw, **kwargs
            )

    return build_llm(
        provider=settings.LLM_PROVIDER,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        model=settings.LLM_MODEL,
        max_tokens=settings.LLM_MAX_TOKENS,
        temperature=settings.LLM_TEMPERATURE,
        chat_cls=_JsonModeChatOpenAI,
    )


def build_openevals_judge():
    """构造 openevals judge。返回 None 表示不可用（未装包 / LLM 未配置），走本地回退。"""
    try:
        from openevals.llm import create_async_llm_as_judge
    except ImportError:
        print("[warn] 未安装 openevals（pip install openevals），factuality 走本地手写 judge")
        return None
    from providers import create_llm                  # 与业务同一 LLM 工厂
    if create_llm() is None:                          # 工厂可返回 None（未配置时）
        print("[warn] create_llm() 返回空，factuality 走本地手写 judge："
              "复制 scripts/.env.scripts.example 为 .env.scripts 并填写 LLM 配置")
        return None
    return create_async_llm_as_judge(
        prompt=FACTUALITY_PROMPT,
        feedback_key="factuality",
        judge=_build_judge_llm(),
        use_reasoning=True,                           # 判分理由写进 comment，UI 可查
    )


async def _legacy_factuality_judge(answer: str, reference: str) -> dict:
    """本地手写 judge（openevals 不可用时的回退路径）。"""
    from providers import create_llm
    llm = create_llm()
    if llm is None:
        return {"key": "factuality", "score": 0, "comment": "create_llm() 返回空，跳过 judge"}
    prompt = (
        "判断【结论】与【参考答案】是否事实一致（允许表述不同，不允许事实相悖）。\n"
        f"【参考答案】\n{reference}\n\n"
        f"【结论】\n{answer}\n\n"
        "只回答 JSON：{{\"consistent\": true/false, \"reason\": \"一句话理由\"}}"
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


async def factuality_judge(run, example) -> dict:
    """langsmith 适配层：把 run/example 映射成 openevals 的 (inputs, outputs, reference_outputs)。"""
    reference = (example.outputs or {}).get("reference", "")
    if not reference:
        return {"key": "factuality", "score": 0, "comment": "该 Example 未标注 reference，跳过 judge"}
    answer = (run.outputs or {}).get("conclusion", "")
    if _FACTUALITY_JUDGE is None:
        return await _legacy_factuality_judge(answer, reference)
    try:
        return await _FACTUALITY_JUDGE(
            inputs=_extract_task(run.inputs),
            outputs=answer,
            reference_outputs=reference,
        )
    except Exception as e:
        # judge 故障不静默记 0 分：0 分会被误读成「事实错误」，这里带上原因便于在 UI 排查
        return {"key": "factuality", "score": 0, "comment": f"judge 失败: {e}"}


# ── 5. evaluator ③：执行轨迹合理性（agentevals，评"过程"而非"结论"） ──
# 前两个评估器只看终态，改了编排逻辑（节点增删、并行改串行、评审被跳过）却看不出
# 回归；这里把引擎事件流映射成 agent 轨迹，交给 agentevals 的轨迹 judge 判路径是否合理。
TRAJECTORY_PROMPT = (
    "你是多智能体系统的过程评审官。下面是一次任务执行的完整轨迹：\n"
    "user 是用户任务；assistant 的 tool_call 是节点启动（工具名=节点名，"
    "goal=子任务目标）；tool 是该节点的产出摘要；最后一条 assistant 是交付的结论。\n\n"
    "判断这条执行路径是否合理，重点看：\n"
    "1. 子任务分解是否覆盖任务要点，有无明显缺失或重复；\n"
    "2. 节点顺序是否符合「规划 → 并行取证 → 评审 → 合成」的编排；\n"
    "3. 关键节点失败（未命中语料 / LLM 未配置 / 执行失败）时后续是否仍正常收敛；\n"
    "4. 有无明显空转（节点无产出却照常合成）或跳步。\n\n"
    "只输出一个 JSON 对象，不要 Markdown 代码块和多余文字，格式如下：\n"
    "{{\"reasoning\": \"一句话判分理由\", \"score\": true 或 false}}\n"
    "score 为 true 表示执行路径合理。\n\n"
    "【执行轨迹】\n{outputs}"
)

_TRAJECTORY_JUDGE = None      # main() 里构造；为 None 时跳过该评估器


def build_trajectory_judge():
    """构造 agentevals 轨迹 judge。返回 None 表示不可用（未装包 / LLM 未配置）。"""
    try:
        from agentevals.trajectory.llm import create_async_trajectory_llm_as_judge
    except ImportError:
        print("[warn] 未安装 agentevals（pip install agentevals），跳过 trajectory 评估")
        return None
    from providers import create_llm
    if create_llm() is None:                          # 提示信息已在 build_openevals_judge 打出
        return None
    return create_async_trajectory_llm_as_judge(
        prompt=TRAJECTORY_PROMPT,
        feedback_key="trajectory_valid",
        judge=_build_judge_llm(),                     # 同样锁定 json_mode
        use_reasoning=True,
    )


async def trajectory_judge(run, example) -> dict:
    if _TRAJECTORY_JUDGE is None:
        return {"key": "trajectory_valid", "score": 0,
                "comment": "agentevals 不可用（未安装或 LLM 未配置），跳过轨迹评估"}
    traj = (run.outputs or {}).get("trajectory") or []
    if len(traj) < 3:                                 # 只有 user + 结论，说明节点没跑起来
        return {"key": "trajectory_valid", "score": 0, "comment": "轨迹过短（节点未启动），判为异常"}
    try:
        return await _TRAJECTORY_JUDGE(outputs=traj)
    except Exception as e:
        return {"key": "trajectory_valid", "score": 0, "comment": f"轨迹 judge 失败: {e}"}


# ── 6. 主流程 ────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="LangSmith 多智能体回归评估")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="数据集名称")
    parser.add_argument("--limit", type=int, default=0, help="只取前 N 条（0=全量），控额度用")
    parser.add_argument("--seed", action="store_true", help="数据集不存在时播种示例")
    parser.add_argument("--reseed", action="store_true",
                        help="删除并重建数据集（现有 Example 结构不对时用，如 UI 手建数据）")
    parser.add_argument("--prefix", default="regression", help="实验名前缀（跨轮对比按前缀分组）")
    args = parser.parse_args()

    # judge 在跑批前构造一次（内部复用 providers 的 LLM 单例），失败则本地回退/跳过
    global _FACTUALITY_JUDGE, _TRAJECTORY_JUDGE
    _FACTUALITY_JUDGE = build_openevals_judge()
    _TRAJECTORY_JUDGE = build_trajectory_judge()

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
    judge_backend = "openevals" if _FACTUALITY_JUDGE is not None else "本地手写（回退）"
    evaluators = [citation_valid, factuality_judge]
    if _TRAJECTORY_JUDGE is not None:
        evaluators.append(trajectory_judge)
    print(f"[eval] 数据集 {args.dataset}：{len(examples)} 条（含 reference {n_ref} 条）｜"
          f"评估器 {' + '.join(e.__name__ for e in evaluators)}"
          f"（factuality: {judge_backend}）｜max_concurrency=2")
    print(f"[eval] 额度提示：预计产生 {len(examples) * 10}~{len(examples) * 50} 条 trace")

    t0 = time.monotonic()
    # target 是 async 函数（引擎走 ainvoke/astream），langsmith 要求用 aevaluate 而非 evaluate
    results = asyncio.run(
        aevaluate(
            target,
            data=examples,
            evaluators=evaluators,
            max_concurrency=2,          # 控并发：保护免费额度与下游 LLM 限流
            experiment_prefix=args.prefix,
        )
    )
    print(f"[done] 用时 {time.monotonic() - t0:.0f}s，实验：{results.experiment_name}")
    print("[done] 打开 LangSmith → Datasets & Experiments → 该数据集 → Experiments 页签查看得分、"
          "跨轮对比与图表；截图沉淀到 doc/智能体/智能体评估/")


if __name__ == "__main__":
    main()
