from __future__ import annotations

import asyncio
import gzip
import json
import logging
import re
import time
import uuid
import zlib
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session
from models import CrawlJob
from services.library_service import ASSET_DIR, LibraryService

logger = logging.getLogger(__name__)

LOG_TAIL_LIMIT = 120


def _now_iso() -> str:
    return datetime.now().isoformat()


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._skip = False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip = True
        if tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip = False
        if tag in {"p", "div", "section", "article", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text)

    def text(self) -> str:
        content = " ".join(self.parts)
        content = re.sub(r"\s+", " ", content)
        content = re.sub(r"\s*\n\s*", "\n", content)
        return unescape(content).strip()


def _empty_detail() -> dict:
    return {
        "stage": "idle",
        "started_at": None,
        "finished_at": None,
        "elapsed_ms": 0,
        "stages": {
            "search": {"progress": 0, "label": "等待开始", "started_at": None, "finished_at": None, "elapsed_ms": 0},
            "fetch":  {"progress": 0, "label": "等待开始", "current": 0, "total": 0, "started_at": None, "finished_at": None, "elapsed_ms": 0},
            "llm":    {"progress": 0, "label": "等待开始", "started_at": None, "finished_at": None, "elapsed_ms": 0},
            "save":   {"progress": 0, "label": "等待开始", "started_at": None, "finished_at": None, "elapsed_ms": 0},
        },
    }


def _parse_json_field(raw: str | None, default):
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return default


def _job_to_dict(job: CrawlJob) -> dict:
    return {
        "id": job.id,
        "keyword": job.keyword,
        "directory_id": job.directory_id,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "urls": json.loads(job.urls or "[]"),
        "file_count": job.file_count,
        "detail": _parse_json_field(job.detail, None),
        "logs": _parse_json_field(job.logs, []),
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }


def _browser_headers() -> dict:
    """更完整的浏览器请求头，降低被轻量反爬拦截的概率。"""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.bing.com/",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _decompress(raw: bytes, encoding: str) -> bytes:
    """部分 CDN/代理即使未请求 gzip 也可能压缩，这里做防御性解压。"""
    encoding = (encoding or "").lower()
    if "gzip" in encoding:
        try:
            return gzip.decompress(raw)
        except OSError:
            return raw
    if "deflate" in encoding:
        try:
            return zlib.decompress(raw)
        except OSError:
            try:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except OSError:
                return raw
    return raw


def _fetch_url(url: str, timeout: int, retries: int = 2) -> str:
    """直连抓取原始 HTML。带完整浏览器头 + 指数退避重试。
    4xx（除 429）不重试——重试也不会变；5xx / 超时 / 网络错误会重试。"""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers=_browser_headers())
            with urlopen(req, timeout=timeout) as response:
                raw = response.read(2 * 1024 * 1024)
                raw = _decompress(raw, response.headers.get("content-encoding", ""))
                content_type = response.headers.get("content-type", "")
            charset = "utf-8"
            match = re.search(r"charset=([\w-]+)", content_type, re.I)
            if match:
                charset = match.group(1)
            return raw.decode(charset, errors="ignore")
        except HTTPError as e:
            last_exc = e
            # 客户端错误（除限流 429）重试无意义，直接抛出
            if 400 <= e.code < 500 and e.code != 429:
                raise
            if attempt >= retries:
                raise
        except (URLError, TimeoutError, OSError) as e:
            last_exc = e
            if attempt >= retries:
                raise
        time.sleep(2 ** attempt)  # 1s, 2s, 4s ...
    # 理论上不可达；保险起见抛出最后一次异常
    raise last_exc if last_exc else RuntimeError(f"抓取失败：{url}")


def _fetch_via_jina(url: str, timeout: int) -> str:
    """通过 Jina Reader (r.jina.ai) 抓取，服务端渲染 JS 并返回干净的 Markdown 正文。
    用于救场两类页面：① 今日头条等 SPA（直连 HTML 无正文）；② 部分反爬页。"""
    jina_url = f"https://r.jina.ai/{url}"
    headers = {
        "User-Agent": _browser_headers()["User-Agent"],
        "Accept": "text/plain, text/markdown, */*",
    }
    token = getattr(settings, "CRAWL_JINA_TOKEN", "") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(jina_url, headers=headers)
    # Jina 需要服务端渲染，给更宽松的超时
    with urlopen(req, timeout=max(30, timeout)) as response:
        raw = response.read(2 * 1024 * 1024)
    return raw.decode("utf-8", errors="ignore")


