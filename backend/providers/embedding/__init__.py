"""嵌入模型工厂（LangChain 封装）。"""

import logging
import os

from config import settings

logger = logging.getLogger(__name__)

_embeddings = None


def create_embeddings():
    """创建/获取嵌入模型单例。"""
    global _embeddings
    if _embeddings is not None:
        return _embeddings

    if settings.EMBEDDING_PROVIDER == "local":
        from langchain_huggingface import HuggingFaceEmbeddings
        kwargs = {"model_name": settings.EMBEDDING_MODEL}
        if settings.HF_CACHE_DIR:
            kwargs["cache_folder"] = settings.HF_CACHE_DIR
            os.environ["HF_HUB_OFFLINE"] = "1"
            logger.info(f"加载本地嵌入模型: {settings.EMBEDDING_MODEL}, 缓存目录: {settings.HF_CACHE_DIR} (离线模式)")
        else:
            logger.info(f"加载本地嵌入模型: {settings.EMBEDDING_MODEL} (在线下载模式)")
        _embeddings = HuggingFaceEmbeddings(**kwargs)
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
