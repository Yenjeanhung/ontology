#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抓取 FAA 适航指令（AD）作为民航维修领域实体抽取语料。

数据源：https://www.faaadsearch.com/api/v1/ads （FAA 公开记录，公共领域）
免费额度：100 请求/天、10 请求/分钟。脚本内置节流，默认总请求数控制在 60 以内。

产出（目录用英文命名，避免 Windows 命令行 / Docker 挂载的中文编码问题）：
  backend/data/corpus/aviation_maintenance/FAA_AD/<机型>/<AD号>.txt   单篇可读文本（供抽取）
  backend/data/corpus/aviation_maintenance/FAA_AD/_index.jsonl        结构化汇总

用法：
  python fetch_aviation_corpus.py                 # 默认抓取主力机型
  python fetch_aviation_corpus.py --max-pages 2   # 每个机型最多抓几页
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://www.faaadsearch.com/api/v1/ads"

# 主力机型 / 发动机：覆盖窄体、宽体、支线，以及主流发动机型号。
# name 用于目录名；manufacturer / model 为 API 查询参数。
TARGETS = [
    # 窄体主力
    {"dir": "B737", "manufacturer": "Boeing", "model": "737"},
    {"dir": "A320", "manufacturer": "Airbus", "model": "A320"},
    {"dir": "A330", "manufacturer": "Airbus", "model": "A330"},
    # 宽体
    {"dir": "B777", "manufacturer": "Boeing", "model": "777"},
    {"dir": "B787", "manufacturer": "Boeing", "model": "787"},
    {"dir": "B747", "manufacturer": "Boeing", "model": "747"},
    {"dir": "A350", "manufacturer": "Airbus", "model": "A350"},
    # 支线 / 其他
    {"dir": "B757", "manufacturer": "Boeing", "model": "757"},
    {"dir": "B767", "manufacturer": "Boeing", "model": "767"},
    {"dir": "MD80", "manufacturer": "Boeing", "model": "MD-88"},
    # 发动机（product_category=engine）
    {"dir": "ENG_CFM56", "manufacturer": "CFM International", "model": "CFM56"},
    {"dir": "ENG_V2500", "manufacturer": "International Aero Engines", "model": "V2500"},
    {"dir": "ENG_GE90", "manufacturer": "General Electric", "model": "GE90"},
]

# ATA 章节号 -> 中文系统名（ATA 100 规范，用于从 AD 文本中标注章节）
ATA_CHAPTERS = {
    "05": "时限/维护检查", "06": "尺寸与区域", "07": "顶升与支撑", "08": "校平与称重",
    "09": "牵引与滑行", "10": "停放与系留", "11": "标牌", "12": "勤务",
    "20": "标准施工", "21": "空调", "22": "自动飞行", "23": "通信",
    "24": "电源", "25": "设备/装饰", "26": "防火", "27": "飞行操纵",
    "28": "燃油", "29": "液压", "30": "防冰排雨", "31": "指示/记录",
    "32": "起落架", "33": "照明", "34": "导航", "35": "氧气",
    "36": "气源", "38": "给水/排污", "45": "中央维护系统", "46": "信息系统",
    "49": "辅助动力装置", "51": "标准施工/结构", "52": "舱门", "53": "机身",
    "54": "短舱/吊挂", "55": "安定面", "56": "窗户", "57": "机翼",
    "61": "螺旋桨", "65": "旋翼", "71": "动力装置", "72": "发动机",
    "73": "发动机燃油与控制", "74": "点火", "75": "空气", "76": "发动机控制",
    "77": "发动机指示", "78": "排气", "79": "滑油", "80": "起动",
    "81": "涡轮", "82": "涡轮增压", "83": "附件齿轮箱",
}


def _http_get_json(url: str, timeout: int = 60, retries: int = 3) -> dict:
    """带重试的 GET JSON。"""
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "ontology-corpus-fetcher/1.0", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"请求失败: {url} -> {last_err}")


def detect_ata_chapters(text: str) -> list[str]:
    """从文本中识别 ATA 章节。

    AD 正文里常见两种写法：
      - Boeing Requirements Bulletin 编号：737-32A1599 RB -> ATA 32
      - 显式 ATA 引用：ATA 32 / ATA Chapter 32
    """
    found: set[str] = set()
    if not text:
        return []

    # 1) 机型-ATA 编号形式： \d{3}-(\d{2})[A-Z]?\d+
    for m in re.finditer(r"\b\d{3}-(\d{2})[A-Z]?\d{2,}\b", text):
        ch = m.group(1)
        if ch in ATA_CHAPTERS:
            found.add(ch)

    # 2) 显式 ATA 章节
    for m in re.finditer(r"\bATA\s*(?:Chapter\s*)?(\d{2})\b", text, re.IGNORECASE):
        ch = m.group(1)
        if ch in ATA_CHAPTERS:
            found.add(ch)

    return sorted(found)


