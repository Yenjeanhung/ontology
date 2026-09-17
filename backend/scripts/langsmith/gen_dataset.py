#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LangSmith 数据集合成脚本：从知识库 chunk 采样，LLM 生成「任务 + 参考答案」。

RAG 合成范式（业界标准做法）：
  1. 从本地 chunks 表随机采样文档片段（真实知识，保证 reference 可溯源）；
  2. 每条片段按「任务类型轮转」让 LLM 出题：检索问答 / 要点简报 / 运行研判 /
     对比归纳 / 风险清单——覆盖多智能体系统的典型任务面；
  3. 生成的 task/reference 批量写入 LangSmith 数据集（已存在则追加），
     同时本地落一份 JSON 备份，便于人工抽检修订（合成数据必须人工把关）。

用法（脚本自动定位 backend/，任意目录可执行）：
  python scripts/langsmith/gen_dataset.py                          # 默认 30 条 → multi-agent-regression
  python scripts/langsmith/gen_dataset.py --count 100              # 指定条数
  python scripts/langsmith/gen_dataset.py --dataset myTest         # 写入指定数据集（追加）
  python scripts/langsmith/gen_dataset.py --dry-run                # 只生成打印，不入库不落盘
  python scripts/langsmith/gen_dataset.py --kb <kb_id>             # 只从指定知识库采样

前置：知识库已有文档（chunks 表非空）、scripts/.env.scripts 已配置 LLM
（模板见 .env.scripts.example，与服务端 backend/.env 分离）、LangSmith 环境变量已配置。

规模建议：冒烟 10~50 条；常规回归 100~300 条；发布门禁 500+。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
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

from datetime import datetime  # noqa: E402

from sqlalchemy import select  # noqa: E402

from langsmith import Client  # noqa: E402

GEN_CONCURRENCY = 3          # LLM 并发出题数（控额度与限流）
MIN_CHUNK_CHARS = 80         # 低于此长度的片段无出题价值，跳过


# ── 1. 采样：chunks 表随机抽 N 条（两步走，避开跨数据库随机函数差异） ──
async def sample_chunks(n: int, kb_id: str | None = None) -> list[dict]:
    from database import async_session
    from models import Chunk, File

    async with async_session() as db:
        stmt = select(Chunk.id, Chunk.file_id).join(
            File, Chunk.file_id == File.id
        )
        if kb_id:
            stmt = stmt.where(File.kb_id == kb_id)
        rows = (await db.execute(stmt)).all()
        if not rows:
            sys.exit("[error] chunks 表为空（或该 kb 无数据）：先上传文档建好知识库")
        picked = random.sample(rows, min(n * 3, len(rows)))   # 多采 3 倍，容错生成失败

        ids = [r.id for r in picked]
        q = (
            select(Chunk.content, File.name)
            .join(File, Chunk.file_id == File.id)
            .where(Chunk.id.in_(ids))
        )
        out = [
            {"content": c, "file_name": f}
            for c, f in (await db.execute(q)).all()
            if c and len(c.strip()) >= MIN_CHUNK_CHARS
        ]
        random.shuffle(out)
        return out


# ── 2. 出题：LLM 依片段生成 task + reference（任务类型轮转保证覆盖面） ──
TASK_KINDS = [
    ("知识检索问答", "围绕片段中一个具体知识点，提出用户会真实提问的问题"),
    ("要点简报生成", "要求系统就片段主题整理一份分条要点简报"),
    ("运行研判决策", "基于片段描述的情形，要求给出处置建议或判断结论"),
    ("对比归纳", "要求对片段中多个要点做对比、归纳或排序"),
    ("风险识别清单", "要求从片段提炼风险点或核查清单"),
]

PROMPT_TMPL = (
    "你是航空运行领域的数据标注专家。基于下面的知识库片段出一道评测题，"
    "用于考核多智能体问答系统。\n\n"
    "任务类型：{kind}（{kind_hint}）\n\n"
    "要求：\n"
    "1. task：模拟真实用户向系统提出的任务描述，具体、可回答；"
    "不要出现「片段」「文档」「来源」等字眼；\n"
    "2. reference：标准参考答案，只依据片段事实作答，2~4 句话，"
    "每条事实都能在片段中找到依据；\n"
    "3. 若片段信息量不足以支撑该任务类型，输出 {{\"skip\": true}} 换题。\n\n"
    "【知识库片段】（来源：{file_name}）\n{content}\n\n"
    "只输出 JSON：{{\"task\": \"...\", \"reference\": \"...\"}}"
)


