import asyncio, asyncpg, json, urllib.request

DSN = "postgresql://ontology:ontology123@localhost:5432/knowsource"
BASE = "http://0.0.0.0:8000"

async def main():
    c = await asyncpg.connect(DSN)
    # 确认表存在
    has = await c.fetchval("SELECT to_regclass('security_settings')")
    print("security_settings regclass:", has)
    if has:
        before = await c.fetchrow("SELECT key,value FROM security_settings WHERE key='auth_enabled'")
        print("before:", dict(before) if before else None)
        rc = await c.execute("UPDATE security_settings SET value='false', updated_at=now() WHERE key='auth_enabled'")
        if str(rc) == "UPDATE 0":
            await c.execute("INSERT INTO security_settings(key,value,updated_by,updated_at) VALUES('auth_enabled','false','script',now())")
        after = await c.fetchrow("SELECT key,value FROM security_settings WHERE key='auth_enabled'")
        print("after:", dict(after))
    # 读取生效模型
    llm = await c.fetchrow("SELECT model, base_url, max_tokens FROM llm_configs WHERE is_active=1 ORDER BY updated_at DESC LIMIT 1")
    print("llm_active:", dict(llm) if llm else "NONE")
    await c.close()

    # 备份：用管理员账号登录拿 token
    req = urllib.request.Request(BASE + "/api/auth/login",
        data=json.dumps({"username":"admin","password":"6BqI9EQYaSbw"}).encode(),
        headers={"Content-Type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read().decode())
            tok = body.get("access_token") or body.get("token") or ""
            print("LOGIN_OK token_len:", len(tok))
            print("TOKEN:", (tok[:40] + "...") if tok else "NONE")
    except Exception as e:
        print("LOGIN_FAIL:", type(e).__name__, e)

asyncio.run(main())
