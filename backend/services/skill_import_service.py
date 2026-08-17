"""技能导入服务：ZIP 技能包解析 + JSON / URL / ZIP 三端点共用的导入执行器。

支持技能市场（SkillsMP / Claude Code 生态）的完整技能包结构：
    SKILL.md（YAML frontmatter: name/description + 正文指令）
    + scripts/ references/ data/ 等配套文件

存储模型（v1.2）：配套文件**落盘**到 SKILL_FILES_DIR/<code>/（数据库只存清单元数据），
智能体可按清单中的目录路径引用；导出时从磁盘回读内容（JSON 回流）或打包完整 ZIP。

安全模型：解压前按 ZipInfo 元数据做总量/条目数/压缩比预检（zip bomb 防护）；
路径穿越（绝对路径 / ..）整包拒收；__MACOSX/.DS_Store/.git 等垃圾条目过滤；
落盘路径复用同一套相对路径清洗。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from services.skill_group_service import resolve_group_path

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
# 异常与数据结构
# ────────────────────────────────────────────────────────────────


class SkillImportError(Exception):
    """解析/校验失败。路由层按 status 转 HTTPException。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SkillFile:
    path: str                 # 相对技能根的 POSIX 路径
    size: int                 # 解压后字节数
    is_text: bool
    content: str | None = None  # 仅导出回流/存量迁移链路使用；新导入不把内容写进数据库

    def to_dict(self) -> dict:
        d = {"path": self.path, "size": self.size, "is_text": self.is_text}
        if self.content is not None:
            d["content"] = self.content
        return d


@dataclass
class ParsedSkill:
    name: str
    code: str
    description: str
    instructions: str
    sort_order: int = 0
    files: list[SkillFile] = field(default_factory=list)
    origin: str = ""          # 来源标注（zip 内路径等），用于错误定位
    raw_files: list[tuple[str, bytes]] = field(default_factory=list)  # 配套文件原始字节（导入时落盘）
    group_path: str = ""      # 导出回流：分组路径 "A/B"（优先于请求级 group_id）
    is_enabled: int | None = None  # 导出回流：enabled frontmatter（仅新建时生效）

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "instructions": self.instructions,
            "sort_order": self.sort_order,
            "files": [f.to_dict() for f in self.files],
            "origin": self.origin,
            "group_path": self.group_path,
        }
        if self.is_enabled is not None:
            d["is_enabled"] = self.is_enabled
        return d


@dataclass
class ImportOutcome:
    imported: list[dict] = field(default_factory=list)
    updated: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)  # [{code, name, existing_name}]

    def to_response(self) -> dict:
        return {
            "imported": len(self.imported),
            "updated": len(self.updated),
            "skipped": len(self.skipped),
            "errors": len(self.errors),
            "duplicates": self.duplicates,
            "details": {
                "imported": self.imported,
                "updated": self.updated,
                "skipped": self.skipped,
                "errors": self.errors,
            },
        }


# ────────────────────────────────────────────────────────────────
# SKILL.md / frontmatter 解析
# ────────────────────────────────────────────────────────────────

# 首行容忍 BOM / 空行；结束标记 --- 或 ...
_FM_RE = re.compile(
    r"\A(?:\ufeff|\s)*---[ \t]*\n(.*?)\n(?:-{3}|\.{3})[ \t]*(?:\n|\Z)",
    re.DOTALL,
)
_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:[ \t]*(.*))?$")


