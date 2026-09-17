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
  python scripts/langsmith/eval_langsmith.py --concurrency 1          # 串行跑（本地模型加载慢时用）
  python scripts/langsmith/eval_langsmith.py --timeout 120            # 单条 120s 超时（卡点定位用）
  python scripts/langsmith/eval_langsmith.py --no-log                 # 不落盘日志

日志：默认在脚本旁写 eval_时间戳.log，控制台同步输出（用 tqdm.write，不被进度条吞）。
每条打印 [序号] 开始/完成 → 各节点启动/完成 → 三个评估器得分；卡住时最后一行即卡点。
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
from datetime import datetime  # noqa: E402
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

DEFAULT_DATASET = "multi-agent-regression"   # UI 已建数据集；建议改为语义化名称（如 multi-agent-取证回归）

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


_run_timeout = 300    # 单条 Example 的整体超时（秒）：见 target() 说明
_total = 0            # 本轮 Example 总数（main 里设置，仅用于日志编号）
_seq = 0              # 已完成/正在执行的条数
_log_path: Path | None = None


def log(msg: str) -> None:
    """统一日志：控制台实时输出 + 同内容落盘。

    控制台用 tqdm.write 而非 print——langsmith 的进度条会吞掉裸 print；
    同时 flush 保证卡住时也能看到最后一行（定位卡在哪个节点）。
    """
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    try:
        from tqdm import tqdm
        tqdm.write(line)
    except Exception:
        print(line, flush=True)
    if _log_path is not None:
        try:
            with _log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


async def _pump_events(engine, stop: asyncio.Event, sink: list) -> None:
    """边跑边消费引擎事件队列，实时打印节点进度。

    卡住时最后一行日志就是"卡在哪个节点"的直接证据；事件同时存进 sink，
    供 build_trajectory 组装轨迹（队列只能消费一次）。
    """
    while not stop.is_set():
        try:
            evt = engine.q.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.sleep(1)
            continue
        sink.append(evt)
        kind = evt.get("type")
        if kind == "node_start":
            log(f"    ├ 启动 {evt.get('node')}（{evt.get('role')}）")
        elif kind == "node_done":
            log(f"    └ 完成 {evt.get('node')}：{evt.get('summary', '')}")


async def target(inputs: dict) -> dict:
    """单条入口：引擎全流程 + 整体超时兜底。

    引擎内部有同步阻塞调用（向量检索 / 本地模型推理），Milvus 或向量库不可达时
    可能挂起且不自愈——没有整体超时的话，一条卡死就会拖住整轮（进度条不再前进）。
    这里给每条设上限，超时抛错由 LangSmith 记为该 run 的 error，整轮继续跑。
    """
    try:
        return await asyncio.wait_for(_run_engine(inputs), timeout=_run_timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"单条超过 {_run_timeout}s 未完成（引擎节点或向量检索挂起），已放弃该条"
        )


async def _run_engine(inputs: dict) -> dict:
    global _seq
    task = _extract_task(inputs)
    if not task:
        keys = list(inputs.keys()) if isinstance(inputs, dict) else type(inputs).__name__
        raise ValueError(
            f"Example inputs 缺少任务描述（期望 {{\"task\": \"...\"}}，实际 keys={keys}）。"
            f"请在 LangSmith UI 修正该 Example 的 inputs，或删除数据集后用 --seed 重新播种。"
        )
    _seq += 1
    seq = _seq
    log(f"[{seq}/{_total}] 开始：{task[:48]}…")

    scenario = UniversalScenario()
    engine = await scenario.build_engine_from_task(task)

    events: list[dict] = []
    stop = asyncio.Event()
    pump = asyncio.create_task(_pump_events(engine, stop, events))
    t0 = time.monotonic()
    try:
        final = await engine.run()      # engine.py: run() -> 终态 dict
    finally:
        stop.set()
        await pump

    conclusion = final.get("conclusion", "")
    facts = final.get("facts", [])
    log(f"[{seq}/{_total}] 完成：{(time.monotonic() - t0):.0f}s｜"
        f"结论 {len(conclusion)} 字｜事实卡 {len(facts)} 张｜事件 {len(events)} 条")
    return {
        "conclusion": conclusion,
        "facts": facts,
        "verdict": final.get("verdict", {}),
        "elapsed_ms": final.get("elapsed_ms"),
        "trajectory": build_trajectory(events, task, conclusion),
    }


def build_trajectory(events: list[dict], task: str, conclusion: str) -> list[dict]:
    """把引擎事件流还原成 agentevals 轨迹（OpenAI messages 形态）。

    事件由 _pump_events 边跑边收集（队列只能消费一次）。映射规则：
      user               = 用户任务
      assistant+tool_call = 节点启动（工具名=节点名，入参=子任务目标）
      tool               = 节点完成（返回=节点产出摘要）
      assistant          = 最终结论
    这样 judge 看到的不是"结论对不对"，而是"这条执行路径合不合理"。
    """
    msgs: list[dict] = [{"role": "user", "content": task}]
    for evt in events:
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


