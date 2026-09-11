import asyncio, json
import asyncpg

RUN = '8ac82b3c4184'

async def main():
    conn = await asyncpg.connect(host='localhost', port=5432, user='ontology',
                                 password='ontology123', database='knowsource')
    row = await conn.fetchrow("SELECT status, node_states, duration_ms FROM workflow_runs WHERE id=$1", RUN)
    st = row['node_states']
    if isinstance(st, str):
        st = json.loads(st)
    for nid, s in (st or {}).items():
        keep = {k: s.get(k) for k in ('status', 'summary', 'duration_ms', 'task_id', 'started_at') if k in s}
        out = s.get('output') or {}
        if isinstance(out, dict):
            keep['output.decision'] = out.get('decision')
            keep['output.operator'] = out.get('operator')
        print(nid, '->', json.dumps(keep, ensure_ascii=False, default=str))
    print('run status:', row['status'], 'duration:', row['duration_ms'])
    await conn.close()

asyncio.run(main())