def _parse_mini_yaml(fm_text: str) -> dict[str, str]:
    """手写 mini-YAML：单行/引号值、块标量（| |- |+ > >- >+）、零缩进续行。

    只识别零缩进顶层键，嵌套子键（如 metadata: 下的缩进行）整体忽略——
    SKILL.md 的 frontmatter 只需要 name/description 等标量。
    """
    lines = fm_text.split("\n")
    meta: dict[str, str] = {}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _KEY_RE.match(line)
        if not m:
            continue
        key, inline = m.group(1), (m.group(2) or "").strip()

        if inline in ("|", "|-", "|+", ">", ">-", ">+"):
            # 块标量：收集后续缩进行
            folded = inline.startswith(">")
            indent = None
            block: list[str] = []
            while i < n:
                nxt = lines[i]
                if not nxt.strip():
                    block.append("")
                    i += 1
                    continue
                ind = len(nxt) - len(nxt.lstrip(" "))
                if indent is None:
                    if ind == 0:
                        break          # 块结束，回到顶层键
                    indent = ind
                if ind < indent:
                    break
                block.append(nxt[indent:] if len(nxt) > indent else "")
                i += 1
            while block and block[-1] == "":
                block.pop()
            value = (
                " ".join(x.strip() for x in block) if folded else "\n".join(block)
            )
            meta[key] = value.strip()
        else:
            # 单行值：剥引号；未加引号的 plain 标量吞掉零缩进非键续行（folded 语义），
            # 缩进行（嵌套 map）直接停
            quoted = len(inline) >= 2 and inline[0] == inline[-1] and inline[0] in "'\""
            value = inline[1:-1] if quoted else inline
            if not quoted:
                cont: list[str] = []
                while i < n:
                    nxt = lines[i]
                    if not nxt.strip() or nxt[:1] in (" ", "\t") or _KEY_RE.match(nxt):
                        break
                    cont.append(nxt.strip())
                    i += 1
                if cont:
                    value = " ".join([value] + cont) if value else " ".join(cont)
            meta[key] = value.strip()
    return meta


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """返回 (frontmatter 键值, 正文)。无 frontmatter 时原文返回。"""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    return _parse_mini_yaml(m.group(1)), text[m.end():]


def slugify(value: str, fallback: str = "") -> str:
    """lower → 非法字符替换为 - → strip → 截 50。"""
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "").strip()).strip("-").lower()[:50]
    return slug or fallback


def _stable_code(*candidates: str) -> str:
    """按优先级取 slug；全空（如纯中文名）时用首个非空候选的 sha1 短哈希保证唯一。"""
    for c in candidates:
        slug = slugify(c)
        if slug:
            return slug
    for c in candidates:
        if c and c.strip():
            return hashlib.sha1(c.strip().encode("utf-8")).hexdigest()[:8]
    return "imported-skill"


def _coerce_int(value) -> int:
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return 0


def _coerce_enabled(value) -> int | None:
    """'false'/'no'/'0' → 0（禁用）；其余（含缺省）→ None（不指定，走默认启用）。"""
    if value is None or str(value).strip() == "":
        return None
    return 0 if str(value).strip().lower() in ("false", "no", "0") else 1


def parse_skill_md(text: str, source: str = "") -> dict:
    """解析 SKILL.md / CLAUDE.md 为 {name, code, description, instructions, origin, ...}。

    正文 frontmatter 之后原样保留（仅去首尾空白）；description 缺省取正文首段截 200 字。
    透传导出元数据：sort_order / group_path / enabled（导出 ZIP 回流用）。
    """
    meta, body = parse_frontmatter(text)
    name = (meta.get("name") or meta.get("title") or "").strip()
    description = (meta.get("description") or meta.get("desc") or "").strip()
    code = (meta.get("code") or meta.get("id") or "").strip()
    body = body.strip()

    if not description and body:
        first_para = re.split(r"\n\s*\n", body)[0]
        description = re.sub(r"\s+", " ", first_para).strip()[:200]

    return {
        "name": name,
        "code": code,
        "description": description[:500],
        "instructions": body,
        "origin": source,
        "sort_order": _coerce_int(meta.get("sort_order")),
        "group_path": (meta.get("group_path") or "").strip(),
        "is_enabled": _coerce_enabled(meta.get("enabled")),
    }


def parse_skill_json(data: dict, fallback_name: str = "") -> dict:
    """解析 skill.json 格式（市场/社区的松散字段兼容）。"""
    if not isinstance(data, dict):
        raise SkillImportError("skill.json 格式错误：须为对象")
    name = (data.get("name") or data.get("title") or data.get("skill_name") or fallback_name or "").strip()
    code = (data.get("code") or data.get("id") or data.get("skill_id") or "").strip()
    instructions = (
        data.get("instructions") or data.get("content")
        or data.get("prompt") or data.get("system_prompt") or ""
    )
    desc = (data.get("description") or "").strip()
    # instructions 太短但 description 很长时，多半是字段装反了
    if len(instructions) < 50 and len(desc) > 100:
        instructions, desc = desc, instructions
    return {
        "name": name or "未命名技能",
        "code": code,
        "description": desc[:500],
        "instructions": (instructions or "").strip(),
        "sort_order": int(data.get("sort_order") or 0),
        "group_path": str(data.get("group_path") or "").strip(),
        "is_enabled": _coerce_enabled(data.get("enabled")),
    }