def ad_to_text(rec: dict) -> str:
    """把一条 AD 记录渲染成便于实体抽取的可读文本。"""
    lines: list[str] = []
    ad_no = rec.get("ad_number") or "UNKNOWN"

    lines.append(f"适航指令编号：{ad_no}")
    if rec.get("document_title"):
        lines.append(f"标题：{rec['document_title']}")
    if rec.get("manufacturer_name") or rec.get("model_name"):
        lines.append(f"制造商/型号：{rec.get('manufacturer_name', '')} {rec.get('model_name', '')}".strip())
    if rec.get("product_category"):
        lines.append(f"产品类别：{rec['product_category']}")
    if rec.get("status"):
        lines.append(f"状态：{rec['status']}")
    if rec.get("effective_date"):
        lines.append(f"生效日期：{rec['effective_date']}")
    if rec.get("is_recurring"):
        lines.append(f"重复性检查：{'是' if rec['is_recurring'] else '否'}")

    # ATA 章节：优先从正文/适用性原文中识别
    raw_all = " ".join(
        str(rec.get(k) or "")
        for k in ("applicability_raw_text", "summary_unsafe_condition", "summary_required_actions", "unsafe_condition")
    )
    atas = detect_ata_chapters(raw_all)
    if atas:
        lines.append("ATA章节：" + "、".join(f"{c} {ATA_CHAPTERS[c]}" for c in atas))

    if rec.get("summary_affected"):
        lines.append("")
        lines.append("【适用机型】")
        lines.append(str(rec["summary_affected"]))

    if rec.get("applicability_raw_text"):
        lines.append("")
        lines.append("【适用性原文】")
        lines.append(str(rec["applicability_raw_text"]).replace("\\n", "\n"))

    if rec.get("summary_unsafe_condition"):
        lines.append("")
        lines.append("【不安全状态（故障原因/现象）】")
        lines.append(str(rec["summary_unsafe_condition"]))

    if rec.get("summary_required_actions"):
        lines.append("")
        lines.append("【要求的纠正措施】")
        lines.append(str(rec["summary_required_actions"]))

    if rec.get("summary_compliance_time"):
        lines.append("")
        lines.append("【符合性时限】")
        lines.append(str(rec["summary_compliance_time"]))

    if rec.get("docket_number"):
        lines.append("")
        lines.append(f"档案号：{rec['docket_number']}")
    if rec.get("federal_register_citation"):
        lines.append(f"联邦公报引证：{rec['federal_register_citation']}")
    if rec.get("source_url"):
        lines.append(f"原文链接：{rec['source_url']}")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取 FAA AD 作为民航维修语料")
    ap.add_argument("--out", default=None, help="输出目录，默认 backend/data/corpus/aviation_maintenance/FAA_AD")
    ap.add_argument("--max-pages", type=int, default=3, help="每个机型最多抓取页数（默认 3）")
    ap.add_argument("--per-page", type=int, default=50, help="每页条数（默认 50）")
    ap.add_argument("--throttle", type=float, default=6.5, help="请求间隔秒，默认 6.5（限流 10 次/分）")
    ap.add_argument("--max-requests", type=int, default=60, help="总请求数上限，默认 60（免费额度 100/天）")
    args = ap.parse_args()

    if args.out:
        out_root = Path(args.out)
    else:
        out_root = Path(__file__).resolve().parent.parent / "data" / "corpus" / "aviation_maintenance" / "FAA_AD"
    out_root.mkdir(parents=True, exist_ok=True)

    total_req = 0
    total_written = 0
    index_path = out_root / "_index.jsonl"

    print(f"输出目录：{out_root}")
    print(f"抓取目标：{len(TARGETS)} 个机型/发动机，每机型最多 {args.max_pages} 页\n")

    with index_path.open("w", encoding="utf-8") as idx:
        for t in TARGETS:
            if total_req >= args.max_requests:
                print(f"达到总请求上限 {args.max_requests}，停止。")
                break
            print(f"-> {t['dir']} ({t['manufacturer']} {t['model']}) ...", flush=True)
            req, cnt, recs = fetch_target_with_records(
                t, out_root, args.max_pages, args.per_page, args.throttle, args.max_requests - total_req
            )
            total_req += req
            total_written += cnt
            for r in recs:
                idx.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"   写入 {cnt} 条（消耗 {req} 次请求，累计 {total_req}）", flush=True)

    print(f"\n完成：共 {total_written} 条 AD，{total_req} 次请求")
    print(f"语料目录：{out_root}")
    print(f"汇总索引：{index_path}")
    return 0


def fetch_target_with_records(
    target: dict, out_root: Path, max_pages: int, per_page: int, throttle: float, budget: int
) -> tuple[int, int, list[dict]]:
    """与 fetch_target 相同，但额外返回原始记录列表（供写 jsonl 索引）。"""
    model_dir = out_root / target["dir"]
    model_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    requests_used = 0
    collected: list[dict] = []
    seen_ad_numbers: set[str] = set()

    page = 1
    while page <= max_pages and requests_used < budget:
        params = {
            "manufacturer": target["manufacturer"],
            "model": target["model"],
            "page": page,
            "perPage": per_page,
        }
        url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
        try:
            payload = _http_get_json(url)
        except Exception as e:  # noqa: BLE001
            print(f"  ! 抓取失败 {target['dir']} page={page}: {e}", file=sys.stderr)
            break

        requests_used += 1
        rows = payload.get("data") or []
        if not rows:
            break

        for rec in rows:
            ad_no = (rec.get("ad_number") or "").strip()
            if not ad_no or ad_no in seen_ad_numbers:
                continue
            seen_ad_numbers.add(ad_no)

            text = ad_to_text(rec)
            if len(text) < 200:
                continue

            safe_no = re.sub(r"[^\w\-.]+", "_", ad_no)
            (model_dir / f"{safe_no}.txt").write_text(text, encoding="utf-8")

            raw_all = " ".join(
                str(rec.get(k) or "")
                for k in ("applicability_raw_text", "summary_unsafe_condition", "summary_required_actions")
            )
            rec["_ata_chapters"] = detect_ata_chapters(raw_all)
            rec["_model_dir"] = target["dir"]
            rec["_text"] = text
            collected.append(rec)
            written += 1

        pagination = payload.get("pagination") or {}
        if not pagination.get("hasNext"):
            break
        page += 1
        time.sleep(throttle)

    return requests_used, written, collected


if __name__ == "__main__":
    raise SystemExit(main())
