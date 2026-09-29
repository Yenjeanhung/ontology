import asyncio, time, httpx
BASE = "http://0.0.0.0:8000"
KB = "a20a9a0a6492"
QUERY = "请简要介绍一下这个知识库的核心内容。"

async def main():
    async with httpx.AsyncClient(timeout=httpx.Timeout(180)) as c:
        t0 = time.perf_counter()
        async with c.stream("POST", f"{BASE}/api/query",
                            json={"query": QUERY, "kb_id": KB}) as r:
            print("status:", r.status_code, "headers:", dict(r.headers).get("content-type"))
            first = True
            n = 0
            async for chunk in r.aiter_text():
                if first:
                    print("T_first_byte: %.2fs" % (time.perf_counter() - t0))
                    print("RAW first 600:", repr(chunk[:600]))
                    first = False
                n += 1
                if n > 40:
                    print("... (truncated after 40 chunks)"); break
            print("T_total: %.2fs" % (time.perf_counter() - t0))

asyncio.run(main())
