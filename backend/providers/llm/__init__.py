"""LLM 工厂（LangChain 封装）。

- provider=openai：OpenAI 兼容协议，覆盖 OpenAI 官方 / DeepSeek / 通义千问 / 智谱 / 自定义 OpenAI 格式。
- provider=anthropic：Anthropic 官方协议（需安装 langchain-anthropic）。
"""

import logging

from config import settings

_logger = logging.getLogger(__name__)

# ═══════════════ 修补 langchain-openai，保留 reasoning_content ═══════════════
# langchain-openai 1.2.2 的 _convert_delta_to_message_chunk 会丢弃 raw delta 中的
# reasoning_content / reasoning / thinking 等字段，导致 DeepSeek R1 / Qwen QwQ / qwen3-thinking
# 等 OpenAI 兼容接口的反思内容无法在 LangChain chunk 中取得。
# 这里在打补丁，把 raw delta 里的推理字段透传到 AIMessageChunk.additional_kwargs。
try:
    import langchain_openai.chat_models.base as _openai_base
    from langchain_core.messages import AIMessageChunk

    _orig_convert_delta = _openai_base._convert_delta_to_message_chunk

    def _convert_delta_to_message_chunk_with_reasoning(_dict, default_class):
        msg = _orig_convert_delta(_dict, default_class)
        if isinstance(msg, AIMessageChunk) and msg.additional_kwargs is not None:
            for key in ("reasoning_content", "reasoning", "reasoning_text", "thinking"):
                value = _dict.get(key)
                if value and not msg.additional_kwargs.get(key):
                    msg.additional_kwargs[key] = value
                    break
        return msg

    _openai_base._convert_delta_to_message_chunk = _convert_delta_to_message_chunk_with_reasoning
    _logger.debug("patched langchain_openai _convert_delta_to_message_chunk for reasoning_content")
except Exception as _patch_err:  # pragma: no cover
    _logger.debug("failed to patch langchain_openai reasoning_content: %s", _patch_err)

_llm = None


def build_llm(provider, api_key, base_url, model, max_tokens, temperature):
    """根据显式参数构造一个 LLM 实例（不缓存，供测试连接使用）。"""
    provider = (provider or settings.LLM_PROVIDER or "openai").lower()

    if provider == "anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError as e:
            raise RuntimeError(
                "未安装 langchain-anthropic，无法使用 Anthropic 格式。"
                "请先执行 pip install langchain-anthropic"
            ) from e
        kwargs = dict(
            model=model,
            anthropic_api_key=api_key,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if base_url:
            kwargs["anthropic_api_url"] = base_url
        return ChatAnthropic(**kwargs)

    # 默认 OpenAI 兼容协议
    from langchain_openai import ChatOpenAI
    kwargs = dict(
        model=model,
        api_key=api_key,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def create_llm():
    """创建/获取 LLM 单例。未配置 API KEY 时跳过（延迟到页面配置后创建）。"""
    global _llm
    if _llm is not None:
        return _llm

    if not settings.OPENAI_API_KEY or not settings.LLM_MODEL:
        return None

    _llm = build_llm(
        provider=settings.LLM_PROVIDER,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        model=settings.LLM_MODEL,
        max_tokens=settings.LLM_MAX_TOKENS,
        temperature=settings.LLM_TEMPERATURE,
    )
    return _llm


def reset_llm():
    """重置 LLM 单例；下次 create_llm 会按最新 settings 重建。"""
    global _llm
    _llm = None


# ══════════════════════ 流式 chunk 兼容提取 ══════════════════════
# OpenAI 兼容格式的推理/反思内容在不同厂商的实现中位置不一：
# - DeepSeek R1 / Qwen QwQ / qwen3-thinking：additional_kwargs.reasoning_content
# - 部分 OpenAI 兼容网关：additional_kwargs.reasoning / reasoning_text / thinking
# - OpenAI Responses API（o 系列 / gpt-5）：content=[{"type":"reasoning","summary":[...],"content":[...]}, {"type":"output_text","text":...}]
# - LangChain 某些版本还会把 reasoning_content 映射为 chunk 顶层属性
# 这里统一做多位置兜底提取，保证各厂商都能取到数据。

def _reasoning_to_text(value) -> str:
    """把不同形态的推理值（str / dict / list 内容块）归一化为纯文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                # OpenAI Responses 推理块：{"type":"summary","text":...} / {"type":"text","text":...}
                # 或嵌套 {"type":"summary","summary":[{"type":"summary_text","text":...}]}
                if isinstance(item.get("text"), str) and item["text"]:
                    out.append(item["text"])
                elif isinstance(item.get("content"), str) and item["content"]:
                    out.append(item["content"])
                elif isinstance(item.get("summary"), (str, list)):
                    out.append(_reasoning_to_text(item["summary"]))
            else:
                out.append(str(item))
        return "".join(out)
    if isinstance(value, dict):
        for key in ("text", "content", "summary"):
            v = value.get(key)
            if isinstance(v, str) and v:
                return v
            if isinstance(v, list) and v:
                return _reasoning_to_text(v)
    return str(value)


def _pick_reasoning(mapping: dict) -> str:
    """在 additional_kwargs / response_metadata 中查找推理内容（含兜底键名）。"""
    if not isinstance(mapping, dict):
        return ""
    for key in ("reasoning_content", "reasoning", "reasoning_text", "thinking"):
        v = mapping.get(key)
        if v:
            text = _reasoning_to_text(v)
            if text:
                return text
    # 兜底：任意包含 reasoning 的键
    for key, v in mapping.items():
        if isinstance(key, str) and "reason" in key.lower() and v:
            text = _reasoning_to_text(v)
            if text:
                return text
    return ""


def extract_reasoning(chunk) -> str:
    """从 LLM 流式 chunk 中提取推理/反思内容，兼容 DeepSeek / Qwen / OpenAI Responses 等格式。"""
    # 1. content 为列表时优先取 reasoning 内容块（OpenAI Responses API）
    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "reasoning"]
        if blocks:
            text = _reasoning_to_text(blocks)
            if text:
                return text
    # 2. additional_kwargs
    text = _pick_reasoning(getattr(chunk, "additional_kwargs", None) or {})
    if text:
        return text
    # 3. response_metadata
    text = _pick_reasoning(getattr(chunk, "response_metadata", None) or {})
    if text:
        return text
    # 4. chunk 顶层属性
    for key in ("reasoning_content", "reasoning", "reasoning_text", "thinking"):
        if hasattr(chunk, key):
            v = getattr(chunk, key)
            if v:
                text = _reasoning_to_text(v)
                if text:
                    return text
    return ""


def chunk_text(chunk) -> str:
    """从 LLM chunk（流式或非流式）中提取可见正文，兼容 str 与 OpenAI Responses 内容块列表。"""
    content = getattr(chunk, "content", None)
    if content is None:
        return str(chunk)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") in ("text", "output_text") and isinstance(block.get("text"), str):
                    out.append(block["text"])
                # reasoning / 其它块不参与正文
            else:
                out.append(str(block))
        return "".join(out)
    return str(content)
