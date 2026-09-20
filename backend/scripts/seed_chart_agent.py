# -*- coding: utf-8 -*-
"""一次性配置脚本：图表智能体 + 开源图表 MCP（AntV）注册入库。

两件事全部走系统现有功能（MCP 注册中心 mcp_store + 智能体配置 agents 表），
与页面手工操作等价；幂等可重复执行（已存在则跳过，不覆盖用户修改）：

1. 注册开源图表 MCP 服务器「chart」（@antv/mcp-server-chart，stdio / npx）——
   AntV 官方开源，26 种常用图（柱状/折线/饼图/雷达/漏斗/直方/词云/桑基…）+
   数据表格工具（generate_spreadsheet），返回图片 URL；
2. 创建「图表智能体」（id=agent_chart，is_preset=0，use_tools=1）——多智能体
   协作中任务含图表需求且数据适合成图时产出常用图与表格；自动组队按固定 id
   定位（名称含「图表」兜底），也可在协作页手动勾选。

用法：cd backend && python scripts/seed_chart_agent.py
     cd backend && python scripts/seed_chart_agent.py --update
     （--update：智能体已存在时，用代码内最新人设/描述/工具开关刷新配置）
取消接入 = 页面删除（MCP 注册中心删「chart」/ 配置页删「图表智能体」），
脚本不复活已删除项。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import async_session
from models import Agent
from services.agent_service import (CHART_AGENT_DESCRIPTION, CHART_AGENT_ID,
                                    CHART_AGENT_SYSTEM_PROMPT)
from services.mcp_store import create_server, list_servers

CHART_MCP = {
    "name": "chart",
    "transport": "stdio",
    "command": "npx",
    "args": ["-y", "@antv/mcp-server-chart"],
    "env": {},
    "url": "",
    "description": "AntV 开源图表 MCP（@antv/mcp-server-chart）：柱状/折线/饼图等"
                   "常用图 + 数据表格，返回图片 URL（首次调用 npx 自动拉包）",
    "enabled": 1,
}


async def main() -> None:
    async with async_session() as db:
        # 1) MCP 注册中心：开源图表服务器（与页面「MCP 工具管理」手工注册等价）
        servers = await list_servers(db)
        if any(s.get("name") == CHART_MCP["name"] for s in servers):
            print("[skip] MCP 服务器 chart 已注册")
        else:
            row = await create_server(db, dict(CHART_MCP))
            print(f"[ok] 已注册 MCP 服务器 chart（id={row['id']}）")

        # 2) 智能体配置：图表智能体（与页面「智能体配置」手工创建等价）
        row = await db.get(Agent, CHART_AGENT_ID)
        if row is not None:
            if "--update" in sys.argv:
                row.description = CHART_AGENT_DESCRIPTION
                row.system_prompt = CHART_AGENT_SYSTEM_PROMPT
                row.use_tools = 1
                await db.commit()
                print("[ok] 已刷新图表智能体配置（--update）")
            else:
                print("[skip] 图表智能体已存在（agent_chart），"
                      "加 --update 可用代码内最新人设刷新配置")
        else:
            db.add(Agent(
                id=CHART_AGENT_ID,
                name="图表智能体",
                description=CHART_AGENT_DESCRIPTION,
                system_prompt=CHART_AGENT_SYSTEM_PROMPT,
                kb_id="",
                skill_ids="[]",
                use_tools=1,
                is_preset=0,
                is_enabled=1,
            ))
            await db.commit()
            print("[ok] 已创建图表智能体（agent_chart，use_tools=1）")
    print("done — 协作页勾选「图表智能体」或任务含图表需求即可生成常用图与表格")


if __name__ == "__main__":
    asyncio.run(main())
