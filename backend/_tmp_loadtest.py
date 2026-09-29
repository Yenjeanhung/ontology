import asyncio, time, json, sqlite3
import httpx

BASE = "http://0.0.0.0:8000"
QUERY = "请简要介绍一下这个知识库的核心内容，并给出要点。"
LEVELS = [1, 5, 10, 20]
WINDOW = 15  # 每级秒数
OTEL = r"D:\myWorkspace\AI_project\ontology\backend\data\otel_traces.db"


async def pick_kb(client):
    r = await client.get(f"{BASE}/api/kb", timeout=20)
    r.raise_for_status()
    kbs = r.json()
    kbs = [k for k in kbs if isinstance(k, dict)]
    if not kbs:
        raise SystemExit("no kb")
    best = max(kbs, key=lambda k: int(k.get("chunk_count") or 0))
    print(f"使用 KB: {best.get('id')} chunk_count={best.get('chunk_count')} name={best.get('name')}")
    return best["id"]


async def one_query(client, kb_id, stats):
    start = time.perf_counter()
    ttft = None
    ok = False
    try:
        async with client.stream("POST", f"{BASE}/api/query",
                                 json={"query": QUERY, "kb_id": kb_id},
                                 timeout=httpx.Timeout(180)) as resp:
            if resp.status_code != 200:
                stats["errors"] += 1
                return
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    obj = json.loads(payload)
                except Exception:
                    continue
                text = obj.get("content") or obj.get("answer") or obj.get("text") or ""
                if isinstance(text, str) and text.strip():
                    if ttft is None:
                        ttft = time.perf_counter() - start
                    ok = True
                if obj.get("type") == "done" or obj.get("done"):
                    break
    except Exception:
        stats["errors"] += 1
        return
    dur = time.perf_counter() - start
    if ok:
        stats["done"].append((ttft, dur))


async def run_level(conc, kb_id):
    limits = httpx.Limits(max_connections=conc + 10)
    async with httpx.AsyncClient(timeout=httpx.Timeout(180), limits=limits) as client:
        stats = {"done": [], "errors": 0}
        stop = asyncio.Event()

        async def worker():
            while not stop.is_set():
                await one_query(client, kb_id, stats)

        tasks = [asyncio.create_task(worker()) for _ in range(conc)]
        await asyncio.sleep(WINDOW)
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    n = len(stats["done"])
    ttfts = [x[0] for x in stats["done"] if x[0] is not None]
    durs = [x[1] for x in stats["done"]]
    def pct(xs, p):
        if not xs: return 0.0
        xs = sorted(xs); k = (len(xs) - 1) * p; f = int(k)
        return xs[f] if f + 1 >= len(xs) else xs[f] + (xs[f+1] - xs[f]) * (k - f)
    return {
        "concurrency": conc,
        "completed": n,
        "errors": stats["errors"],
        "answers_per_sec": round(n / WINDOW, 2),
        "ttft_p50": round(pct(ttfts, 0.5), 2),
        "ttft_p95": round(pct(ttfts, 0.95), 2),
        "latency_p50": round(pct(durs, 0.5), 2),
        "latency_p95": round(pct(durs, 0.95), 2),
    }


def otel_token_stats():
    try:
        c = sqlite3.connect(OTEL)
        rows = c.execute(
            "SELECT attributes FROM otel_spans WHERE name='llm_generate' "
            "AND start_ns > ?", (int(time.time() * 1e9) - 20 * 60 * 1e9,)
        ).fetchall()
        tin = []; tout = []
        for (attr,) in rows:
            try:
                a = json.loads(attr)
            except Exception:
                continue
            i = a.get("llm.input_tokens"); o = a.get("llm.output_tokens")
            if i: tin.append(int(i))
            if o: tout.append(int(o))
        c.close()
        if not tin:
            return None
        return {
            "llm_calls": len(tin),
            "avg_input_tokens": round(sum(tin) / len(tin)),
            "avg_output_tokens": round(sum(tout) / len(tout)) if tout else 0,
            "avg_total_tokens": round((sum(tin) + sum(tout)) / len(tin)),
        }
    except Exception as e:
        return {"error": str(e)}


async def main():
    async with httpx.AsyncClient(timeout=httpx.Timeout(180)) as client:
        kb_id = await pick_kb(client)
    print(f"\n{'conc':>4} {'完成':>5} {'错误':>4} {'回答/秒':>8} {'TTFT_p50':>9} {'TTFT_p95':>9} {'延迟_p50':>9} {'延迟_p95':>9}")
    results = []
    for conc in LEVELS:
        r = await run_level(conc, kb_id)
        results.append(r)
        print(f"{r['concurrency']:>4} {r['completed']:>5} {r['errors']:>4} {r['answers_per_sec']:>8} "
              f"{r['ttft_p50']:>9} {r['ttft_p95']:>9} {r['latency_p50']:>9} {r['latency_p95']:>9}")
    print("\n=== OTel 真实 token 统计（最近 20 分钟 llm_generate）===")
    print(json.dumps(otel_token_stats(), ensure_ascii=False, indent=2))


asyncio.run(main())
