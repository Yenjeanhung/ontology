"""LLM 工厂（LangChain 封装）。"""

from config import settings

_llm = None


def create_llm():
    """创建/获取 LLM 单例。"""
    global _llm
    if _llm is not None:
        return _llm

    if settings.LLM_PROVIDER == "openai":
        from langchain_openai import ChatOpenAI
        _llm = ChatOpenAI(
            model=settings.LLM_MODEL,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            max_tokens=settings.LLM_MAX_TOKENS,
            temperature=settings.LLM_TEMPERATURE,
        )
    else:
        raise ValueError(f"未知的 LLM Provider: {settings.LLM_PROVIDER}")

    return _llm
