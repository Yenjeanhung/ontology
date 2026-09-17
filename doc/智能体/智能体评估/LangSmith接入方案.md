# LangSmith 接入方案：多智能体取证系统可观测性与评估体系

> 适用范围：`backend/services/multi_agent`（通用智能体团队场景 `UniversalScenario`）
> 文档版本：v1.0（2026-09）

---

## 1. 目标与收益

| 能力 | 接入前 | 接入后 |
|---|---|---|
| **执行可观测** | 前端 SSE 事件流，无法回看历史 | 每次运行的完整 trace 树（每个节点的 LLM 输入/输出、耗时、token 花销）云端留存，可分享链接 |
| **质量评估** | 无系统性评估，改 prompt 靠感觉 | golden 数据集 + 自动化评估（确定性校验 + LLM-as-judge），每轮迭代回归跑分 |
| **回归防护** | 无 | 评估不达标即发现 prompt/检索改动引入的退化 |

**选型结论**：直接使用 LangSmith 云端 Developer 免费计划（1 用户、5,000 traces/月、14 天保留），不做本地部署。理由：

1. 项目技术栈为 `langchain-core >= 0.3` + `langgraph`，**tracing 是框架原生能力**，设两个环境变量即生效，业务代码零改动；
2. 个人项目数据无合规约束，Self-Hosted Lite 那套容器栈（企业内网合规场景）收益为零；
3. 云端 trace 可生成分享链接，作品集/面试可直接展示。

---

## 2. 现状分析（为什么接入成本极低）

- 引擎：`MultiAgentEngine`（`engine.py`）基于 LangGraph 建图调度，`run()` 执行流水线，返回终态 dict：
  `context / plan / evidence（素材卡） / facts（事实卡） / verdict（Critic 裁定） / conclusion（成果文本，含 [事实N] 引用） / elapsed_ms`；
- LLM：`providers.create_llm()` 返回 `langchain-openai` 的 ChatOpenAI（OpenAI 兼容，含 DeepSeek/Qwen/智谱），所有节点经 `engine.llm_stream()` 调用；
- 配置：`config.py` 的 pydantic `Settings` + `load_dotenv`，`.env` 集中管理。

**关键结论**：`langchain-core` 内置 LangSmith 回调上报——只要进程环境变量里有
`LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY`，所有 `astream`/`ainvoke` 调用**自动**上报 trace，无需在业务代码里写任何埋点。

**需要补充埋点的边界**：不走 LangChain 的环节（Retriever 的向量检索 `_search_kb_chunks`、GraphAgent 的图谱查询 `_graph_facts`、DataAgent 的台账查询 `_data_facts`）默认不出现在 trace 树里，用 `@traceable` 装饰即可补全（阶段二，可选）。

---

## 3. 阶段一：Tracing 接入（半天，业务代码零改动）

### 3.1 安装依赖

```bash
pip install langsmith>=0.2.0
```

`requirements.txt` 的 `# LangChain 核心` 区块追加一行：

```
langsmith>=0.2.0        # LangSmith 可观测与评估（LANGSMITH_TRACING=true 时上报）
```

> 注：`langchain-core` 已将 `langsmith` 作为传递依赖引入，显式声明是为了锁定评估模块可用版本。

### 3.2 配置（`.env` 追加三行）

```env
# ── LangSmith 可观测（不设置 KEY 或 TRACING=false 时静默跳过，不影响本地运行）──
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_xxxxxxxxxxxx
LANGSMITH_PROJECT=ontology-multi-agent
```

- `config.py` 已有的 `load_dotenv(override=False)` 会把上述变量灌入进程环境，`langchain-core` 自动读取，**Settings 类可以不加字段**；
- 若希望集中校验，可在 `Settings` 中追加可选字段（非必需）：

```python
# LangSmith（可选：仅用于集中管理，框架直接读环境变量）
LANGSMITH_TRACING: bool = False
LANGSMITH_API_KEY: str = ""
LANGSMITH_PROJECT: str = "ontology-multi-agent"
```

### 3.3 验证

1. 启动后端，前端发起一次"通用智能体团队"取证任务；
2. 打开 https://smith.langchain.com → 项目 `ontology-multi-agent`，应能看到本次运行：
   - LangGraph 图执行（planner → Retriever/Worker/GraphAgent → critic → synthesizer）；
   - 每个节点的 `llm_stream` 调用：完整 system/user prompt、流式输出、首 token 延迟、token 用量；
