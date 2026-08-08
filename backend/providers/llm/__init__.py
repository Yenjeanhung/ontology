"""LLM 工厂（LangChain 封装）。

- provider=openai：OpenAI 兼容协议，覆盖 OpenAI 官方 / DeepSeek / 通义千问 / 智谱 / 自定义 OpenAI 格式。
- provider=anthropic：Anthropic 官方协议（需安装 langchain-anthropic）。
"""

from config import settings

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
