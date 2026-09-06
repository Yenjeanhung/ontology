import sys
sys.path.insert(0, '.')
import asyncio
from services.service_runtime import execute_service

async def main():
    r = await execute_service(
        code_text="def run(params, entity, context):\n    return {'hello': 'world'}\n",
        language="python",
        params={"raw_topic": "比亚迪"},
        entity={},
        context={"start": {"topic": "比亚迪", "reasoning": ""}},
        timeout_seconds=30,
    )
    print(r)

asyncio.run(main())
