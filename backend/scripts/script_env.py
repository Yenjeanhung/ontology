#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""脚本专用环境配置加载器（scripts/.env.scripts）。

离线测试/评估脚本与服务端配置彻底分离：
- 服务端：backend/.env + 页面配置（存数据库 llm_configs，运行时热加载）；
- 脚本端：scripts/.env.scripts（独立文件，离线 LLM 调用专用，已被 .gitignore 忽略）。

配置优先级（脚本视角，高 → 低）：
  rag_eval 内置 EVAL_LLM_CONFIG > .env.scripts > backend/.env > 数据库页面配置
实现原理：本模块把 .env.scripts 的键值注入 os.environ——pydantic-settings 读
Settings 时环境变量优先于 .env 文件，因此脚本配置自然压过 backend/.env 同名项；
未在本文件设置的变量（数据库 / LangSmith 等）照常回落 backend/.env，无需重复。

用法（调用必须先于 from config import ... / from providers import ...）：
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # scripts/ 根
    from script_env import load_script_env                     # noqa: E402
    load_script_env()                                          # noqa: E402
"""
from __future__ import annotations

import os
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SCRIPT_ENV_FILE = SCRIPTS_DIR / ".env.scripts"


def _strip_inline_comment(value: str) -> str:
    """剥行内注释：仅当 # 前有空白才视为注释起点，避免误伤值里的 #（如 URL fragment）。"""
    for i in range(1, len(value)):
        if value[i] == "#" and value[i - 1] in (" ", "\t"):
            return value[:i].rstrip()
    return value


def _unquote(value: str) -> str:
    """剥成对的单/双引号（dotenv 惯例）。"""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_env_file(text: str) -> dict[str, str]:
    """极简 dotenv 解析：注释 / export 前缀 / KEY=VALUE / 行内注释 / 成对引号。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.lstrip("﻿").strip()          # 兼容 BOM
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = _unquote(_strip_inline_comment(value.strip()))
        if key:
            out[key] = value
    return out


def load_script_env(path: Path | None = None, *, override: bool = True) -> Path | None:
    """加载脚本专用 env 并注入 os.environ。

    override=True：覆盖 os.environ 已有同名变量——脚本显式配置始终生效，
    行为可预期（CI 里临时 export 的变量以脚本文件为准，与「脚本专用」定位一致）。
    文件不存在时静默返回 None（脚本后续 create_llm 缺配置时有明确报错兜底）。
    """
    target = path or SCRIPT_ENV_FILE
    if not target.is_file():
        return None
    for key, value in _parse_env_file(target.read_text(encoding="utf-8")).items():
        if override or key not in os.environ:
            os.environ[key] = value
    return target