3. 在 trace 页右上角生成 **分享链接**，存入作品集。

### 3.4 额度纪律（免费 5,000 traces/月）

- 一次多智能体任务约产生 10~50 条 trace（LLM 调用次数决定），日常开发够用；
- 批量/压测/演示性重复点击前，临时将 `LANGSMITH_TRACING=false`；
- trace 14 天过期，**代表性运行截图存档**。

---

## 4. 阶段二：评估体系（1~2 天）

### 4.1 评估维度设计

| # | 维度 | 方法 | 类型 | 防什么 |
|---|---|---|---|---|
| 1 | **引用可溯源率** | 成果文本 `[事实N]` 编号 vs 终态 facts 数量/内容对照 | 确定性校验（纯 Python） | 幻觉引用：引用了不存在的事实 |
| 2 | **事实正确性** | conclusion 与标注参考答案一致性 | LLM-as-judge | 结论与知识库/图谱事实相悖 |
| 3 | **素材充分性** | verdict 判定"证据足够"时 facts+evidence 是否达标 | 规则复用 + judge | Critic 放水：素材不足仍放行 |

设计原则：**能用确定性校验就不用 LLM**（机器可验证、零成本、防抖动）；LLM 只裁"语义对不对"。

### 4.2 Golden 数据集

来源两部分，规模建议 10~20 条：

1. `universal.py` 的 `EXAMPLES` 预设任务（现成种子）；
2. 人工补充边界用例：无图谱命中任务、素材不足任务（考 Critic 兜底）、多跳关系任务（考 GraphAgent 价值）。

每条样例：`inputs={"task": ...}` + `outputs={"reference": 人工参考答案}`。
参考答案可基于知识库文档与图谱实体人工撰写——这份数据集本身即是交付物。

### 4.3 脚本落位与实现

新建 `backend/scripts/eval_langsmith.py`（依赖 `backend` 包可导入，在 `backend/` 目录下执行）：

```python
"""LangSmith 评估脚本：多智能体取证系统回归评估。
用法：cd backend && python -m scripts.eval_langsmith
前置：.env 已配置 LANGSMITH_API_KEY；知识库/图谱已导入数据。
"""
import asyncio
import re

import langsmith as ls
from langsmith.evaluation import evaluate

from scenarios.universal import UniversalScenario   # 按项目实际导入路径调整

DATASET = "multi-agent-取证回归"


# ── 1. 数据集：首次运行创建，之后复用 ─────────────────────────
def ensure_dataset(samples: list[dict]):
    client = ls.Client()
    try:
        return client.read_dataset(dataset_name=DATASET)
    except ls.LangSmithNotFoundError:                  # 首次创建
        ds = client.create_dataset(DATASET, description="多智能体取证系统回归集")
        client.create_examples(
            dataset_id=ds.id,
            inputs=[{"task": s["task"]} for s in samples],
            outputs=[{"reference": s["reference"]} for s in samples],
        )
        return ds


# ── 2. target：包装引擎为可评估函数（非流式取终态） ────────────
async def target(inputs: dict) -> dict:
    scenario = UniversalScenario()
    engine = await scenario.build_engine_from_task(inputs["task"])
    final = await engine.run()                          # engine.py: run() -> 终态 dict
    return {
        "conclusion": final.get("conclusion", ""),
        "facts": final.get("facts", []),
        "verdict": final.get("verdict", {}),
        "elapsed_ms": final.get("elapsed_ms"),
    }


# ── 3. evaluator ①：引用可溯源（确定性，零成本） ──────────────
def citation_valid(run, example) -> dict:
    answer, facts = run.outputs["conclusion"], run.outputs["facts"]
    cited = {int(n) for n in re.findall(r"\[事实(\d+)\]", answer)}
    if not facts:
        ok = not cited                                   # 无事实卡时不允许出现引用
    else:
        ok = bool(cited) and max(cited) <= len(facts)
    return {"key": "citation_valid", "score": int(ok)}


# ── 4. evaluator ②：事实正确性（LLM-as-judge，复用项目 LLM 配置） ──
async def factuality_judge(run, example) -> dict:
    from providers import create_llm                     # 与业务同一 LLM 工厂
    llm = create_llm()
    prompt = (
        "判断【结论】与【参考答案】是否事实一致（允许表述不同，不允许事实相悖）。\n"
        f"【参考答案】\n{example.outputs['reference']}\n\n"
        f"【结论】\n{run.outputs['conclusion']}\n\n"
        "只回答 JSON：{\"consistent\": true/false, \"reason\": \"一句话理由\"}"
    )
    try:
        resp = await llm.ainvoke(prompt)
        verdict = re.search(r"\{.*\}", resp.content, re.S).group()
        score = 1 if '"consistent": true' in verdict.lower() else 0
        return {"key": "factuality", "score": score, "comment": verdict}
    except Exception as e:
        return {"key": "factuality", "score": 0, "comment": f"judge 失败: {e}"}


# ── 5. 主流程 ────────────────────────────────────────────────
if __name__ == "__main__":
    samples = [   # 首次创建数据集时的样例；之后可从 LangSmith UI 维护
        {"task": "……（EXAMPLES 预设或自建用例）……", "reference": "……"},
    ]
    ensure_dataset(samples)
    evaluate(
        target,
        data=DATASET,
        evaluators=[citation_valid, factuality_judge],
        max_concurrency=2,        # 控并发：保护免费额度与下游 LLM 限流
        experiment_prefix="regression",
    )
```

