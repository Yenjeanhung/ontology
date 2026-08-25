"""本体服务动作的沙箱执行器。

安全模型（一期，本地/内网单机部署）：
- 子进程隔离：`sys.executable -I -c <runner>`（隔离模式，不加载用户 site-packages）；
- AST 静态检查 import 白名单，命中即拒绝；
- 超时 kill（timeout_seconds，上限 120s）；
- stdout/stderr 与结果 JSON 截断。

代码契约：用户代码定义 `def run(params, entity, context) -> dict`，异常直接 raise。
"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
import time

# 允许 import 的模块根名（标准库子集 + 随项目依赖的第三方 HTTP 库）
IMPORT_WHITELIST: set[str] = {
    # 标准库
    "json", "re", "math", "datetime", "time", "random", "collections",
    "itertools", "functools", "typing", "hashlib", "base64", "urllib",
    "textwrap", "statistics", "decimal", "fractions", "string", "uuid",
    "csv", "io", "unicodedata",
    # 第三方（项目依赖内）
    "requests", "httpx",
}

RUNNER_SRC = r"""
import io, json, sys, traceback
from contextlib import redirect_stdout

# Windows 下子进程 stdout 默认 GBK 编码，强制 UTF-8 避免中文输出乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

def _var(ctx, node, field=None, default=None):
    # safely read upstream node output by node_id / field
    # supports service/code nodes: { "data": { ... } } and llm/agent nodes: { "text": ..., "cc": ..., ... }
    out = ctx.get(node, {})
    if not isinstance(out, dict):
        return default if field else out
    if field is None:
        return out
    target = out.get("data", out) if "data" in out else out
    if not isinstance(target, dict):
        return default
    return target.get(field, default)


def main():
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    code = payload.get("code") or ""
    params = payload.get("params") or {}
    entity = payload.get("entity") or {}
    context = payload.get("context") or {}
    buf = io.StringIO()
    error = None
    data = None
    try:
        g = {
            "__name__": "__sandbox__",
            "var": lambda node, field=None, default=None: _var(context, node, field, default),
        }
        with redirect_stdout(buf):
            exec(compile(code, "<service>", "exec"), g)
            fn = g.get("run")
            if not callable(fn):
                raise RuntimeError("代码中未定义 run(params, entity, context) 函数")
            data = fn(params, entity, context)
    except BaseException as e:
        error = "".join(traceback.format_exception_only(type(e), e)).strip() or f"{type(e).__name__}"
    sys.stdout.write(json.dumps(
        {"success": error is None, "data": data, "error": error, "stdout": buf.getvalue()},
        ensure_ascii=False, default=str,
    ))

main()
"""

STDOUT_LIMIT = 64 * 1024
RESULT_LIMIT = 256 * 1024


def check_code(code_text: str) -> str | None:
    """静态检查：语法 + import 白名单。返回错误信息，None 表示通过。"""
    try:
        tree = ast.parse(code_text)
    except SyntaxError as e:
        return f"语法错误：第 {e.lineno or '?'} 行 {e.msg}"
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in IMPORT_WHITELIST:
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                violations.append("相对 import")
            elif node.module:
                root = node.module.split(".")[0]
                if root not in IMPORT_WHITELIST:
                    violations.append(f"from {node.module} import ...")
    if violations:
        return "不允许的导入（仅开放标准库子集与 requests/httpx）：" + "、".join(sorted(set(violations)))
    return None


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...（已截断，共 {len(text)} 字符）"


async def execute_service(
    *,
    code_text: str,
    language: str,
    params: dict,
    entity: dict,
    context: dict,
    timeout_seconds: int,
) -> dict:
    """执行动作代码，返回统一结果结构。"""
    if language != "python":
        return {"success": False, "data": None, "error": f"暂不支持 {language}", "stdout": "", "duration_ms": 0}

    err = check_code(code_text or "")
    if err:
        return {"success": False, "data": None, "error": err, "stdout": "", "duration_ms": 0}

    timeout = max(1, min(120, timeout_seconds or 30))
    payload = json.dumps(
        {"code": code_text, "params": params or {}, "entity": entity or {},
         "context": context or {}},
        ensure_ascii=False, default=str,
    ).encode("utf-8")

    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            # -X utf8：UTF-8 模式（-I 会忽略 PYTHONIOENCODING 环境变量，需用解释器参数）
            sys.executable, "-I", "-X", "utf8", "-c", RUNNER_SRC,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:  # 启动失败
        return {"success": False, "data": None, "error": f"执行器启动失败：{e}", "stdout": "", "duration_ms": 0}

    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(payload), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"success": False, "data": None,
                "error": f"执行超时（{timeout}s），已终止", "stdout": "", "duration_ms": timeout * 1000}
    except BaseException:
        proc.kill()
        await proc.wait()
        raise

    duration_ms = int((time.monotonic() - started) * 1000)
    out_text = out_b.decode("utf-8", errors="replace")
    err_text = _truncate(err_b.decode("utf-8", errors="replace"), STDOUT_LIMIT)

    if proc.returncode != 0:
        return {"success": False, "data": None,
                "error": _truncate(err_text or f"进程异常退出（code={proc.returncode}）", STDOUT_LIMIT),
                "stdout": _truncate(out_text, STDOUT_LIMIT), "duration_ms": duration_ms}

    try:
        result = json.loads(out_text)
    except ValueError:
        return {"success": False, "data": None,
                "error": _truncate(err_text or "无法解析执行结果", STDOUT_LIMIT),
                "stdout": _truncate(out_text, STDOUT_LIMIT), "duration_ms": duration_ms}

    return {
        "success": bool(result.get("success")),
        "data": result.get("data"),
        "error": result.get("error"),
        "stdout": _truncate(str(result.get("stdout") or ""), STDOUT_LIMIT),
        "duration_ms": duration_ms,
    }


def coerce_params(params_schema: list[dict], raw: dict) -> tuple[dict | None, str | None]:
    """按 params_schema 校验并转换参数。返回 (params, error)。"""
    out: dict = {}
    for p in params_schema:
        name = p["name"]
        value = raw.get(name, p.get("default"))
        if value is None or (isinstance(value, str) and value.strip() == ""):
            if p.get("required"):
                return None, f"缺少必填参数：{p.get('label') or name}"
            continue
        ptype = p.get("type", "string")
        try:
            if ptype == "number":
                value = float(value) if "." in str(value) else int(str(value))
            elif ptype == "boolean":
                if isinstance(value, str):
                    value = value.strip().lower() in ("1", "true", "yes", "on")
                value = bool(value)
            else:
                value = str(value)
        except (TypeError, ValueError):
            return None, f"参数 {p.get('label') or name} 需要 {ptype} 类型"
        out[name] = value
    # 透传未在 schema 中声明的额外参数（便于灵活调试）
    for k, v in (raw or {}).items():
        if k not in out:
            out[k] = v
    return out, None