# ────────────────────────────────────────────────────────────────
# 资源清单（instructions 末尾自动生成，哨兵标签包裹，可剥可再生成）
# ────────────────────────────────────────────────────────────────

RESOURCE_BEGIN = "<skill-resources>"
RESOURCE_END = "</skill-resources>"
_RESOURCE_RE = re.compile(
    re.escape(RESOURCE_BEGIN) + r".*?" + re.escape(RESOURCE_END) + r"[ \t]*\n*",
    re.DOTALL,
)


def _fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def _as_file_dict(f: "SkillFile | dict") -> dict:
    return f.to_dict() if isinstance(f, SkillFile) else f


def build_resource_manifest(files: list, file_dir: str = "") -> str:
    """生成 <skill-resources> 清单块；SKILL.md/skill.json 之外无文件时返回空。

    file_dir 非空时说明配套文件已解压的磁盘目录，智能体可按路径引用。
    """
    extras = [
        _as_file_dict(f) for f in files
        if str(_as_file_dict(f).get("path", "")).lower() not in ("skill.md", "skill.json")
    ]
    if not extras:
        return ""
    extras.sort(key=lambda f: f.get("path", ""))
    max_lines = settings.SKILL_MANIFEST_MAX_LINES
    lines = []
    for f in extras[:max_lines]:
        lines.append(f"- {f.get('path')} ({_fmt_size(int(f.get('size') or 0))})")
    hidden = len(extras) - max_lines
    if hidden > 0:
        lines.append(f"…另有 {hidden} 个文件未列出")
    if file_dir:
        head = f"本技能的配套资源文件已解压至 {file_dir}/（相对后端运行目录），智能体可按路径引用："
    else:
        head = "本技能随包附带以下资源文件："
    return (
        f"{RESOURCE_BEGIN}\n"
        f"{head}\n"
        + "\n".join(lines)
        + f"\n{RESOURCE_END}"
    )


def strip_resource_manifest(instructions: str) -> str:
    """删除 instructions 中的资源清单块（覆盖导入时先剥旧清单再生成新清单）。"""
    return _RESOURCE_RE.sub("", instructions or "").rstrip()


# ────────────────────────────────────────────────────────────────
# ZIP 解析
# ────────────────────────────────────────────────────────────────

_JUNK_SEGMENTS = {"__macosx", ".ds_store", ".git", ".idea", "__pycache__", ".vscode"}
_TEXT_EXTS = {
    ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".sh", ".bash", ".zsh", ".fish",
    ".html", ".htm", ".css", ".scss", ".xml", ".csv", ".tsv", ".sql", ".rst",
    ".java", ".c", ".h", ".cpp", ".go", ".rs", ".rb", ".php", ".env", ".gitignore",
}
_SNIFF_LEN = 8 * 1024


