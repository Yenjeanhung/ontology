# 工作流模块面试题（LangGraph 执行引擎）

> 依据本仓库实际实现整理：`backend/services/workflow_engine.py`（LangGraph 引擎）、`backend/routers/workflow.py`（API）、`front/src/components/workflow/`（Vue Flow 画布）。
> 每题附「答案要点」，可作为面试官参考答案或候选人自测。

---

## 一、基础与选型

### 1. 这个工作流系统为什么选择 LangGraph，而不是 Airflow、Temporal 或自研拓扑排序？

**要点：**
- 场景是「LLM 节点组成的 DAG + 流式进度 + 人工审批」，Airflow 是批处理调度、Temporal 是长事务编排，都偏重；
- LangGraph 原生提供：StateGraph 建图、`add_conditional_edges` 分支路由、同一 superstep 内无依赖节点**自动并行**、`astream` 流式推进、thread_id/checkpointer 断点恢复预留；
- 项目历史上确实自研过拓扑排序引擎（v0），2026-08 迁移到 LangGraph：调度正确性（并行、汇聚）交给框架，自研层只保留「节点执行器 + 变量渲染 + SSE 事件契约」，事件类型与旧引擎完全一致，前端零改动。

### 2. 工作流的「定义（definition）」和「运行（run）」分别是怎么持久化的？

**要点：**
- 定义 = `{nodes: [...], edges: [...]}` JSON，由前端 Vue Flow 画布的 nodes/edges 序列化而来，存 `workflows` 表；
- 运行 = `workflow_runs` 表：status（running/waiting/succeeded/failed）、inputs、outputs、node_states（每节点状态 JSON）、context_snapshot、definition_snapshot、duration_ms、trigger_source；
- 每个工作流只保留最近 N 次运行（`WorkflowRunService.trim` 裁剪）。

---

## 二、LangGraph 引擎核心

### 3. JSON 定义是怎么「翻译」成 LangGraph 图的？每次运行都重建图，性能可以接受吗？

**要点：** 见 `_build_graph(rt)`：
- 找到 `start` 类型节点，`g.add_edge(START, start_id)`；
- 所有节点 `g.add_node(id, 闭包)`——闭包持有 `_Runtime` 上下文；
- 非条件节点：逐条出边 `add_edge`；`condition`/`human` 节点：`add_conditional_edges` 挂路由函数；
- `end` 节点连到 `END`；编译 `g.compile()`。
- 性能：建图+编译是纯内存操作，毫秒级；每次运行重建换来的是**无需缓存失效逻辑**（定义随时改，运行时取当前定义），这笔交易划算。

### 4. 图状态（GraphState）是怎么设计的？并行节点同时写状态会不会互相覆盖？

**要点：**
```python
class GraphState(TypedDict):
    start: dict
    outputs: Annotated[dict, _merge_outputs]   # reducer

def _merge_outputs(left, right):
    return {**(left or {}), **(right or {})}
```
- `outputs` 用 `Annotated` 声明 reducer，LangGraph 在 superstep 提交时自动按 node_id 键合并并行节点的写入，不覆盖；
- 这是 LangGraph 并行语义的关键考点：没有 reducer 的字段在并行写时会报 `InvalidUpdateError`。

### 5. LangGraph 的 superstep（超步）模型是什么？和普通 DAG 调度的区别？

**要点：**
- LangGraph 按 superstep 推进：同一波「入边全部就绪」的节点在同一个 superstep 内并发执行，全部结束后状态统一 reducer 合并，再进入下一波；
- 对业务的意义：`condition` 路由返回多条目标边时可以**并行扇出**（`router -> list[str]`）；
- 多入边节点的触发语义：任一到达边触发即执行（Pregel 模型），不是「等所有上游」——这一点和某些 BPMN 的 join 语义不同，设计文档里明确记录过。

### 6. 节点闭包是怎么把「业务执行」和「框架调度」解耦的？

**要点：** `_make_node_fn(rt, node)` 返回 `async fn(state)`：
- 先查 `rt.inject`（人工结果注入）→ 直接产出输出，不重跑；
- 再查 `rt.replay`（已完成节点快照）→ O(1) 返回，并保留条件路由所需的上游输出；
- 否则常规执行：构造输入视图 → `rt.emit(node_started)` → 按 type 分发执行器（`asyncio.create_task`）→ 成功 `node_finished` / 失败 `node_failed` 后 `raise`（fail-fast 终止整图）。
- 好处：LangGraph 只看到「一个返回 dict 增量的异步函数」，SSE、重放、注入等横切逻辑全部收在闭包和 `_Runtime` 里。