def warmup_local_models() -> None:
    """跑批前先把本地嵌入模型加载好。

    两个坑绑在一起，不预热就会表现为"卡住"：
    1. 本地嵌入模型是同步加载，会阻塞 event loop，加载期间进度条纹丝不动；
    2. create_embeddings() 是全局单例但没锁，并发两条 Example 会同时进入加载分支，
       于是同一份权重被加载两次（控制台能看到两次 Loading weights）。
    预热放在 asyncio.run 之前，串行加载一次，后续并发直接命中单例。
    """
    try:
        from providers.embedding import create_embeddings
        create_embeddings().embed_query("预热")
        log("[warmup] 本地嵌入模型已加载")
    except Exception as e:
        log(f"[warn] 嵌入模型预热失败（检索节点会自行降级）：{e}")


# ── 3. evaluator ①：引用可溯源（确定性，零成本） ─────────────────────
def _log_eval(res: dict) -> dict:
    """评估器统一出口：打一行分，再返回（comment 截断避免刷屏）。"""
    comment = str(res.get("comment") or "")
    log(f"    · {res['key']}={res['score']}｜{comment[:110]}")
    return res


def target_failed_reason(run) -> str | None:
    """target 跑挂（超时/异常）时返回原因，正常返回 None。

    必须显式判：citation_valid 遇到空输出会得出"无事实卡且无引用 → 合规"= 满分，
    不判的话一条卡死的 Example 反而拿 1.0，把均分抬上去。
    """
    err = getattr(run, "error", None)
    if err:
        return f"target 执行失败：{err}"
    if not run.outputs:
        return "target 无输出（超时或异常）"
    return None


def citation_valid(run, example) -> dict:
    reason = target_failed_reason(run)
    if reason:
        return _log_eval({"key": "citation_valid", "score": 0, "comment": reason})
    outputs = run.outputs or {}
    answer, facts = outputs.get("conclusion", ""), outputs.get("facts", [])
    cited = {int(n) for n in re.findall(r"\[事实(\d+)\]", answer)}
    if not facts:
        ok = not cited                                  # 无事实卡时不允许出现引用
    else:
        ok = bool(cited) and max(cited) <= len(facts)
    return _log_eval({"key": "citation_valid", "score": int(ok),
                      "comment": f"引用 {sorted(cited) or '无'}｜事实卡 {len(facts)} 张"})


# ── 4. evaluator ②：事实正确性（LLM-as-judge） ──────────────────────
# 首选 openevals 的 create_async_llm_as_judge：结构化输出取分（不用手写正则解析 JSON）、
# 自带 reasoning 便于 UI 里看判分理由；judge 直接复用 providers.create_llm() 的 LLM 实例，
# 因此与业务走同一套 .env 配置（含自定义 base_url / 国内兼容模型）。
# 注意：prompt 是 str 模板，openevals 用 str.format 填充，除 {inputs}/{outputs}/
# {reference_outputs} 外不能再出现花括号。
FACTUALITY_PROMPT = (
    "你是航空运行领域的评分裁判，判断【系统结论】与【参考答案】是否事实一致。\n"
    "判分口径：允许表述不同，不允许事实相悖；结论中参考答案未覆盖的内容，"
    "只要不与参考答案冲突，不算相悖。对原则、要点、流程类内容的归纳，"
    "允许同义重组与措辞差异，按语义重合判断而非字面重合。\n\n"
    "【用户任务】\n{inputs}\n\n"
    "【参考答案】\n{reference_outputs}\n\n"
    "【系统结论】\n{outputs}\n\n"
    "只输出一个 JSON 对象，不要 Markdown 代码块和多余文字，格式如下：\n"
    "{{\"reasoning\": \"一句话判分理由\", \"score\": true 或 false}}\n"
    "score 为 true 表示事实一致。"
)

