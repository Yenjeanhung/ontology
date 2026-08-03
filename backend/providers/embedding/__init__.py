"""嵌入模型工厂（LangChain 封装）。"""

from config import settings

_embeddings = None


def create_embeddings():
    """创建/获取嵌入模型单例。"""
    global _embeddings
    if _embeddings is not None:
        return _embeddings

    if settings.EMBEDDING_PROVIDER == "local":
        from langchain_huggingface import HuggingFaceEmbeddings
        _embeddings = HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)
    elif settings.EMBEDDING_PROVIDER == "openai":
        from langchain_openai import OpenAIEmbeddings
        _embeddings = OpenAIEmbeddings(
            model=settings.OPENAI_EMBEDDING_MODEL,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
        )
    else:
        raise ValueError(f"未知的嵌入模型 Provider: {settings.EMBEDDING_PROVIDER}")

    return _embeddings