> 实施时按真实情况微调：`import scenarios.universal` 的路径（项目运行根目录）、
> `resp.content` 的取值形态（ChatOpenAI 返回 string 内容）、异常分支。

### 4.4 产物与使用方式

- 每次 `evaluate()` 产出一次 **experiment**，LangSmith UI 中按列对比历次得分与耗时；
- 团队约定：改动节点 prompt / 检索参数（`SIMILARITY_THRESHOLD` 等）/ 图谱策略后，跑一轮回归，三个指标不回退方可合入；
- 得分截图与分享链接沉淀到本目录（`doc/智能体/智能体评估/`），作为作品集证据。

---

## 5. 阶段三（可选增强）

| 增强 | 说明 | 成本 |
|---|---|---|
| **非 LangChain 环节补 trace** | `_search_kb_chunks` / `_graph_facts` / `_data_facts` 加 `from langsmith import traceable; @traceable(name="graph_search")`，trace 树补全检索细节 | 每处 1 行装饰器 |
| **定时回归** | 项目已有 `apscheduler` 调度引擎，注册 cron 任务夜间跑评估，早上看报告 | 半天 |
| **评估指标看板** | experiment 得分导出 CSV，前端或 notebook 画趋势 | 半天 |

---

## 6. 配置与依赖清单

```env
# .env 追加
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_xxx
LANGSMITH_PROJECT=ontology-multi-agent
```

```
# requirements.txt 追加
langsmith>=0.2.0
```

账号：https://smith.langchain.com 注册（免费 Developer 计划），Settings → API Keys 生成 key。

---

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| 免费额度 5,000 traces/月刷爆 | 批量操作前关 `LANGSMITH_TRACING`；评估脚本 `max_concurrency=2`；监控 UI 用量页 |
| trace 14 天过期丢失证据 | 代表性 trace 与评估报告**截图存档**到本目录 |
| judge 与被评对象同源偏差（同一 LLM 既执行又打分） | 认知局限记录在案；条件允许时 judge 换用另一模型（`.env` 加 judge 专属配置） |
| 无 Key 时静默跳过导致"以为在记录" | 启动日志打印一次 tracing 状态；上线检查清单加一条 |
| 评估脚本对引擎接口的耦合 | target 只依赖 `build_engine_from_task` + `run()` 两个稳定入口，不碰节点内部 |

---

## 8. 面试叙事（一句话版本）

> "可观测与评估直接建在 LangSmith 上：项目是 LangGraph 栈，两个环境变量就完成 tracing 接入，零业务代码侵入；评估上我设计了三层指标——引用可溯源率用确定性校验防幻觉引用、事实正确性用 LLM-as-judge 对照人工标注集、素材充分性复用 Critic 硬规则——每轮 prompt 或检索策略迭代后回归跑分，防止性能回退。"

---
*实施顺序建议：阶段一（半天）上线验证 → 补 golden 数据集（1~2h 人工）→ 阶段二脚本（半天）→ 首轮回归跑分并截图存档。*