_factuality_judge = None      # main() 里构造；为 None 时走本地回退


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
        def with_structured_output(self, schema=None, *, include_raw=False, **kwargs):
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
        log("[warn] 未安装 openevals（pip install openevals），factuality 走本地手写 judge")
        return None
    from providers import create_llm                  # 与业务同一 LLM 工厂
    if create_llm() is None:                          # 工厂可返回 None（未配置时）
        log("[warn] create_llm() 返回空，factuality 走本地手写 judge："
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
    reason = target_failed_reason(run)
    if reason:
        return _log_eval({"key": "factuality", "score": 0, "comment": reason})
    reference = (example.outputs or {}).get("reference", "")
    if not reference:
        return _log_eval({"key": "factuality", "score": 0,
                          "comment": "该 Example 未标注 reference，跳过 judge"})
    answer = (run.outputs or {}).get("conclusion", "")
    if _factuality_judge is None:
        return _log_eval(await _legacy_factuality_judge(answer, reference))
    try:
        return _log_eval(await _factuality_judge(
            inputs=_extract_task(run.inputs),
            outputs=answer,
            reference_outputs=reference,
        ))
    except Exception as e:
        # judge 故障不静默记 0 分：0 分会被误读成「事实错误」，这里带上原因便于在 UI 排查
        return _log_eval({"key": "factuality", "score": 0, "comment": f"judge 失败: {e}"})


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

_trajectory_judge = None      # main() 里构造；为 None 时跳过该评估器


def build_trajectory_judge():
    """构造 agentevals 轨迹 judge。返回 None 表示不可用（未装包 / LLM 未配置）。"""
    try:
        from agentevals.trajectory.llm import create_async_trajectory_llm_as_judge
    except ImportError:
        log("[warn] 未安装 agentevals（pip install agentevals），跳过 trajectory 评估")
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
    reason = target_failed_reason(run)
    if reason:
        return _log_eval({"key": "trajectory_valid", "score": 0, "comment": reason})
    if _trajectory_judge is None:
        return _log_eval({"key": "trajectory_valid", "score": 0,
                          "comment": "agentevals 不可用（未安装或 LLM 未配置），跳过轨迹评估"})
    traj = (run.outputs or {}).get("trajectory") or []
    if len(traj) < 3:                                 # 只有 user + 结论，说明节点没跑起来
        return _log_eval({"key": "trajectory_valid", "score": 0,
                          "comment": "轨迹过短（节点未启动），判为异常"})
    try:
        return _log_eval(await _trajectory_judge(outputs=traj))
    except Exception as e:
        return _log_eval({"key": "trajectory_valid", "score": 0, "comment": f"轨迹 judge 失败: {e}"})


# ── 6. 主流程 ────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="LangSmith 多智能体回归评估")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="数据集名称")
    parser.add_argument("--limit", type=int, default=0, help="只取前 N 条（0=全量），控额度用")
    parser.add_argument("--seed", action="store_true", help="数据集不存在时播种示例")
    parser.add_argument("--reseed", action="store_true",
                        help="删除并重建数据集（现有 Example 结构不对时用，如 UI 手建数据）")
    parser.add_argument("--prefix", default="regression", help="实验名前缀（跨轮对比按前缀分组）")
    parser.add_argument("--concurrency", type=int, default=2,
                        help="并发条数（默认 2；本地模型加载慢或下游限流时调 1）")
    parser.add_argument("--timeout", type=int, default=300,
                        help="单条超时秒数（默认 300；向量检索/引擎挂起时该条记 error 并继续）")
    parser.add_argument("--no-log", action="store_true",
                        help="不落盘日志文件（默认在脚本旁写 eval_时间戳.log）")
    args = parser.parse_args()

    # 日志先初始化：预热与 judge 构造的提示也要进文件
    global _run_timeout, _log_path
    _run_timeout = args.timeout
    if not args.no_log:
        _log_path = _SCRIPT_DIR / f"eval_{datetime.now():%Y%m%d_%H%M%S}.log"
        log(f"[log] 日志文件：{_log_path}")

    # 本地模型（嵌入/精排）同步加载会阻塞 event loop，先进 event loop 前预热一次
    warmup_local_models()

    # judge 在跑批前构造一次（内部复用 providers 的 LLM 单例），失败则本地回退/跳过
    global _factuality_judge, _trajectory_judge
    _factuality_judge = build_openevals_judge()
    _trajectory_judge = build_trajectory_judge()

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

    global _total
    _total = len(examples)                            # 供日志编号 [n/总数]
    n_ref = sum(1 for e in examples if (e.outputs or {}).get("reference"))
    judge_backend = "openevals" if _factuality_judge is not None else "本地手写（回退）"
    evaluators = [citation_valid, factuality_judge]
    if _trajectory_judge is not None:
        evaluators.append(trajectory_judge)
    log(f"[eval] 数据集 {args.dataset}：{len(examples)} 条（含 reference {n_ref} 条）｜"
        f"评估器 {' + '.join(e.__name__ for e in evaluators)}"
        f"（factuality: {judge_backend}）｜max_concurrency={args.concurrency}｜"
        f"单条超时 {args.timeout}s")
    log(f"[eval] 额度提示：预计产生 {len(examples) * 10}~{len(examples) * 50} 条 trace；"
        f"预估 {len(examples) * 70 // max(1, args.concurrency)}~"
        f"{len(examples) * 120 // max(1, args.concurrency)}s")
    log("[eval] 每条日志格式：[序号] 开始/完成 → 各节点启动/完成 → 三个评估器得分；"
        "卡住时最后一行即卡点所在")

    t0 = time.monotonic()
    # target 是 async 函数（引擎走 ainvoke/astream），langsmith 要求用 aevaluate 而非 evaluate
    results = asyncio.run(
        aevaluate(
            target,
            data=examples,
            evaluators=evaluators,
            max_concurrency=args.concurrency,   # 控并发：保护免费额度与下游 LLM 限流
            experiment_prefix=args.prefix,
        )
    )
    log(f"[done] 用时 {time.monotonic() - t0:.0f}s，实验：{results.experiment_name}")
    log("[done] 打开 LangSmith → Datasets & Experiments → 该数据集 → Experiments 页签查看得分、"
        "跨轮对比与图表；截图沉淀到 doc/智能体/智能体评估/")
    if _log_path is not None:
        log(f"[done] 日志已落盘：{_log_path}")


if __name__ == "__main__":
    main()