def _extract_jina_title(jina_text: str, fallback: str) -> str:
    """Jina 输出通常以 'Title: xxx' 开头。"""
    match = re.match(r"\s*Title:\s*(.+)", jina_text)
    if match:
        return match.group(1).strip()[:120]
    return fallback


def _fetch_document(url: str, timeout: int) -> tuple[str | None, str, str | None, Exception | None]:
    """抓取单个 URL 为 (title, text, method, error)。
    先直连（带重试 + 完整头），失败或正文过短则回退 Jina Reader。
    method ∈ {'direct', 'jina', None}；二者都拿不到时 error 为直连异常（便于日志）。"""
    html: str | None = None
    direct_error: Exception | None = None
    try:
        html = _fetch_url(url, timeout)
    except Exception as e:
        direct_error = e

    title: str | None = None
    text = ""
    method: str | None = None

    if html is not None:
        title = _extract_title(html, url)
        text = _html_to_text(html)
        if len(text) >= 200:
            method = "direct"

    # 直连失败或正文过短（多为 JS 渲染 / SPA）→ 回退 Jina
    if method is None and getattr(settings, "CRAWL_JINA_FALLBACK", True):
        try:
            jina_text = _fetch_via_jina(url, timeout)
            if len(jina_text) >= 200:
                text = jina_text.strip()
                title = _extract_jina_title(jina_text, title or url)
                method = "jina"
        except Exception:
            # Jina 也失败则保留直连的结果/异常，交由调用方记录
            pass

    if method is None:
        # 没拿到有效内容：清空 text 以免存入空串
        text = ""
    return title, text, method, direct_error


def _extract_title(html: str, fallback: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if not match:
        return fallback
    title = re.sub(r"\s+", " ", match.group(1)).strip()
    return unescape(title)[:120] or fallback


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


def _normalize_search_url(url: str) -> str | None:
    if not url:
        return None
    url = unescape(url)
    if url.startswith("//"):
        url = "https:" + url
    if "duckduckgo.com/l/" in url:
        parsed = urlparse(url)
        value = parse_qs(parsed.query).get("uddg", [""])[0]
        url = unquote(value)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if "duckduckgo.com" in parsed.netloc:
        return None
    return url


def _search_tavily(keyword: str, limit: int, timeout: int) -> list[str]:
    """用 Tavily API 搜索（专门为 AI 设计的搜索，返回高质量结果）。"""
    api_key = settings.TAVILY_API_KEY
    if not api_key:
        raise RuntimeError(
            "Tavily API Key 未配置，请在 .env 中设置 TAVILY_API_KEY="
            "（可到 https://www.tavily.com/ 免费注册，每月 1000 次额度）"
        )
    search_url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": keyword,
        "max_results": limit,
        "search_depth": "basic",
    }
    logger.info("Tavily 搜索请求：%s", keyword)
    import json as _json
    try:
        req = Request(
            search_url,
            data=_json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read())
    except Exception as e:
        raise RuntimeError(
            f"Tavily API 调用失败：{e}\n"
            "可能原因：网络不通、API Key 无效、或 Tavily 服务不可达"
        )
    results = data.get("results", [])
    urls = [r["url"] for r in results if r.get("url")]
    logger.info("Tavily 搜索到 %d 个结果", len(urls))
    if not urls:
        raise RuntimeError(
            "Tavily API 未返回任何搜索结果，请检查 API Key 是否有效或搜索关键词是否合理"
        )
    return urls[:limit]


