"""nl2sql_service：生成、校验、执行、重试（NL2SQL 受约束生成核心）。

方案：doc/智能体/多智能体/多智能体DataAgent·本体驱动NL2SQL.md §4~§5。

四级防线（永不信任 LLM）：
1. 生成约束：system 角色锁定「只产单条 SELECT」+ 方言 + 反模式红线 + few-shot；
2. 确定性校验 validate_sql：单语句/黑名单/表名白名单/LIMIT 纠偏/SELECT * 管控/
   RIGHT JOIN 归一化（sqlite）/隐式 join 与 NOT IN 拒绝——唯一放行人；
3. 只读执行 run_readonly：PG 会话只读 + statement_timeout、sqlite ro URI，
   外加 asyncio.wait_for + 行数截断双保险；
4. 报错回灌自修正循环：执行错误/校验拒绝回灌重试 ≤2 轮，首轮空结果触发一次
   「放宽条件自查」；仍失败返回 None，调用方回落 NL2Filter 老路。

双模式（§4.6）：pipeline（默认）/ react（显式思考链：复杂问题首轮即用，其余首轮
失败后自动升级）——同一循环骨架，只切回灌提示策略。
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
import time
from typing import Optional

from config import settings
from core.otel import async_span, set_llm_usage
from services.multi_agent.schema_store import prepare_nl2sql

logger = logging.getLogger(__name__)

MAX_ROWS = getattr(settings, "NL2SQL_MAX_ROWS", 500)
TIMEOUT_S = getattr(settings, "NL2SQL_TIMEOUT_S", 8.0)
MAX_RETRIES = getattr(settings, "NL2SQL_MAX_RETRIES", 2)

_BLACKLIST = [
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "merge", "grant", "revoke", "pragma", "attach", "detach", "vacuum",
    "load_extension", "copy",
]
_BLACKLIST_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _BLACKLIST) + r")\b", re.I)
_CTE_RE = re.compile(r"(?i)\bwith\s+(?:recursive\s+)?([a-zA-Z_]\w*)\s+as")
_TABLE_RE = re.compile(r"(?i)\b(?:from|join)\s+([a-zA-Z_]\w*)")
_SELECT_STAR_RE = re.compile(r"(?i)\bselect\s+(?:\w+\.)?\*")
_IMPLICIT_JOIN_RE = re.compile(r"(?i)\bfrom\s+[\"`]?\w+[\"`]?(?:\s+\w+)?\s*,")
_LIMIT_RE = re.compile(r"(?i)\blimit\s+(\d+)")
_RIGHT_JOIN_RE = re.compile(
    r"(?is)^(.*?\bfrom\b.+?)\s+right\s+(?:outer\s+)?join\s+"
    r"(\"?\w+\"?(?:\s+\w+)?)\s+on\s+(.+?)"
    r"(?=\s+(?:inner|left|right|full|cross|join|where|group\s+by|order\s+by|limit|having|union)\b|\s*$)")
_NOT_IN_RE = re.compile(r"(?i)\bnot\s+in\s*\(\s*select\b")
_FULL_OUTER_RE = re.compile(r"(?i)\bfull\s+(?:outer\s+)?join\b")
_REACT_HINTS = ("对比", "排名", "为什么", "占比", "top", "前几", "归因", "原因")

# ── few-shot（§5.4 九例；注入时按子图表交集选 4~6 例） ──
# (问题, SQL, 覆盖表)
FEW_SHOTS = [
    ("CA1501 航班的所有旅客及会员等级",
     'SELECT p.name AS "旅客姓名", m.level AS "会员等级", t.seat_no AS "座位号" FROM flights f '
     "JOIN tickets t ON f.flight_no = t.flight_no JOIN passengers p ON t.passenger_id = p.id "
     "LEFT JOIN members m ON p.member_id = m.member_id WHERE f.flight_no = 'CA1501'",
     {"flights", "tickets", "passengers", "members"}),
    ("统计每个航班的旅客数，没有旅客的航班也要列出",
     'SELECT f.flight_no AS "航班号", COUNT(t.ticket_no) AS "旅客数" FROM flights f '
     "LEFT JOIN tickets t ON f.flight_no = t.flight_no "
     'GROUP BY f.flight_no ORDER BY "旅客数" DESC',
     {"flights", "tickets"}),
    ("哪些航班没有旅客托运行李",
     'SELECT f.flight_no AS "航班号", f.dep_airport AS "起飞机场" FROM flights f '
     "LEFT JOIN tickets t ON f.flight_no = t.flight_no "
     "LEFT JOIN baggage b ON t.ticket_no = b.ticket_no WHERE b.bag_tag IS NULL",
     {"flights", "tickets", "baggage"}),
    ("平均票价超 1000 元的舱位及其客票量",
     'SELECT t.cabin AS "舱位", AVG(t.fare) AS "平均票价", COUNT(*) AS "客票量" FROM tickets t '
     'GROUP BY t.cabin HAVING AVG(t.fare) > 1000 ORDER BY "平均票价" DESC',
     {"tickets"}),
    ("金卡会员乘坐过的航班",
     'SELECT DISTINCT f.flight_no AS "航班号", f.dep_datetime AS "起飞时间" FROM members m '
     "JOIN passengers p ON m.member_id = p.member_id JOIN tickets t ON t.passenger_id = p.id "
     "JOIN flights f ON f.flight_no = t.flight_no WHERE m.level = '金卡'",
     {"members", "passengers", "tickets", "flights"}),
    ("按延误分档（≤15/15-60/>60）统计各档航班数",
     "SELECT CASE WHEN f.delay_min <= 15 THEN '轻微' WHEN f.delay_min <= 60 THEN '中度' "
     "ELSE '重度' END AS \"延误档位\", COUNT(*) AS \"航班数\" FROM flights f GROUP BY \"延误档位\"",
     {"flights"}),
    ("CA1501 的机长是谁",
     'SELECT c.name AS "姓名", ca.duty_role AS "岗位" FROM flights f '
     "JOIN crew_assignments ca ON f.flight_no = ca.flight_no "
     "JOIN crew_members c ON ca.crew_id = c.crew_id "
     "WHERE f.flight_no = 'CA1501' AND ca.duty_role = '机长'",
     {"flights", "crew_assignments", "crew_members"}),
    ("本月净增里程为正的金卡会员",
     'SELECT m.member_id AS "会员ID", m.name AS "姓名", SUM(r.miles) AS "净增里程" '
     "FROM mileage_records r JOIN members m ON r.member_id = m.member_id WHERE m.level = '金卡' "
     'GROUP BY m.member_id, m.name HAVING SUM(r.miles) > 0 ORDER BY "净增里程" DESC',
     {"mileage_records", "members"}),
    ("投诉未办结且满意度 ≤2 分的旅客名单",
     'SELECT p.name AS "姓名", p.phone AS "联系电话", s.request_no AS "单号", s.satisfaction AS "满意度" '
     "FROM service_requests s JOIN passengers p ON s.passenger_id = p.id "
     "WHERE s.type = '投诉' AND s.status != '已办结' AND s.satisfaction <= 2",
     {"service_requests", "passengers"}),
]


def _pick_few_shots(sub_tables: set) -> str:
    ranked = sorted(FEW_SHOTS, key=lambda fs: len(fs[2] & sub_tables), reverse=True)
    picked = [fs for fs in ranked if fs[2] & sub_tables][:6]
    if len(picked) < 4:
        picked = ranked[:4]
    return "\n\n".join(f"问题：{q}\nSQL：\n{sql}" for q, sql, _ in picked)


# ────────────────────── validate_sql（§4.2，唯一放行人） ──────────────────────

def validate_sql(sql: str, dialect: str, sub_tables: list) -> tuple:
    """确定性校验与归一化。返回 (ok, sql 或修复提示, notes)。"""
    notes: list = []
    s = (sql or "").strip()
    if not s:
        return False, "SQL 为空", notes

    s = s.rstrip().rstrip(";").strip()          # 剥尾分号
    if ";" in s:
        return False, "只允许一条 SELECT 语句（检测到多余分号）", notes

    m = _BLACKLIST_RE.search(s)
    if m:
        return False, f"禁止 {m.group(0).upper()} 写操作，只允许 SELECT", notes
    if _IMPLICIT_JOIN_RE.search(s):
        return False, "禁止隐式 join（FROM a, b），请改写为显式 JOIN ... ON ...", notes
    if _NOT_IN_RE.search(s):
        return False, "禁止 NOT IN (子查询)（NULL 陷阱），请改写为 NOT EXISTS 或 LEFT JOIN ... IS NULL", notes
    if _FULL_OUTER_RE.search(s) and dialect != "postgres":
        return False, f"{dialect} 不支持 FULL OUTER JOIN，请用 LEFT JOIN ... UNION ... 改写", notes

    rj_n = len(re.findall(r"(?i)\bright\s+(?:outer\s+)?join\b", s))
    if rj_n and dialect != "postgres":
        # 单个 RIGHT JOIN 才做确定性翻转（串联翻转需括号重排，正则不可靠 → 拒绝回灌）
        if rj_n == 1:
            m2 = _RIGHT_JOIN_RE.match(s)
            if m2:
                head = re.match(r"(?is)^(.*?)\bfrom\b", m2.group(1)).group(1)
                left_chain = re.sub(r"(?is)^.*?\bfrom\b\s*", "", m2.group(1))
                s = (f"{head}FROM {m2.group(2).strip()} "
                     f"LEFT JOIN {left_chain} ON {m2.group(3).strip()}")
                notes.append("RIGHT JOIN 已归一化为反向 LEFT JOIN")
        if re.search(r"(?i)\bright\s+(?:outer\s+)?join\b", s):
            return False, (f"{dialect} 不支持 RIGHT JOIN"
                           + ("（含串联）" if rj_n > 1 else "")
                           + "，请交换两表改写为 LEFT JOIN（保持各 JOIN 的 ON 紧随其后）"), notes

    allowed = {t.lower() for t in sub_tables} | {
        c.group(1).lower() for c in _CTE_RE.finditer(s)}
    bad = [t for t in set(_extract_tables(s)) if t.lower() not in allowed]
    if bad:
        return False, f"只能使用给定表（越权表：{', '.join(sorted(bad))}）", notes

    if _SELECT_STAR_RE.search(s) and len(sub_tables) > 3:
        return False, "表较多时禁止 SELECT *，请显式列出需要的列", notes

    limits = _LIMIT_RE.findall(s)
    if limits:
        n = max(int(x) for x in limits)
        if n > MAX_ROWS:
            s = _LIMIT_RE.sub(f"LIMIT {MAX_ROWS}", s)
            notes.append(f"LIMIT {n} 已纠偏为 {MAX_ROWS}")
    else:
        s += f"\nLIMIT {MAX_ROWS}"
        notes.append("已自动追加 LIMIT " + str(MAX_ROWS))
    return True, s, notes


def _extract_tables(sql: str) -> list:
    """提取 FROM/JOIN 后表名：sqlglot AST 优先，异常降级正则。"""
    try:
        import sqlglot
        tables = set()
        for tree in sqlglot.parse(sql, read="postgres"):
            for node in tree.find_all(sqlglot.exp.Table):
                if node.name:
                    tables.add(node.name)
        return list(tables)
    except Exception:
        return _TABLE_RE.findall(sql)


# ────────────────────── run_readonly（§4.3） ──────────────────────

def _pg_dsn(dsn: str) -> str:
    """SQLAlchemy URL → asyncpg 原生 DSN。dsn 必须显式配置（独立业务实例，
    业务数据不得落入平台实体库；空 DSN 已在 nl2sql_query 入口拦截回落）。"""
    dsn = (dsn or "").strip()
    if not dsn:
        raise ValueError("数据源未配置 datasource_dsn（业务库连接串）")
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn[len("postgresql+asyncpg://"):]
    return dsn


async def run_readonly(dsn: str, dialect: str, sql: str,
                       timeout_s: float = TIMEOUT_S, max_rows: int = MAX_ROWS) -> dict:
    """只读执行：会话只读 + 语句超时 + 行数截断三道保险。永不抛异常。"""
    t0 = time.perf_counter()
    out = {"ok": False, "columns": [], "rows": [], "rowcount": 0,
           "error": "", "elapsed_ms": 0, "truncated": False}
    try:
        if dialect == "postgres":
            columns, rows, truncated = await asyncio.wait_for(
                _pg_fetch(_pg_dsn(dsn), sql, timeout_s), timeout_s + 2)
        elif dialect == "sqlite":
            columns, rows, truncated = await asyncio.wait_for(
                _sqlite_fetch(dsn, sql, timeout_s), timeout_s + 2)
        else:
            out["error"] = f"暂不支持的方言：{dialect}"
            out["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
            return out
        out.update(ok=True, columns=columns, rowcount=len(rows),
                   rows=rows[:max_rows], truncated=truncated or len(rows) > max_rows)
    except asyncio.TimeoutError:
        out["error"] = f"执行超时（>{timeout_s:g}s）"
    except Exception as exc:
        msg = str(exc).strip()
        out["error"] = msg.splitlines()[0][:300] if msg else exc.__class__.__name__
    out["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
    return out


async def _pg_fetch(dsn: str, sql: str, timeout_s: float):
    import asyncpg
    conn = await asyncio.wait_for(asyncpg.connect(dsn, timeout=timeout_s), timeout_s)
    try:
        await conn.execute("SET default_transaction_read_only = on")
        await conn.execute(f"SET statement_timeout = {int(timeout_s * 1000)}")
        records = await asyncio.wait_for(conn.fetch(sql), timeout_s)
        rows = [dict(r) for r in records]
        return ([k for k in rows[0].keys()] if rows else []), rows, False
    finally:
        await conn.close()


async def _sqlite_fetch(dsn: str, sql: str, timeout_s: float):
    import aiosqlite
    uri = dsn if dsn.startswith("file:") else f"file:{dsn}"
    if "mode=ro" not in uri:
        uri += ("&" if "?" in uri else "?") + "mode=ro"
    async with aiosqlite.connect(uri, uri=True) as conn:
        cur = await asyncio.wait_for(conn.execute(sql), timeout_s)
        columns = [d[0] for d in cur.description or []]
        raw = await cur.fetchall()
        return columns, [dict(zip(columns, r)) for r in raw], False


# ────────────────────── prompt 构造（§4.1） ──────────────────────

def _system_prompt(dialect: str) -> str:
    label = {"postgres": "PostgreSQL", "sqlite": "SQLite", "mysql": "MySQL"}.get(
        dialect, dialect)
    today = datetime.date.today().isoformat()
    weekday = "一二三四五六日"[datetime.date.today().weekday()]
    return (
        f"你是资深数据分析工程师，把业务问题翻译成**单条只读 SELECT**（{label} 方言）。"
        f"今天是 {today}（周{weekday}），「本周/本月/近N天」按此换算；"
        "TEXT 存储的 ISO 时间可 BETWEEN 直接比较。\n"
        "硬性红线（违反即报废）：\n"
        "1. 只产一条 SELECT，禁止任何写操作/DDL/多语句；\n"
        "2. 只用【可用表】里的表与列，列名用物理列名，禁止编造表列；\n"
        "3. 表间关联只用【表关系】给定的 ON 条件，禁止自造 join；\n"
        "4. 「取值」枚举之外的条件值一律不用，文本值用单引号；\n"
        "5. 跨表统计主表指标（如航班的旅客数）必须先子查询聚合或按主键去重，"
        "禁止直接对 1:N join 结果 COUNT/SUM 主表字段（会翻倍）；\n"
        "6. 禁隐式 join（FROM a, b）、禁 NOT IN(子查询)，用显式 JOIN / NOT EXISTS；\n"
        "7. 取「全部主表行」而外键可空时用 LEFT JOIN，否则默认 INNER JOIN；\n"
        "8. 中文输出列加双引号别名；行数上限 500（写 LIMIT）。\n"
        + (f"{label} 方言注意：不支持 RIGHT JOIN/FULL OUTER JOIN，需改写为 LEFT JOIN；"
           if dialect in ("sqlite", "mysql") else "")
    )


def _user_prompt(ctx: str, task: str, fewshot: str, react: bool,
                 feedback: Optional[dict] = None) -> str:
    parts = [ctx, "", "── few-shot 示例 ──", fewshot, "", f"── 任务 ──", task]
    if react:
        parts.insert(0, "先在 thought 里依次回答：① 问题主语命中哪张种子表；② 用到哪些"
                        "关系边与 ON 条件；③ 是否聚合、聚合粒度是否防翻倍；④ 时间窗与"
                        "过滤值。再给 SQL。")
    if feedback:
        parts += ["", "── 上次尝试失败，修正 ──",
                  f"上次 SQL：\n{feedback.get('sql', '')}",
                  f"失败原因：{feedback.get('error', '')}",
                  "请修正后重新输出（只输出 JSON）。"]
    parts += ["", "只输出 JSON（不要 markdown 代码块）："
              '{"thought": "简要思路(react 时)", "sql": "SELECT ...", '
              '"used_tables": ["表名"], "explain": "一句话说明"}']
    return "\n".join(parts)


async def _llm_json(messages: list, temperature: float = 0.1) -> Optional[dict]:
    """调 LLM 并解析 JSON 输出（容忍 ```json 代码块）。失败返回 None。"""
    from providers.llm import create_llm
    llm = create_llm()
    if llm is None:
        return None
    try:
        async with async_span("nl2sql.generate", {"nl2sql.mode": "generate"}) as span:
            resp = await llm.ainvoke(messages)
            set_llm_usage(span, resp)
        text = (resp.content or "").strip() if hasattr(resp, "content") else str(resp)
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        raw = m.group(1) if m else text
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        return json.loads(raw[start:end + 1])
    except Exception:
        logger.exception("[NL2SQL] LLM 调用/解析失败")
        return None


# ────────────────────── 顶层编排（§4.4~§4.5） ──────────────────────

def _is_complex(task: str, n_edges: int) -> bool:
    low = task.lower()
    return any(h in low for h in _REACT_HINTS) or n_edges >= 2


async def nl2sql_query(task: str,
                       category_ids: Optional[list[str]] = None) -> Optional[dict]:
    """三级链第一级总入口。category_ids 非空 = 只在用户勾选的数据源类别中检索。
    返回事实数据 dict 或 None（回落 NL2Filter 老路）。"""
    if getattr(settings, "NL2SQL_ENABLED", True) is False:
        return None
    from providers.llm import create_llm
    if create_llm() is None:            # LLM 未配置：直接回落老链，不空转重试
        return None
    prep = await prepare_nl2sql(task, category_ids)
    if prep is None:
        return None
    if not (prep.get("dsn") or "").strip():
        # 独立业务实例策略：DSN 未配置 = 数据源位置不明，回落老链，绝不默认打平台实体库
        logger.warning("[NL2SQL] 类别未配置 datasource_dsn，回落老链"
                       "（业务数据须落独立实例，禁止默认用平台库）")
        return None

    sub = prep["sub"]
    sub_tables = [t.code for t in sub.tables]
    fewshot = _pick_few_shots(set(sub_tables))
    dialect = prep["dialect"]
    react = _is_complex(task, len(sub.edges))
    system = _system_prompt(dialect)

    async with async_span("nl2sql.query", {"nl2sql.dialect": dialect,
                                           "nl2sql.tables": len(sub_tables)}):
        feedback: Optional[dict] = None
        sql = used = explain = thought = None
        result: Optional[dict] = None
        notes: list = []
        for attempt in range(1 + MAX_RETRIES):
            if attempt == 1:
                react = True          # 首轮失败 → 升级 react 思考链
            payload = await _llm_json([
                ("system", system), ("human", _user_prompt(prep["ctx"], task, fewshot, react, feedback))])
            if not payload or not (payload.get("sql") or "").strip():
                feedback = {"sql": payload.get("sql", "") if payload else "",
                            "error": "输出无法解析为 JSON，请严格按约定输出"}
                continue
            sql = payload["sql"].strip()
            used = payload.get("used_tables") or []
            explain = payload.get("explain") or ""
            thought = payload.get("thought") or ""
            ok, fixed, notes = validate_sql(sql, dialect, sub_tables)
            if not ok:
                feedback = {"sql": sql, "error": f"校验拒绝：{fixed}"}
                continue
            sql = fixed
            async with async_span("nl2sql.execute", {"nl2sql.attempt": attempt}):
                result = await run_readonly(prep["dsn"], dialect, sql)
            if result["ok"] and result["rowcount"] > 0:
                break
            if result["ok"] and result["rowcount"] == 0 and attempt == 0:
                # 空结果 ≠ 失败：触发一次「放宽条件自查」回灌（§4.4）
                feedback = {"sql": sql,
                            "error": "执行成功但 0 行。请自查：过滤值是否不在枚举内、"
                                     "时间窗是否过紧、INNER JOIN 是否应放宽为 LEFT JOIN；"
                                     "确认无误则保持原逻辑重出 SQL"}
                continue
            if not result["ok"]:
                feedback = {"sql": sql, "error": f"执行报错：{result['error']}"}
                continue
            break                     # 空结果且已自查过 → 接受空
        else:
            result = result or {"ok": False}

        if not (result and result.get("ok")):
            logger.warning("[NL2SQL] 放弃回老链：%s", (result or {}).get("error", "生成失败"))
            return None
        result.update({
            "sql": sql, "used_tables": used, "explain": explain,
            "thought": thought, "retries": attempt,
            "notes": notes, "dialect": dialect,
            "joins": [{"relation": e.relation_name, "source": e.source_table,
                       "target": e.target_table,
                       "on": " AND ".join(f"{e.source_table}.{j['left']}={e.target_table}.{j['right']}"
                                          for j in e.join)}
                      for e in sub.edges],
            "tables": sub_tables, "category": sub.graph.category_name,
            "category_id": sub.graph.category_id,
        })
        logger.info("[NL2SQL] ok tables=%s rows=%s retries=%s %.0fms",
                    sub_tables[:4], result["rowcount"], result["retries"], result["elapsed_ms"])
        return result