### 7. SSE 事件是怎么从并发节点里汇到一条 HTTP 流上的？

**要点：**
- 每次运行一个 `_Runtime`，核心是 `events: asyncio.Queue`；
- `_drive()` 后台任务里 `graph.astream(...)` 推进执行，节点闭包通过 `rt.emit()` 把事件 put 进队列；
- `run_stream` 生成器主循环 `await asyncio.wait_for(rt.events.get(), timeout=0.2)` 消费队列，逐条 yield 成 `data: {...}\n\n`，最后 `data: [DONE]`；
- 事件契约：`workflow_started / node_started / node_progress / node_finished / node_failed / node_skipped / node_waiting / node_resumed / node_replayed / workflow_finished / [DONE]`。

### 8. 流式 LLM 节点的 progress 事件为什么做 80ms 节流？非流式节点怎么办？

**要点：**
- `_on_token` 里 `now - last_progress_emit < 0.08` 直接丢弃：高频 token 事件会把 SSE 和前端响应式渲染打爆；
- 非流式节点（service/code/http）：执行期间 `asyncio.wait({task}, timeout=2.0)` 循环等待，超时未完成就发一次心跳 `node_progress`，让前端知道节点还活着；
- 流式节点首 token 到达时记录日志，用于观测首字延迟。

### 9. condition 节点的分支路由是怎么实现的？未选中的下游节点会怎样？

**要点：**
- `_make_condition_router`：从 edges 里收集该节点 `handle=true/false` 的出边；
- 路由函数读 `state['outputs'][nid]['result']`，返回目标节点 id **列表**（多条边→并行扇出）；
- 未选中分支的下游 LangGraph 根本不会调度；运行收尾时统一补发 `node_skipped` 事件，前端把这些节点画成灰色。

---

## 三、人工节点：挂起与续跑（本项目最有区分度的部分）

### 10. 人工审批节点是怎么让「执行中的图」停下来等人的？为什么不用 LangGraph 的 interrupt？

**要点：**
- 自研方案：人工节点在闭包里走 `_suspend_at_human_node`：渲染待审内容 → `HumanTaskService.create` 落库待办 → `emit(node_waiting)` → `raise _NodeSuspended`；
- `_NodeSuspended` 一路上抛终止整图，`_drive()` 捕获后 run 置 `waiting` 而非 failed；
- 不用 `interrupt()` 的原因（可展开讨论）：`interrupt` 依赖 checkpointer 持久化线程状态，项目当时没有启用 checkpointer，选择了「快照落库」这条更直白的路——`_suspend_run` 把 `context_snapshot`（`state['outputs']` + start 输入）、`definition_snapshot`、`node_states` 全部写进 `workflow_runs`；
- 追问点：`langgraph-checkpoint-sqlite` 已在 requirements 里预留，后续可平滑切换。

### 11. 挂起信号为什么用异常而不是返回值？中间踩过什么坑？

**要点：**
- 挂起要穿透 LangGraph 的调度栈直接终止整图，异常是最短路径；
- **坑**：LangGraph 可能把节点异常包装进 `ExceptionGroup`（Python 3.11，它不是 `Exception` 的子类），`except Exception` 接不住，挂起会被误判成运行失败；
- 解法：`_is_suspend_error` 递归解包 `e.exceptions`，且 `_drive`/收尾处 `except BaseException`，配合 `rt.suspend is not None` 双重判定。

### 12. 审批完成后怎么续跑？已完成节点会不会被重新执行？

**要点：** `resume_run_stream(run_id, task_id)`：
- 校验 run 状态必须是 `waiting`、task 属于该 run 且已处理；
- **状态抢占**：`UPDATE workflow_runs SET status='running' WHERE id=? AND status='waiting'`，`rowcount==0` 说明并发续跑已被别人抢到，直接拒绝——用单条条件 UPDATE 保证幂等；
- 组装 `_Runtime(replay=快照outputs, inject={pending_node_id: 决策结果})`，重建图再跑一遍；
- 已完成节点命中 `replay` 直接 O(1) 返回快照（不重跑、不发重复执行副作用），人工节点命中 `inject` 直接产出审批结果；
- 耗时口径：`extra_duration_ms` 累加挂起前的真实耗时，**等待人工的时长不计入执行时长**。