def _search_bing(keyword: str, limit: int, timeout: int) -> list[str]:
    """用 Bing 国内版搜索，国内可直连。只提取 b_algo 块中的结果链接。"""
    search_url = f"https://cn.bing.com/search?q={quote_plus(keyword)}&count={min(limit, 50)}"
    logger.info("Bing 搜索请求：%s", search_url)
    try:
        html = _fetch_url(search_url, timeout)
    except Exception as e:
        raise RuntimeError(
            f"Bing 搜索网络请求失败：{e}\n"
            "可能原因：网络不通、需要代理、或 Bing 暂时不可达。可尝试切换 SEARCH_PROVIDER=tavily 或 duckduckgo"
        )
    logger.debug("Bing 响应长度：%d 字符", len(html))
    # Bing 搜索结果在 <li class="b_algo"> 或 <li class="b_algo ..."> 块中
    algo_blocks = re.findall(r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>', html, re.S)
    raw_urls: list[str] = []
    if algo_blocks:
        for block in algo_blocks:
            m = re.search(r'href="(https?://[^"]+)"', block)
            if m:
                raw_urls.append(m.group(1))
    if not raw_urls:
        # 兜底：直接匹配 <h2><a href="..."> 结构
        raw_urls = re.findall(r'<h2[^>]*>\s*<a[^>]*href="(https?://[^"]+)"[^>]*>', html)
    urls: list[str] = []
    seen: set[str] = set()
    skip_domains = {"bing.com", "microsoft.com", "msn.com", "go.microsoft.com"}
    for url in raw_urls:
        parsed = urlparse(url)
        if any(d in parsed.netloc for d in skip_domains):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    logger.info("Bing 搜索到 %d 个链接", len(urls))
    if not urls:
        raise RuntimeError(
            "Bing 搜索未返回任何结果，可能触发了验证码或反爬限制\n"
            "建议：1) 稍后重试  2) 切换搜索引擎 SEARCH_PROVIDER=tavily（推荐，每月 1000 次免费）"
        )
    return urls


def _search_duckduckgo(keyword: str, limit: int, timeout: int) -> list[str]:
    """用 DuckDuckGo HTML 版搜索，作为备选。"""
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(keyword)}"
    logger.info("DuckDuckGo 搜索请求：%s", search_url)
    try:
        html = _fetch_url(search_url, timeout)
    except Exception as e:
        raise RuntimeError(
            f"DuckDuckGo 搜索网络请求失败：{e}\n"
            "可能原因：网络不通、需要代理。可尝试切换 SEARCH_PROVIDER=tavily 或 bing"
        )
    logger.debug("DuckDuckGo 响应长度：%d 字符", len(html))
    candidates = re.findall(r'href="([^"]+)"[^>]*class="result__a"', html)
    if not candidates:
        candidates = re.findall(r'class="result__a"[^>]*href="([^"]+)"', html)
    logger.info("DuckDuckGo 搜索到 %d 个候选链接", len(candidates))
    urls: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        url = _normalize_search_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    if not urls:
        raise RuntimeError(
            "DuckDuckGo 搜索未返回任何结果，可能被限制或网络不通\n"
            "建议：1) 稍后重试  2) 切换 SEARCH_PROVIDER=tavily（推荐，每月 1000 次免费）"
        )
    return urls


def _search_web(keyword: str, limit: int, timeout: int) -> list[str]:
    """按配置的搜索引擎搜索，抛出明确错误便于排查。"""
    provider = settings.SEARCH_PROVIDER.strip().lower()
    known = {"tavily", "duckduckgo", "bing"}
    if provider not in known:
        raise RuntimeError(
            f"未知的搜索引擎 [{provider}]，请在 .env 中设置 SEARCH_PROVIDER 为 tavily / bing / duckduckgo 之一"
        )
    try:
        if provider == "tavily":
            return _search_tavily(keyword, limit, timeout)
        elif provider == "duckduckgo":
            return _search_duckduckgo(keyword, limit, timeout)
        else:
            return _search_bing(keyword, limit, timeout)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(
            f"搜索引擎 [{provider}] 调用异常：{e}\n"
            "请检查 .env 中 SEARCH_PROVIDER 及相关 API Key 配置"
        )


def _clean_thinking(content: str) -> str:
    return re.sub(r"<think[\s\S]*?</think\s*>", "", content).strip()