def is_zip_bytes(data: bytes) -> bool:
    return data[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def _clean_relpath(name: str) -> str | None:
    """条目名 → 干净相对路径；目录/垃圾条目返回 None；路径穿越抛错拒收整包。"""
    p = name.replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        raise SkillImportError(f"ZIP 内含绝对路径条目：{name}")
    if any(seg == ".." for seg in p.split("/")):
        raise SkillImportError(f"ZIP 内含路径穿越条目：{name}")
    if any(seg.lower() in _JUNK_SEGMENTS for seg in p.split("/")):
        return None
    p = "/".join(seg for seg in p.split("/") if seg not in ("", "."))
    return p or None


def _check_zip_limits(zf: zipfile.ZipFile) -> None:
    """解压前预检：条目数 / 解压总量 / 单文件压缩比（zip bomb 主防线）。"""
    infos = zf.infolist()
    if len(infos) > settings.SKILL_ZIP_MAX_ENTRIES:
        raise SkillImportError(
            f"ZIP 条目数超限（{len(infos)} > {settings.SKILL_ZIP_MAX_ENTRIES}）", 413)
    total = sum(i.file_size for i in infos if not i.is_dir())
    if total > settings.SKILL_ZIP_MAX_TOTAL_UNCOMPRESSED:
        raise SkillImportError(
            f"ZIP 解压总量超限（{_fmt_size(total)} > "
            f"{_fmt_size(settings.SKILL_ZIP_MAX_TOTAL_UNCOMPRESSED)}）", 413)
    for i in infos:
        if (not i.is_dir() and i.compress_size > 0
                and i.file_size > 1024 * 1024
                and i.file_size / i.compress_size > settings.SKILL_ZIP_MAX_COMPRESSION_RATIO):
            raise SkillImportError(
                f"ZIP 内 {i.filename} 压缩比异常，疑似压缩炸弹", 413)


def _strip_common_shell(entries: dict[str, zipfile.ZipInfo]) -> dict[str, zipfile.ZipInfo]:
    """剥掉 GitHub zipball 等的唯一起始目录壳（如 owner-repo-branch/）。"""
    if not entries or any("/" not in p for p in entries):
        return entries  # 存在根级文件 → 无壳
    first = next(iter(entries)).split("/", 1)[0]
    if all(p.split("/", 1)[0] == first for p in entries):
        return {p.split("/", 1)[1]: info for p, info in entries.items()}
    return entries


def _split_by_marker(
    entries: dict[str, zipfile.ZipInfo], markers: tuple[str, ...],
) -> list[tuple[str, dict[str, zipfile.ZipInfo]]]:
    """按标记文件（SKILL.md 等）划分技能根。

    - 根级标记文件 → 单技能，全部条目归它
    - 否则每个含标记文件的目录各成一个技能根；无主的根级散文件并入唯一技能组
    """
    marker_paths = [
        p for p in entries
        if p.rsplit("/", 1)[-1].lower() in markers
    ]
    if not marker_paths:
        return []
    roots = sorted({p.rsplit("/", 1)[0] for p in marker_paths if "/" in p})
    if any("/" not in p for p in marker_paths):
        # 根级标记文件：整包一个技能
        return [("", entries)]
    groups: list[tuple[str, dict[str, zipfile.ZipInfo]]] = []
    for root in roots:
        prefix = root + "/"
        own = {p[len(prefix):]: info for p, info in entries.items() if p.startswith(prefix)}
        groups.append((prefix, own))
    # 无主条目（不在任何技能根下的散文件）
    owners = [root for root, _ in groups]
    orphans = {
        p: info for p, info in entries.items()
        if not any(p.startswith(root) for root in owners)
    }
    if orphans:
        if len(groups) == 1:
            groups[0][1].update(orphants)
            # 按 path 重排，保持清单有序
            groups[0] = (groups[0][0], dict(sorted(groups[0][1].items())))
        # 多技能包的散落文件无法归属 → 忽略
    return groups


def _fallback_json_roots(
    entries: dict[str, zipfile.ZipInfo],
) -> list[tuple[str, dict[str, zipfile.ZipInfo]]]:
    """无 SKILL.md 时退而求其次：根级/各顶层目录的 skill.json。"""
    return _split_by_marker(entries, ("skill.json",))


def _legacy_md_roots(
    entries: dict[str, zipfile.ZipInfo],
) -> list[tuple[str, dict[str, zipfile.ZipInfo]]]:
    """再退：旧格式文件名扫描（CLAUDE.md / instructions.md）。"""
    return _split_by_marker(entries, ("claude.md", "instructions.md"))


def _is_text_file(path: str, data: bytes) -> bool:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    if f".{ext}" in _TEXT_EXTS:
        return True
    head = data[:_SNIFF_LEN]
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _read_files(
    zf: zipfile.ZipFile, own_entries: dict[str, zipfile.ZipInfo],
) -> tuple[list[SkillFile], list[tuple[str, bytes]]]:
    """读取技能根下全部文件。返回 (清单元数据, 原始字节)。

    文件内容不再写进数据库——清单入库、字节留给导入执行器落盘。
    """
    manifest: list[SkillFile] = []
    raw: list[tuple[str, bytes]] = []
    for rel, info in sorted(own_entries.items()):
        data = zf.read(info)
        manifest.append(SkillFile(path=rel, size=len(data), is_text=_is_text_file(rel, data)))
        raw.append((rel, data))
    return manifest, raw


def _build_skill(
    zf: zipfile.ZipFile, prefix: str, own_entries: dict[str, zipfile.ZipInfo],
    source: str,
) -> ParsedSkill:
    """从一个技能根构建 ParsedSkill。"""
    marker = next(
        (rel for rel in own_entries
         if rel.rsplit("/", 1)[-1].lower() in ("skill.md", "skill.json",
                                               "claude.md", "instructions.md")),
        None,
    )
    if marker is None:
        raise SkillImportError("技能根下没有可识别的入口文件")

    files, raw_files = _read_files(zf, own_entries)
    marker_data = zf.read(own_entries[marker]).decode("utf-8", errors="replace")
    if marker.lower().endswith(".json"):
        parsed = parse_skill_json(json.loads(marker_data), fallback_name=source)
    else:
        parsed = parse_skill_md(marker_data, source=marker)

    name = parsed["name"]
    root_dir = prefix.rstrip("/")
    fallback_name = root_dir.split("/", 1)[0] if root_dir else ""
    source_stem = re.sub(r"\.zip$", "", source.rsplit("/", 1)[-1]) if source else ""
    if not name:
        name = fallback_name or source_stem or "未命名技能"
    code = parsed["code"] or _stable_code(name, fallback_name, source_stem)

    # 此处清单不带磁盘目录（执行器拿到最终 code 落盘后会剥掉重生成）
    instructions = parsed["instructions"]
    manifest = build_resource_manifest(files)
    if manifest and instructions:
        instructions = f"{instructions}\n\n{manifest}"
    elif manifest:
        instructions = manifest

    return ParsedSkill(
        name=name,
        code=code,
        description=parsed["description"],
        instructions=instructions,
        sort_order=int(parsed.get("sort_order") or 0),
        files=files,
        origin=prefix or marker,
        raw_files=raw_files,
        group_path=parsed.get("group_path") or "",
        is_enabled=parsed.get("is_enabled"),
    )


def parse_zip_bytes(data: bytes, source: str = "") -> list[ParsedSkill]:
    """解析完整 ZIP 技能包 → ParsedSkill 列表（一个包可含多个技能）。"""
    if len(data) > settings.SKILL_ZIP_MAX_UPLOAD_BYTES:
        raise SkillImportError(
            f"ZIP 体积超限（{_fmt_size(len(data))} > "
            f"{_fmt_size(settings.SKILL_ZIP_MAX_UPLOAD_BYTES)}）", 413)
    if not is_zip_bytes(data):
        raise SkillImportError("不是有效的 ZIP 文件")
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        raise SkillImportError("ZIP 文件损坏或格式不正确")

    with zf:
        _check_zip_limits(zf)
        entries: dict[str, zipfile.ZipInfo] = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = _clean_relpath(info.filename)
            if rel:
                entries[rel] = info
        if not entries:
            raise SkillImportError("ZIP 内没有有效文件")

        stripped = _strip_common_shell(entries)
        groups = (_split_by_marker(stripped, ("skill.md",))
                  or _fallback_json_roots(stripped)
                  or _legacy_md_roots(stripped))
        if not groups:
            raise SkillImportError(
                "未在 ZIP 中找到 SKILL.md / skill.json / CLAUDE.md / instructions.md")

        skills: list[ParsedSkill] = []
        for prefix, own in groups:
            try:
                skills.append(_build_skill(zf, prefix, own, source))
            except (SkillImportError, json.JSONDecodeError, KeyError) as e:
                if len(groups) == 1:
                    raise SkillImportError(f"技能解析失败：{e}")
                continue  # 多技能包：单根失败不影响其余
        if not skills:
            raise SkillImportError("ZIP 内所有技能根解析均失败")

    # 同包 code 去重
    seen: dict[str, int] = {}
    for s in skills:
        n = seen.get(s.code, 0)
        seen[s.code] = n + 1
        if n:
            s.code = f"{s.code}-{n + 1}"
    return skills


# ────────────────────────────────────────────────────────────────
# JSON 导入的 files 透传清洗
# ────────────────────────────────────────────────────────────────


def sanitize_files(raw) -> list[dict]:
    """清洗外部传入的 files 数组（JSON 导入/导出回流）：非法条目丢弃，上限复检。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    total = 0
    for entry in raw[: settings.SKILL_ZIP_MAX_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        try:
            rel = _clean_relpath(path)
        except SkillImportError:
            continue  # 单条非法不拒整个导入
        if not rel:
            continue
        try:
            size = int(entry.get("size") or 0)
        except (ValueError, TypeError):
            size = 0
        content = entry.get("content")
        is_text = bool(entry.get("is_text", isinstance(content, str)))
        item = {"path": rel, "size": size, "is_text": is_text}
        if (is_text and isinstance(content, str)
                and len(content.encode("utf-8")) <= settings.SKILL_FILE_MAX_CONTENT_BYTES
                and total + len(content.encode("utf-8")) <= settings.SKILL_FILES_MAX_TOTAL_CONTENT_BYTES):
            item["content"] = content
            total += len(content.encode("utf-8"))
        out.append(item)
    return out

# ────────────────────────────────────────────────────────────────
# 配套文件落盘 / 回读 / 完整导出
# ────────────────────────────────────────────────────────────────


def _safe_dir_name(code: str) -> str:
    """code → 安全目录名（code 理论上是 slug/hash，这里防御性清洗）。"""
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", (code or "").strip()).strip("_")
    return name[:64] or "imported-skill"


def _skill_dir(code: str) -> str:
    base = (settings.SKILL_FILES_DIR or "./data/skills").strip().rstrip("/")
    return f"{base}/{_safe_dir_name(code)}"


def _extract_to_disk(code: str, raw_files: list[tuple[str, bytes]]) -> str:
    """配套文件落盘到 _skill_dir(code)；先清空旧目录，保证覆盖导入无残留。

    返回目录（POSIX 相对路径，如 data/skills/<code>）。
    """
    target_dir = Path(_skill_dir(code))
    shutil.rmtree(target_dir, ignore_errors=True)
    for rel, data in raw_files:
        try:
            clean = _clean_relpath(rel)
        except SkillImportError:
            continue  # 单条非法路径丢弃，不阻断整个技能
        if not clean:
            continue
        target = target_dir.joinpath(*clean.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return target_dir.as_posix()


def read_files_from_disk(skill: dict) -> list[dict]:
    """导出用：从 file_dir 回读配套文件——文本且不超上限的附 content，其余仅清单。

    file_dir 存在但清单为空（如清单丢失）时按磁盘实际内容重建清单。
    """
    file_dir = skill.get("file_dir") or ""
    files = skill.get("files") or []
    root = Path(file_dir) if file_dir else None
    if root is None or not root.is_dir():
        return files
    if not files:
        files = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            with open(p, "rb") as fh:
                head = fh.read(_SNIFF_LEN)
            files.append({
                "path": rel, "size": p.stat().st_size,
                "is_text": _is_text_file(rel, head),
            })
    out: list[dict] = []
    total = 0
    for f in files:
        rel = str(f.get("path", ""))
        target = root.joinpath(*[seg for seg in rel.split("/") if seg])
        item = {
            "path": rel,
            "size": int(f.get("size") or 0),
            "is_text": bool(f.get("is_text")),
        }
        if item["is_text"] and target.is_file():
            size = target.stat().st_size
            if (size <= settings.SKILL_FILE_MAX_CONTENT_BYTES
                    and total + size <= settings.SKILL_FILES_MAX_TOTAL_CONTENT_BYTES):
                item["content"] = target.read_text(encoding="utf-8", errors="replace")
                item["size"] = size
                total += size
        out.append(item)
    return out


def _fm_scalar(value) -> str:
    """frontmatter 单行标量：折叠空白；含特殊字符时双引号包裹。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return '""'
    if any(ch in text for ch in ":#'\""):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


async def build_export_zip(db: AsyncSession, skill_id: str | None = None) -> bytes:
    """导出技能为完整 ZIP：每技能一个 <code>/ 目录（SKILL.md + 配套文件，二进制原样）。

    skill_id 给定则仅导出该单个技能；否则导出全部。
    SKILL.md frontmatter 携带 code / sort_order / group_path / enabled，
    重新走 import-zip 可还原分组与启用状态；格式同时兼容 Claude Code 生态。
    """
    from models import AgentSkill
    from services.skill_group_service import group_paths

    query = select(AgentSkill).order_by(AgentSkill.sort_order, AgentSkill.created_at)
    if skill_id:
        query = query.where(AgentSkill.id == skill_id)
    result = await db.execute(query)
    skills = result.scalars().all()
    if skill_id and not skills:
        return b""  # 技能不存在 → 空包，路由层据此返回 404
    paths = await group_paths(db)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in skills:
            try:
                files = json.loads(s.files) if s.files else []
                if not isinstance(files, list):
                    files = []
            except (ValueError, TypeError):
                files = []
            enriched = read_files_from_disk({"file_dir": s.file_dir or "", "files": files})
            dir_name = _safe_dir_name(s.code)

            fm = [
                "---",
                f"name: {_fm_scalar(s.name)}",
                f"description: {_fm_scalar(s.description)}",
                f"code: {s.code}",
            ]
            if s.sort_order:
                fm.append(f"sort_order: {int(s.sort_order)}")
            group_path = paths.get(s.group_id) if s.group_id else ""
            if group_path:
                fm.append(f"group_path: {_fm_scalar(group_path)}")
            fm.append(f"enabled: {'true' if s.is_enabled else 'false'}")
            fm.append("---")
            body = strip_resource_manifest(s.instructions or "").strip()
            zf.writestr(f"{dir_name}/SKILL.md", "\n".join(fm) + "\n\n" + body + "\n")

            root = Path(s.file_dir) if s.file_dir else None
            for f in enriched:
                rel = str(f.get("path", ""))
                if rel.lower() == "skill.md":
                    continue  # SKILL.md 由上方重新生成，避免 zip 内重名
                data: bytes | None = None
                if root is not None:
                    target = root.joinpath(*[seg for seg in rel.split("/") if seg])
                    if target.is_file():
                        data = target.read_bytes()
                if data is None and isinstance(f.get("content"), str):
                    data = f["content"].encode("utf-8")  # 旧数据：内容仍在数据库
                if data is not None:
                    zf.writestr(f"{dir_name}/{rel}", data)
    return buf.getvalue()


async def sync_skill_files_to_disk(db: AsyncSession) -> int:
    """存量迁移：files 清单中带 content 的行 → 内容写入磁盘、剥离 content、回写 file_dir。

    幂等（无 content 键即跳过）；失败只记日志不抛出，不阻断启动。
    """
    from models import AgentSkill

    try:
        rows = (await db.execute(select(AgentSkill))).scalars().all()
        moved = 0
        for s in rows:
            try:
                files = json.loads(s.files) if s.files else []
            except (ValueError, TypeError):
                continue
            if not isinstance(files, list) or not any(
                    isinstance(f, dict) and isinstance(f.get("content"), str) for f in files):
                continue
            raw = [
                (f["path"], f["content"].encode("utf-8"))
                for f in files
                if isinstance(f, dict) and isinstance(f.get("content"), str)
            ]
            s.file_dir = _extract_to_disk(s.code, raw) if raw else ""
            s.files = json.dumps(
                [{k: v for k, v in f.items() if k != "content"}
                 for f in files if isinstance(f, dict)],
                ensure_ascii=False,
            )
            moved += 1
        if moved:
            await db.commit()
            logger.info("Synced %d skills' files to disk", moved)
        return moved
    except Exception:  # noqa: BLE001 —— 迁移失败不阻断启动
        await db.rollback()
        logger.exception("sync_skill_files_to_disk failed")
        return 0


# ────────────────────────────────────────────────────────────────
# 共用导入执行器（import / import-zip / import-url 三端点走这里）
# ────────────────────────────────────────────────────────────────


async def import_skills(
    db: AsyncSession, items: list, *, overwrite: bool = False,
    group_id: str | None = None,
) -> ImportOutcome:
    """逐项导入技能。

    - code 不存在        → create
    - code 存在 & overwrite → update（保留 id / is_enabled / is_preset；sort_order 仅在
                          导入项给出非 0 值时才覆盖，避免重置用户自定义排序）
    - code 存在 & 非 overwrite → skipped + duplicates 双记录（前端弹窗数据源）
    - 单项异常进 errors，不整包失败

    分组归属优先级：逐项 group_path（导出回流，按路径找或建）> 请求级 group_id > 未分组；
    overwrite 仅在导入项自带 group_path 时才移动已有技能分组（请求级 group_id 不动用户手动归类）。

    配套文件：raw_files 落盘到 SKILL_FILES_DIR/<code>/，files 列只存清单；
    instructions 统一走「剥旧清单 → 按最终 file_dir 重生成清单」，保证幂等可再生成。
    """
    from models import AgentSkill
    from services.skill_service import SkillService

    outcome = ImportOutcome()

    for idx, item in enumerate(items):
        parsed_obj: ParsedSkill | None = None
        if isinstance(item, ParsedSkill):
            parsed_obj = item
            item = item.to_dict()
        try:
            if not isinstance(item, dict):
                raise SkillImportError("格式错误：须为对象")
            name = (item.get("name") or "").strip()
            code = (item.get("code") or "").strip()
            if not code and name:
                code = _stable_code(name)
            if not name or not code:
                outcome.errors.append({
                    "index": idx, "origin": item.get("origin", ""),
                    "reason": "name 或 code 为空",
                })
                continue

            # 分组归属：逐项 group_path（导出回流）> 请求级 group_id > 未分组
            target_group_id = group_id
            path_group_id = None
            raw_path = (item.get("group_path") or "").strip()
            if raw_path:
                parts = [p for p in (seg.strip() for seg in raw_path.split("/")) if p]
                if parts:
                    grp = await resolve_group_path(db, parts)
                    if grp is not None:
                        path_group_id = grp.id
                        target_group_id = grp.id

            result = await db.execute(
                select(AgentSkill).where(AgentSkill.code == code))
            existing = result.scalar_one_or_none()

            # 配套文件：ZIP 解析的 raw_files / JSON 旧格式 content → 落盘，清单入库
            files = sanitize_files(item.get("files"))
            raw_files = list(parsed_obj.raw_files) if parsed_obj else []
            if not raw_files:
                raw_files = [
                    (f["path"], f["content"].encode("utf-8"))
                    for f in files if isinstance(f.get("content"), str)
                ]
            file_dir = _extract_to_disk(code, raw_files) if raw_files else ""

            # 覆盖更新但导入项不带文件：沿用已有清单/目录重生成 manifest，别把存量洗掉
            if existing is not None and not files and not file_dir:
                try:
                    existing_files = json.loads(existing.files) if existing.files else []
                except (ValueError, TypeError):
                    existing_files = []
                if isinstance(existing_files, list) and existing_files:
                    files = existing_files
                    file_dir = existing.file_dir or ""

            instructions = strip_resource_manifest((item.get("instructions") or "").strip())
            manifest = build_resource_manifest(files, file_dir)
            if manifest:
                instructions = f"{instructions}\n\n{manifest}" if instructions else manifest

            payload = {
                "name": name,
                "code": code,
                "description": (item.get("description") or "").strip(),
                "instructions": instructions,
            }
            if files or file_dir:
                payload["files"] = files
                payload["file_dir"] = file_dir

            if existing is None:
                payload["sort_order"] = int(item.get("sort_order") or 0)
                if item.get("is_enabled") is not None:
                    payload["is_enabled"] = int(item["is_enabled"])
                if target_group_id:
                    payload["group_id"] = target_group_id
                created = await SkillService.create(db, payload)
                outcome.imported.append({"id": created["id"], "code": code, "name": name})
            elif overwrite:
                if int(item.get("sort_order") or 0):
                    payload["sort_order"] = int(item["sort_order"])
                if path_group_id:
                    payload["group_id"] = path_group_id
                await SkillService.update(db, existing.id, payload)
                outcome.updated.append({"id": existing.id, "code": code, "name": name})
            else:
                outcome.skipped.append({"code": code, "name": name})
                outcome.duplicates.append({
                    "code": code, "name": name, "existing_name": existing.name,
                })
        except Exception as e:  # noqa: BLE001 —— 单项失败不阻断其余
            outcome.errors.append({
                "index": idx,
                "code": item.get("code", "?") if isinstance(item, dict) else "?",
                "reason": str(e),
            })

    return outcome