### 13. 快照恢复时为什么必须取 `state['outputs']` 而不能用运行时 context 反推？

**要点：** 挂起时 `context` 里 `'start'` 是工作流入参，而默认开始节点的 id 就叫 `start`，两者会冲突覆盖；所以 `_suspend_at_human_node` 直接拿 LangGraph 当前 `state` 的 `outputs` 和 `start` 落库。这是典型的「变量作用域命名冲突」案例。

---

## 四、工程实践与并发

### 14. 为什么并行节点不能共用一个 DB 会话？

**要点：**
- LangGraph 在同一 superstep 内并发执行无依赖节点，节点任务共享 `rt.db`（同一条 asyncpg 连接）时，并发 SQL 抛 `InterfaceError: another operation in progress`；
- 解法：除人工节点（串行挂起建单，用 `rt.db`）外，每个节点执行都 `async with async_session()` 开独立会话；
- 考点延伸：异步连接池的「连接=会话」模型、并发与连接生命周期的匹配。

### 15. 变量在节点间是怎么传递的？`{{node.field}}` 和 `var("node","field")` 有什么区别？

**要点：**
- 每个节点执行时 `context = {**state['outputs'], 'start': ...}`，`render()` 对配置里的字符串做模板渲染；
- 整串恰好是 `{{...}}`/`var(...)` → 保留原类型返回（数字还是数字）；嵌入在文本里 → 字符串化替换；
- 字段缺失时保留表达式原样不动（方便排查），兼容 `{data: {...}}` 包装与顶层字段两种输出形态；
- OAG 返回的 `[来源N]/[事实]` 引用标记会被递归剥掉。

### 16. 如果要支持循环（Loop/ForEach）节点，LangGraph 下怎么扩展？

**要点（开放题）：**
- LangGraph 原生支持环：路由函数返回已访问节点即可形成循环，配递归上限（`recursion_limit`）防死循环；
- ForEach 可展开为「子图 + 动态 add_node」或循环计数写进 GraphState；
- 还要处理：循环体的输出累积（reducer 追加 list）、变量作用域（迭代变量）、与挂起/续跑机制的兼容（快照需含循环游标）。

### 17. 前端画布用的 Vue Flow 有哪些工程细节值得注意？

**要点：**
- `nodeTypes` 用 `markRaw` 包裹：组件对象进响应式代理会被 Vue Flow 内部 `h()` 渲染触发性能 warning；
- 悬空连线（指向已删除节点）要在保存前丢弃，否则 Vue Flow 渲染异常；
- 历史 definition 可能缺节点 id（undefined 会让内部 `.toString()` 崩掉），加载时兜底补 id；
- 拖拽新建节点用 `dataTransfer` + `screenToFlowCoordinate` 换算坐标。

### 18. 这个引擎目前的局限和下一步演进方向？

**要点（对照设计文档的非目标清单）：**
- 无循环节点、无子工作流嵌套、checkpointer 断点恢复未启用（thread_id 已按 run 隔离，预留好了）；
- 运行历史按工作流裁剪只留最近 N 次，无全量审计；
- 人工任务通知 v1 只落日志（`NotificationChannel` 预留了渠道抽象）；
- 并发调度依赖单进程 asyncio，多实例部署需把 SSE 队列外移（Redis）并加分布式锁。

---

## 五、快速追问（口头面试用）

1. `add_edge` 和 `add_conditional_edges` 分别适合什么拓扑？
2. GraphState 里去掉 `Annotated[..., _merge_outputs]` 会发生什么？
3. `thread_id: wf-{run_id}` 现在没有 checkpointer，为什么还要按 run 隔离？
4. 两个审批员同时点「通过」和「驳回」，系统如何保证只有一次生效？
5. 节点失败后 run 里怎么区分「失败节点」和「被跳过节点」？
6. 为什么 SSE 用 `stream_mode="updates"` 而事件却来自自己的队列？
7. 人工节点的 `context_snapshot` 如果存了 LLM 的完整中间推理，有什么隐私/体积问题？
8. 把引擎从每次重建图改成缓存编译结果，需要处理哪些失效条件？