def _summarize_with_llm(keyword: str, documents: list[dict], depth: str = "medium") -> tuple[str, str]:
    joined = "\n\n".join(
        f"来源：{item['url']}\n标题：{item['title']}\n正文摘录：{item['text'][:5000]}"
        for item in documents
    )

    depth_config = {
        "low": {
            "prompt_suffix": "要求：简洁整理，保留核心事实和来源链接，给出 3 条要点。",
            "max_length": 12000,
            "text_limit": 2000,
        },
        "medium": {
            "prompt_suffix": "要求：保留事实边界，不要编造；按主题分节；列出来源链接；最后给出 5 条要点。",
            "max_length": 24000,
            "text_limit": 3000,
        },
        "high": {
            "prompt_suffix": "要求：详细分析整理，深入挖掘信息；按主题分节并展开子主题；列出来源链接；给出 8-10 条要点；提供多角度分析。",
            "max_length": 40000,
            "text_limit": 5000,
        },
    }

    config = depth_config.get(depth, depth_config["medium"])

    fallback = "\n\n".join(
        f"## {item['title']}\n\n来源：{item['url']}\n\n{item['text'][:config['text_limit']]}"
        for item in documents
    )
    try:
        from providers.llm import create_llm

        llm = create_llm()
        prompt = (
            f"你是资料采集助手。请基于给定网页摘录，整理一份可进入知识库的中文 Markdown 文档。\n"
            f"{config['prompt_suffix']}\n\n"
            f"关键词：{keyword}\n\n{joined[:config['max_length']]}"
        )
        response = llm.invoke(prompt)
        content = getattr(response, "content", str(response)).strip()
        content = _clean_thinking(content)
        if content:
            summary = content.split("\n", 1)[0].strip("# ").strip()[:200]
            return content, summary or f"{keyword} 采集资料"
    except Exception:
        pass
    return fallback, f"{keyword} 采集资料"


