import os, glob, re

corpus = os.path.join(os.path.dirname(__file__), "..", "data", "corpus", "aviation_maintenance", "FAA_AD")
print("top dirs:", sorted(os.listdir(corpus)))
# 统计机型目录与文档数
total = 0
for d in sorted(os.listdir(corpus)):
    p = os.path.join(corpus, d)
    if os.path.isdir(p):
        n = len(glob.glob(os.path.join(p, "*.txt")))
        print(f"  {d}: {n} docs")
        total += n
print("TOTAL AD docs:", total)

# 解析第一篇提取字段样例
sample = glob.glob(os.path.join(corpus, "A320", "*.txt"))[0]
with open(sample, "r", encoding="utf-8", errors="ignore") as f:
    txt = f.read()
print("="*60)
print("SAMPLE:", os.path.basename(sample))
for line in txt.splitlines()[:25]:
    if line.strip():
        print(line[:120])
