import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
from sentence_transformers import SentenceTransformer
m = SentenceTransformer("BAAI/bge-small-zh-v1.5")
v = m.encode(["飞机发动机EGT超限故障"])
print("dim:", v.shape, "model loaded OK")