class CrawlService:
    @staticmethod
    async def _append_log(
        db: AsyncSession,
        job: CrawlJob,
        level: str,
        message: str,
    ):
        logs: list[dict] = _parse_json_field(job.logs, [])
        logs.append({"time": _now_iso(), "level": level, "message": message[:500]})
        if len(logs) > LOG_TAIL_LIMIT:
            logs = logs[-LOG_TAIL_LIMIT:]
        job.logs = json.dumps(logs, ensure_ascii=False)
        if level == "error":
            logger.error("[crawl:%s] %s", job.id, message)
        elif level == "warning":
            logger.warning("[crawl:%s] %s", job.id, message)
        else:
            logger.info("[crawl:%s] %s", job.id, message)
        await db.commit()

    @staticmethod
    async def _update_stage(
        db: AsyncSession,
        job: CrawlJob,
        *,
        stage: str,
        progress: int,
        message: str,
        status: str | None = None,
        finished: bool = False,
        stage_extra: dict | None = None,
    ):
        now = _now_iso()
        detail: dict = _parse_json_field(job.detail, _empty_detail())

        if detail.get("started_at") is None:
            detail["started_at"] = now
        if finished:
            detail["finished_at"] = now
            if detail.get("started_at"):
                try:
                    elapsed = (datetime.fromisoformat(detail["finished_at"]) -
                               datetime.fromisoformat(detail["started_at"]))
                    detail["elapsed_ms"] = int(elapsed.total_seconds() * 1000)
                except (ValueError, TypeError):
                    pass
        detail["stage"] = stage

        stages = detail.setdefault("stages", {})
        stage_obj = stages.setdefault(stage, {"progress": 0, "label": ""})

        if stage_obj.get("started_at") is None and progress > 0:
            stage_obj["started_at"] = now
        if progress >= 100:
            stage_obj["finished_at"] = now
            if stage_obj.get("started_at"):
                try:
                    elapsed = (datetime.fromisoformat(stage_obj["finished_at"]) -
                               datetime.fromisoformat(stage_obj["started_at"]))
                    stage_obj["elapsed_ms"] = int(elapsed.total_seconds() * 1000)
                except (ValueError, TypeError):
                    pass
        stage_obj["progress"] = progress
        stage_obj["label"] = message
        if stage_extra:
            stage_obj.update(stage_extra)

        # Mark later stages as skipped if this stage just completed and they haven't started
        stage_order = ["search", "fetch", "llm", "save"]
        current_idx = stage_order.index(stage) if stage in stage_order else -1
        for later in stage_order[current_idx + 1:]:
            later_obj = stages.get(later)
            if later_obj and later_obj.get("started_at") is None:
                later_obj["progress"] = 0
                later_obj["label"] = "等待开始"

        job.detail = json.dumps(detail, ensure_ascii=False)
        job.progress = max(0, min(100, progress))
        job.message = message[:300]
        if status is not None:
            job.status = status
        if finished:
            job.finished_at = now
        await db.commit()

    @staticmethod
    async def create_job(
        db: AsyncSession,
        keyword: str,
        *,
        directory_id: str | None = None,
        max_pages: int | None = None,
        auto_attach_kb_id: str | None = None,
        auto_process: bool = False,
        extract_graph: bool = True,
        analysis_depth: str = "medium",
    ) -> dict:
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("Keyword is required")
        if not settings.CRAWL_ENABLED:
            raise ValueError("Crawl is disabled")

        if directory_id is None:
            directory = await LibraryService.default_crawl_directory(db, keyword)
            directory_id = directory.id

        job = CrawlJob(
            keyword=keyword,
            directory_id=directory_id,
            status="queued",
            progress=0,
            message="等待采集",
            urls="[]",
            detail=json.dumps(_empty_detail(), ensure_ascii=False),
            logs="[]",
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)

        asyncio.create_task(
            CrawlService._run_job(
                job.id,
                max_pages=max_pages,
                auto_attach_kb_id=auto_attach_kb_id,
                auto_process=auto_process,
                extract_graph=extract_graph,
                analysis_depth=analysis_depth,
            )
        )
        return _job_to_dict(job)

    @staticmethod
    async def get_job(db: AsyncSession, job_id: str) -> dict | None:
        job = await db.get(CrawlJob, job_id)
        return _job_to_dict(job) if job else None

    @staticmethod
    async def get_latest_job(db: AsyncSession) -> dict | None:
        from sqlalchemy import select, desc
        result = await db.execute(
            select(CrawlJob).order_by(desc(CrawlJob.created_at)).limit(1)
        )
        job = result.scalar_one_or_none()
        return _job_to_dict(job) if job else None

    @staticmethod
    async def _run_job(
        job_id: str,
        *,
        max_pages: int | None,
        auto_attach_kb_id: str | None,
        auto_process: bool,
        extract_graph: bool,
        analysis_depth: str = "medium",
    ):
        async with async_session() as db:
            job = await db.get(CrawlJob, job_id)
            if not job:
                return
            try:
                limit = max(1, max_pages or settings.CRAWL_MAX_PAGES)

                # ── Stage: Search ──
                await CrawlService._update_stage(db, job, stage="search", progress=5, message="正在搜索互联网资料", status="running")
                await CrawlService._append_log(db, job, "info", f"开始采集，关键词：{job.keyword}，最大页数：{limit}")

                urls = await asyncio.to_thread(
                    _search_web,
                    job.keyword,
                    limit,
                    settings.CRAWL_TIMEOUT_SECONDS,
                )
                if not urls:  # 防御性检查，正常情况下 _search_* 会直接抛异常
                    raise RuntimeError(
                        f"搜索引擎 [{settings.SEARCH_PROVIDER}] 未返回任何结果，"
                        "请检查网络连接或 .env 中 SEARCH_PROVIDER 及相关 API Key 配置"
                    )

                job.urls = json.dumps(urls, ensure_ascii=False)
                await db.commit()
                await CrawlService._update_stage(db, job, stage="search", progress=100, message=f"搜索完成，发现 {len(urls)} 个网页")
                await CrawlService._append_log(db, job, "info", f"搜索引擎返回 {len(urls)} 个结果 URL")

                # ── Stage: Fetch ──
                await CrawlService._update_stage(db, job, stage="fetch", progress=0, message=f"准备抓取 {len(urls)} 个网页")
                await CrawlService._append_log(db, job, "info", f"开始逐个抓取网页内容")

                documents: list[dict] = []
                for index, url in enumerate(urls, start=1):
                    pct = int(index / len(urls) * 100)
                    await CrawlService._update_stage(
                        db, job, stage="fetch", progress=pct,
                        message=f"抓取网页 {index}/{len(urls)}",
                        stage_extra={"current": index, "total": len(urls)},
                    )
                    try:
                        title, text, method, fetch_exc = await asyncio.to_thread(
                            _fetch_document, url, settings.CRAWL_TIMEOUT_SECONDS
                        )
                        if method:
                            documents.append({"url": url, "title": title or url, "text": text})
                            tag = "（通过 Jina Reader）" if method == "jina" else ""
                            await CrawlService._append_log(db, job, "info", f"[{index}/{len(urls)}] 抓取成功{tag}：{(title or url)[:60]}（{len(text)} 字）")
                        elif fetch_exc is not None:
                            await CrawlService._append_log(db, job, "error", f"[{index}/{len(urls)}] 抓取失败：{url[:80]} — {fetch_exc}")
                        else:
                            await CrawlService._append_log(db, job, "warning", f"[{index}/{len(urls)}] 跳过（直连与 Jina 均未取到有效正文）：{url[:80]}")
                    except Exception as exc:
                        await CrawlService._append_log(db, job, "error", f"[{index}/{len(urls)}] 抓取失败：{url[:80]} — {exc}")
                    if settings.CRAWL_RATE_LIMIT_SECONDS > 0:
                        await asyncio.to_thread(time.sleep, settings.CRAWL_RATE_LIMIT_SECONDS)

                if not documents:
                    raise RuntimeError("网页抓取失败或正文过短")

                await CrawlService._update_stage(db, job, stage="fetch", progress=100, message=f"抓取完成，有效网页 {len(documents)}/{len(urls)}")
                await CrawlService._append_log(db, job, "info", f"抓取阶段完成，共 {len(documents)} 个有效文档（{len(urls) - len(documents)} 个失败或跳过）")

                # ── Stage: LLM ──
                await CrawlService._update_stage(db, job, stage="llm", progress=10, message="正在调用大模型清洗整理资料")
                await CrawlService._append_log(db, job, "info", f"调用大模型整理资料，分析维度：{analysis_depth}，文档数：{len(documents)}")

                markdown, summary = await asyncio.to_thread(_summarize_with_llm, job.keyword, documents, analysis_depth)

                await CrawlService._update_stage(db, job, stage="llm", progress=100, message="大模型整理完成")
                await CrawlService._append_log(db, job, "info", f"大模型返回内容 {len(markdown)} 字")

                # ── Stage: Save ──
                await CrawlService._update_stage(db, job, stage="save", progress=20, message="正在保存采集文件")

                ASSET_DIR.mkdir(parents=True, exist_ok=True)
                file_id = uuid.uuid4().hex[:12]
                source_path = ASSET_DIR / f"{file_id}_crawl.md"
                source_lines = "\n".join(f"- {item['title']}: {item['url']}" for item in documents)
                source_path.write_text(
                    (
                        f"---\nkeyword: {job.keyword}\ncrawled_at: {_now_iso()}\n"
                        f"sources: {len(documents)}\n---\n\n"
                        f"# {job.keyword}\n\n## 来源\n\n{source_lines}\n\n## 整理内容\n\n{markdown}\n"
                    ),
                    encoding="utf-8",
                )
                asset = await LibraryService.create_asset_from_path(
                    db,
                    source_path,
                    name=f"{job.keyword}.md",
                    directory_id=job.directory_id,
                    source_type="crawl",
                    source_url=documents[0]["url"],
                    source_keyword=job.keyword,
                    sources=[{"url": d["url"], "title": d["title"]} for d in documents],
                    summary=summary,
                    move=True,
                )
                await CrawlService._append_log(db, job, "info", f"已保存文件：{asset.name}（{asset.size} 字节）")

                if auto_attach_kb_id:
                    await CrawlService._append_log(db, job, "info", f"正在添加到知识库...")
                    await LibraryService.attach_assets_to_kb(
                        db,
                        auto_attach_kb_id,
                        [asset.id],
                        auto_process=auto_process,
                        extract_graph=extract_graph,
                    )

                await CrawlService._update_stage(
                    db, job, stage="save", progress=100,
                    message="采集完成，已保存到文件管理",
                    status="done", finished=True,
                    stage_extra={"file_count": 1},
                )
                job.file_count = 1
                await db.commit()
                await CrawlService._append_log(db, job, "info", f"采集全部完成")

            except Exception as exc:
                logger.exception("[crawl:%s] 采集失败", job_id)
                await CrawlService._update_stage(
                    db, job, stage=job.detail and _parse_json_field(job.detail, {}).get("stage", "search"),
                    progress=job.progress, message=f"采集失败：{exc}",
                    status="failed", finished=True,
                )
                await CrawlService._append_log(db, job, "error", f"采集失败：{exc}")