async def gen_one(llm, item: dict, kind: str, kind_hint: str) -> dict | None:
    prompt = PROMPT_TMPL.format(
        kind=kind, kind_hint=kind_hint,
        file_name=item["file_name"], content=item["content"][:3000],
    )
    try:
        resp = await asyncio.wait_for(llm.ainvoke(prompt), timeout=90)
        m = re.search(r"\{.*\}", resp.content, re.S)
        if not m:
            return None
        data = json.loads(m.group())
        task = str(data.get("task", "")).strip()
        reference = str(data.get("reference", "")).strip()
        if data.get("skip") or not task or not reference:
            return None
        return {
            "task": task,
            "reference": reference,
            "source_file": item["file_name"],
            "kind": kind,
        }
    except Exception as e:
        print(f"[warn] 出题失败（{item['file_name']}）：{e}")
        return None


# ── 3. 主流程：采样 → 并发出题 → 写 LangSmith + 本地备份 ─────────────
async def run(args: argparse.Namespace) -> None:
    from providers import create_llm

    llm = create_llm()
    if llm is None:
        sys.exit("[error] create_llm() 返回空：脚本专用 LLM 配置未就绪——"
                 "复制 backend/scripts/.env.scripts.example 为 .env.scripts，"
                 "填写 OPENAI_API_KEY / OPENAI_BASE_URL / LLM_MODEL")

    need, seen = args.count, set()
    samples = await sample_chunks(need, args.kb)
    scope = f"知识库 {args.kb}" if args.kb else "全库"
    print(f"[sample] 采样范围 {scope}，候选片段 {len(samples)} 条，开始出题（目标 {need} 条，"
          f"并发 {GEN_CONCURRENCY}，类型轮转 {len(TASK_KINDS)} 类）")

    t0 = time.monotonic()
    results: list[dict] = []
    idx = 0
    while len(results) < need and idx < len(samples):
        batch = samples[idx: idx + need * 2]
        idx += len(batch)
        sem = asyncio.Semaphore(GEN_CONCURRENCY)

        async def _bound(item, i):
            async with sem:
                kind, hint = TASK_KINDS[i % len(TASK_KINDS)]
                return await gen_one(llm, item, kind, hint)

        for r in await asyncio.gather(*[_bound(s, i + idx) for i, s in enumerate(batch)]):
            if r and r["task"] not in seen:
                seen.add(r["task"])
                results.append(r)
                print(f"  [{len(results)}/{need}] ({r['kind']}) {r['task'][:48]}…")
                if len(results) >= need:
                    break
    print(f"[gen] 生成 {len(results)} 条，用时 {time.monotonic() - t0:.0f}s")
    if not results:
        sys.exit("[error] 一条都没生成出来：检查 LLM 配置或知识库内容质量")

    # 本地备份（人工抽检修订用）
    if not args.dry_run:
        out_path = _SCRIPT_DIR / f"gen_dataset_{datetime.now():%Y%m%d_%H%M%S}.json"
        out_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[backup] 已落盘：{out_path}")

        client = Client()
        if not client.has_dataset(dataset_name=args.dataset):
            client.create_dataset(dataset_name=args.dataset,
                                  description="多智能体取证系统回归集（RAG 合成）")
            print(f"[dataset] 新建数据集：{args.dataset}")
        client.create_examples(
            dataset_name=args.dataset,
            inputs=[{"task": r["task"]} for r in results],
            outputs=[{"reference": r["reference"], "source_file": r["source_file"]}
                     for r in results],
        )
        n = len(list(client.list_examples(dataset_name=args.dataset)))
        print(f"[dataset] 已写入 {len(results)} 条 → {args.dataset}（现有 {n} 条）")
        print("[next] 人工抽检修订 → LangSmith UI 或编辑备份 JSON 后重新导入；"
              f"跑评估：python scripts/langsmith/eval_langsmith.py --dataset {args.dataset}")
    else:
        print("[dry-run] 仅预览未入库。完整 JSON：")
        print(json.dumps(results, ensure_ascii=False, indent=2))

# 航班运行监控评估： python scripts/langsmith/gen_dataset.py --count 30 --dataset multi-agent-regression --kb a20a9a0a6492
def main() -> None:
    parser = argparse.ArgumentParser(description="LangSmith 数据集合成（RAG 范式）")
    parser.add_argument("--count", type=int, default=30, help="生成条数（默认 30）")
    parser.add_argument("--dataset", default="multi-agent-regression", help="目标数据集名")
    parser.add_argument("--kb", default=None, help="只从指定 kb_id 的知识库采样，运行监控处置的 a20a9a0a6492")
    parser.add_argument("--dry-run", action="store_true", help="只生成打印，不入库不落盘")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
