"""Seed 民航维修领域本体到 KnowSource 本体管理系统。

用法：
    cd backend
    python scripts/seed_aviation_ontology.py

如需强制重新生成（删除已存在的同名类别及全部从属数据）：
    python scripts/seed_aviation_ontology.py --force

注意：
- 本脚本直接操作 SQLite（由 .env 中 DATABASE_URL 指定），不会启动 HTTP 服务。
- 首次运行会自动执行 database.init_db() 建表。
- 属性类型使用系统支持的 string/text/number/boolean/date/datetime；
  枚举类字段使用 string 并在描述中给出允许取值。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

# 允许从 backend/scripts 目录直接运行
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlalchemy as sa
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session, engine, init_db
from models import (
    Ontology,
    OntologyAttribute,
    OntologyAttributeTemplate,
    OntologyCategory,
    OntologyRelation,
    OntologyRelationConstraint,
    OntologyTemplateAttribute,
    OntologyTemplateBinding,
)


# ───────────────────────────── 数据定义 ─────────────────────────────

CATEGORY_NAME = "民航维修领域本体"
CATEGORY_DESC = (
    "面向航空公司机务维修、故障根因分析、适航符合性与 MEL 保留管理的领域本体。"
    "以 ATA 章节体系为骨架，IPC 部件树与 FIM 故障树双树合一。"
)

COLORS = {
    "航空器结构类": "#5470c6",
    "故障类": "#ee6666",
    "维修类": "#91cc75",
    "适航文件类": "#fac858",
}

ONTOLOGIES: list[dict] = [
    # 航空器结构类
    {"name": "机型", "code": "AircraftType", "group": "航空器结构类",
     "description": "机型 / 系列，如 B737-800、A320-214、A330-343"},
    {"name": "航空器", "code": "Aircraft", "group": "航空器结构类",
     "description": "具体一架飞机，以注册号（如 B-1234）与 MSN 唯一标识"},
    {"name": "系统", "code": "System", "group": "航空器结构类",
     "description": "ATA 章节系统，如 21 空调、24 电源、29 液压、32 起落架"},
    {"name": "部件", "code": "Component", "group": "航空器结构类",
     "description": "LRU / SRU / 时寿件，以件号 PN 唯一标识"},
    {"name": "监控参数", "code": "Parameter", "group": "航空器结构类",
     "description": "（可选）可监控的运行参数，如 EGT、N1、N2、VIB"},
    # 故障类
    {"name": "故障代码", "code": "FaultCode", "group": "故障类",
     "description": "CMS/ECAM/EICAS 报文码，如 29-11341"},
    {"name": "故障模式", "code": "FailureMode", "group": "故障类",
     "description": "现象层，可观测的异常表现"},
    {"name": "故障原因", "code": "RootCause", "group": "故障类",
     "description": "根因层，机理层面的故障原因"},
    {"name": "故障征兆", "code": "Symptom", "group": "故障类",
     "description": "（可选）早期信号 / 趋势前兆"},
    {"name": "工况", "code": "Condition", "group": "故障类",
     "description": "运行条件 / 诱因，如飞行阶段、气象、高度、负载"},
    # 维修类
    {"name": "维修措施", "code": "Action", "group": "维修类",
     "description": "处置手段：更换 LRU、修复、调节、清洁、测试、保留、监控、改装"},
    {"name": "航材", "code": "SparePart", "group": "维修类",
     "description": "备件 / 消耗件 / 周转件，以件号 PN 唯一标识"},
    {"name": "工装工具", "code": "Tool", "group": "维修类",
     "description": "（可选）专用工具 / 测试设备"},
    {"name": "维修工单", "code": "WorkOrder", "group": "维修类",
     "description": "一次排故事件，对应 FLB/TLB 或 NRC 非例行工单"},
    {"name": "MEL保留", "code": "MELDeferral", "group": "维修类",
     "description": "MEL/CDL 保留项，含 A/B/C/D 类别与生命周期"},
    # 适航文件类
    {"name": "适航文件", "code": "AirworthinessDoc", "group": "适航文件类",
     "description": "AD / SB / SIL / EO / 服务信函"},
    {"name": "手册", "code": "Manual", "group": "适航文件类",
     "description": "AMM / FIM / TSM / IPC / SSM / SWPM / MEL / CDL 章节"},
    {"name": "厂商", "code": "Vendor", "group": "适航文件类",
     "description": "OEM / 部件制造商 / MRO / 航材供应商 / 租赁商"},
    {"name": "人员", "code": "Personnel", "group": "适航文件类",
     "description": "机务 / 工程师 / 放行人员"},
    {"name": "维修站", "code": "Station", "group": "适航文件类",
     "description": "维修站点：主基地 / 分公司基地 / 航站 / 外站"},
]


ATTRIBUTES: dict[str, list[dict]] = {
    "机型": [
        {"name": "机型代码", "code": "type_code", "data_type": "string", "is_required": True,
         "description": "如 B737-800 / A320-214 / A330-343"},
        {"name": "制造商", "code": "manufacturer", "data_type": "string",
         "description": "BOEING / AIRBUS / COMAC"},
        {"name": "发动机型号", "code": "engine_model", "data_type": "string"},
        {"name": "机队规模", "code": "fleet_size", "data_type": "number"},
        {"name": "ATA适用性", "code": "ata_applicability", "data_type": "text"},
    ],
    "航空器": [
        {"name": "注册号", "code": "registration", "data_type": "string", "is_required": True,
         "description": "如 B-1234"},
        {"name": "MSN", "code": "msn", "data_type": "string", "is_required": True,
         "description": "Manufacturer Serial Number"},
        {"name": "所属机型", "code": "aircraft_type", "data_type": "string", "is_required": True},
        {"name": "引进日期", "code": "entry_date", "data_type": "date"},
        {"name": "飞行小时", "code": "flight_hours", "data_type": "number"},
        {"name": "飞行循环", "code": "flight_cycles", "data_type": "number"},
        {"name": "当前状态", "code": "status", "data_type": "string",
         "description": "在役 / 停场 / 定检 / 退役 / 租出"},
    ],
    "系统": [
        {"name": "ATA章节号", "code": "ata_chapter", "data_type": "string", "is_required": True,
         "description": "如 29"},
        {"name": "系统名称", "code": "system_name", "data_type": "string", "is_required": True,
         "description": "如 液压"},
        {"name": "英文缩写", "code": "abbreviation", "data_type": "string",
         "description": "如 HYD / ELEC / APU"},
        {"name": "上级章节", "code": "parent_chapter", "data_type": "string"},
    ],
    "部件": [
        {"name": "件号PN", "code": "part_number", "data_type": "string", "is_required": True},
        {"name": "名称", "code": "name", "data_type": "string", "is_required": True},
        {"name": "ATA章节", "code": "ata_chapter", "data_type": "string"},
        {"name": "制造商", "code": "manufacturer", "data_type": "string"},
        {"name": "类型", "code": "component_type", "data_type": "string",
         "description": "LRU / SRU / 消耗件 / 时寿件"},
        {"name": "MTBUR目标值", "code": "mtbur_target", "data_type": "number",
         "description": "非计划拆换平均间隔目标值（飞行小时）"},
        {"name": "是否时寿件", "code": "is_life_limited", "data_type": "boolean"},
        {"name": "寿命限制", "code": "life_limit", "data_type": "string"},
        {"name": "装机位置", "code": "installed_position", "data_type": "string"},
    ],
    "监控参数": [
        {"name": "参数代码", "code": "param_code", "data_type": "string", "is_required": True,
         "description": "如 EGT / N1 / N2 / VIB / FF"},
        {"name": "名称", "code": "name", "data_type": "string"},
        {"name": "单位", "code": "unit", "data_type": "string"},
        {"name": "正常范围", "code": "normal_range", "data_type": "string"},
        {"name": "采集来源", "code": "source", "data_type": "string",
         "description": "ACARS / QAR / CMS / 人工记录"},
    ],
    "故障代码": [
        {"name": "代码", "code": "fault_code", "data_type": "string", "is_required": True,
         "description": "如 29-11341"},
        {"name": "报文文本", "code": "message", "data_type": "text"},
        {"name": "ATA章节", "code": "ata_chapter", "data_type": "string"},
        {"name": "等级", "code": "level", "data_type": "string",
         "description": "WARNING / CAUTION / ADVISORY / STATUS"},
        {"name": "触发阶段", "code": "trigger_phase", "data_type": "string",
         "description": "启动 / 滑行 / 起飞 / 爬升 / 巡航 / 下降 / 进近 / 着陆 / 地面 / 多阶段"},
    ],
    "故障模式": [
        {"name": "名称", "code": "name", "data_type": "string", "is_required": True},
        {"name": "现象描述", "code": "description", "data_type": "text"},
        {"name": "ATA章节", "code": "ata_chapter", "data_type": "string"},
        {"name": "是否影响签派", "code": "affects_dispatch", "data_type": "boolean"},
        {"name": "是否可MEL保留", "code": "mel_deferrable", "data_type": "boolean"},
        {"name": "故障等级", "code": "fault_class", "data_type": "string",
         "description": "A / B / C / D"},
    ],
    "故障原因": [
        {"name": "名称", "code": "name", "data_type": "string", "is_required": True},
        {"name": "机理描述", "code": "mechanism", "data_type": "text"},
        {"name": "ATA章节", "code": "ata_chapter", "data_type": "string"},
        {"name": "原因分类", "code": "cause_category", "data_type": "string",
         "description": "设计 / 制造 / 安装 / 操作 / 老化磨损 / 维护不当 / 外来物FOD / 软件逻辑 / 外部环境 / 不明"},
    ],
    "故障征兆": [
        {"name": "名称", "code": "name", "data_type": "string", "is_required": True},
        {"name": "观测方式", "code": "observation", "data_type": "string"},
        {"name": "提前量", "code": "lead_time", "data_type": "string"},
        {"name": "可检测性", "code": "detectability", "data_type": "string",
         "description": "高 / 中 / 低"},
    ],
    "工况": [
        {"name": "名称", "code": "name", "data_type": "string", "is_required": True},
        {"name": "类型", "code": "condition_type", "data_type": "string",
         "description": "飞行阶段 / 气象 / 机场 / 高度 / 温度 / 负载 / 震动"},
        {"name": "取值描述", "code": "value", "data_type": "string"},
    ],
    "维修措施": [
        {"name": "名称", "code": "name", "data_type": "string", "is_required": True},
        {"name": "类型", "code": "action_type", "data_type": "string",
         "description": "更换LRU / 修复 / 调节 / 清洁 / 润滑 / 测试 / 复位 / 保留 / 监控 / 改装"},
        {"name": "步骤描述", "code": "procedure", "data_type": "text"},
        {"name": "标准工时", "code": "std_hours", "data_type": "number"},
        {"name": "是否需停场", "code": "requires_shutdown", "data_type": "boolean"},
        {"name": "所需执照类别", "code": "license_required", "data_type": "string"},
        {"name": "是否根治", "code": "is_permanent_fix", "data_type": "boolean"},
    ],
    "航材": [
        {"name": "件号PN", "code": "part_number", "data_type": "string", "is_required": True},
        {"name": "名称", "code": "name", "data_type": "string", "is_required": True},
        {"name": "ATA", "code": "ata_chapter", "data_type": "string"},
        {"name": "类型", "code": "spare_type", "data_type": "string",
         "description": "周转件 / 消耗件 / 时寿件 / 标准件"},
        {"name": "库存量", "code": "stock", "data_type": "number"},
        {"name": "库存站点", "code": "stock_station", "data_type": "string"},
        {"name": "互换性代码", "code": "interchange_code", "data_type": "string",
         "description": "INTERCHANGEABLE / REPLACEABLE / ONE-WAY / 不可互换"},
        {"name": "是否AOG常备", "code": "aog_stock", "data_type": "boolean"},
        {"name": "采购交期", "code": "lead_time_days", "data_type": "number",
         "description": "单位：天"},
    ],
    "工装工具": [
        {"name": "名称", "code": "name", "data_type": "string", "is_required": True},
        {"name": "件号", "code": "part_number", "data_type": "string"},
        {"name": "是否专用", "code": "is_specialized", "data_type": "boolean"},
        {"name": "校验有效期", "code": "calibration_valid_until", "data_type": "date"},
    ],
    "维修工单": [
        {"name": "工单号", "code": "workorder_no", "data_type": "string", "is_required": True},
        {"name": "NRC号", "code": "nrc_no", "data_type": "string"},
        {"name": "开单时间", "code": "opened_at", "data_type": "datetime"},
        {"name": "关闭时间", "code": "closed_at", "data_type": "datetime"},
        {"name": "ATA", "code": "ata_chapter", "data_type": "string"},
        {"name": "处理结果", "code": "result", "data_type": "string",
         "description": "已修复 / 保留 / NFF无故障发现 / 未再现 / 监控中 / 未解决"},
        {"name": "是否重复性故障", "code": "is_repeat", "data_type": "boolean"},
        {"name": "验证方式", "code": "verification", "data_type": "string",
         "description": "地面测试 / 试车 / 试飞 / 目视检查 / 无"},
        {"name": "停场时长", "code": "downtime_hours", "data_type": "number",
         "description": "单位：小时"},
        {"name": "维修时长", "code": "repair_hours", "data_type": "number",
         "description": "单位：小时"},
        {"name": "发生时飞行小时", "code": "flight_hours", "data_type": "number"},
        {"name": "发生时飞行循环", "code": "flight_cycles", "data_type": "number"},
    ],
    "MEL保留": [
        {"name": "保留号", "code": "deferral_no", "data_type": "string", "is_required": True},
        {"name": "MEL项目号", "code": "mel_item_no", "data_type": "string", "is_required": True},
        {"name": "类别", "code": "category", "data_type": "string",
         "description": "A / B / C / D"},
        {"name": "开保留日期", "code": "opened_at", "data_type": "date"},
        {"name": "到期日", "code": "due_date", "data_type": "date"},
        {"name": "批准人", "code": "approved_by", "data_type": "string"},
        {"name": "状态", "code": "status", "data_type": "string",
         "description": "有效 / 已关闭 / 超期"},
        {"name": "保留原因", "code": "reason", "data_type": "text"},
    ],
    "适航文件": [
        {"name": "文件编号", "code": "doc_no", "data_type": "string", "is_required": True,
         "description": "如 AD 2024-08-15 / SB 737-29-1123"},
        {"name": "类型", "code": "doc_type", "data_type": "string",
         "description": "AD / SB / SIL / EO / 服务信函"},
        {"name": "标题", "code": "title", "data_type": "text"},
        {"name": "发布局方", "code": "issuer", "data_type": "string",
         "description": "CAAC / FAA / EASA / OEM"},
        {"name": "生效日期", "code": "effective_date", "data_type": "date"},
        {"name": "是否重复执行", "code": "is_repetitive", "data_type": "boolean"},
        {"name": "重复执行间隔", "code": "repetitive_interval", "data_type": "string"},
        {"name": "是否有终止措施", "code": "has_terminating_action", "data_type": "boolean"},
        {"name": "适用性", "code": "applicability", "data_type": "text",
         "description": "机型 / MSN 范围等"},
    ],
    "手册": [
        {"name": "手册代码", "code": "manual_code", "data_type": "string",
         "description": "AMM / FIM / TSM / IPC / SSM / SWPM / MEL / CDL"},
        {"name": "章节号", "code": "chapter_no", "data_type": "string",
         "description": "如 29-21-00"},
        {"name": "标题", "code": "title", "data_type": "string"},
        {"name": "版本", "code": "version", "data_type": "string"},
        {"name": "有效性", "code": "effectivity", "data_type": "string"},
        {"name": "页码", "code": "page", "data_type": "string"},
    ],
    "厂商": [
        {"name": "名称", "code": "name", "data_type": "string", "is_required": True},
        {"name": "类型", "code": "vendor_type", "data_type": "string",
         "description": "OEM / 部件制造商 / MRO / 航材供应商 / 租赁商"},
        {"name": "联系人", "code": "contact", "data_type": "string"},
    ],
    "人员": [
        {"name": "工号", "code": "employee_no", "data_type": "string", "is_required": True},
        {"name": "姓名", "code": "name", "data_type": "string", "is_required": True},
        {"name": "执照类别", "code": "license_type", "data_type": "string",
         "description": "CCAR-66：TA / PA / TR / PR"},
        {"name": "机型签署", "code": "type_ratings", "data_type": "string"},
        {"name": "技能等级", "code": "skill_level", "data_type": "string",
         "description": "机械员 / 技术员 / 工程师 / 放行工程师 / 专家"},
    ],
    "维修站": [
        {"name": "三字码", "code": "iata_code", "data_type": "string", "is_required": True,
         "description": "如 PVG / CTU / CAN"},
        {"name": "名称", "code": "name", "data_type": "string"},
        {"name": "类型", "code": "station_type", "data_type": "string",
         "description": "主基地 / 分公司基地 / 航站 / 外站"},
        {"name": "维修能力", "code": "capabilities", "data_type": "text"},
        {"name": "是否具备AOG支援能力", "code": "aog_capable", "data_type": "boolean"},
    ],
}


RELATIONS: list[dict] = [
    # 结构类
    {"name": "属于机型", "description": "航空器 → 机型"},
    {"name": "包含系统", "description": "机型 → 系统"},
    {"name": "组成", "description": "部件 → 系统 / 部件 → 部件"},
    {"name": "装于", "description": "部件 → 机型（v1 装于机型而非单机，压缩图规模）"},
    {"name": "监测于", "description": "部件 → 监控参数"},
    {"name": "受控于", "description": "部件 → 适航文件（AD/SB 适用性）"},
    # 故障因果类
    {"name": "表现为", "description": "故障原因 → 故障模式"},
    {"name": "征兆为", "description": "故障原因 → 故障征兆"},
    {"name": "导致", "description": "故障原因 → 故障原因；工况 → 故障原因"},
    {"name": "触发于", "description": "故障模式 → 工况"},
    {"name": "上报代码", "description": "故障模式 → 故障代码"},
    {"name": "发生于", "description": "故障模式/原因 → 部件（双树缝合点）"},
    {"name": "归属系统", "description": "故障模式/原因 → 系统"},
    # 维修类
    {"name": "适用于", "description": "维修措施 → 故障原因；航材 → 部件"},
    {"name": "需要", "description": "维修措施 → 航材 / 工装工具"},
    {"name": "互换", "description": "航材 → 航材（民航互换性）"},
    {"name": "供应自", "description": "部件/航材 → 厂商"},
    {"name": "引用", "description": "维修措施 → 手册"},
    {"name": "依据", "description": "维修措施 → 适航文件"},
    {"name": "覆盖", "description": "手册 → 系统 / 部件"},
    {"name": "对应故障", "description": "手册 → 故障代码"},
    {"name": "隔离步骤", "description": "手册 → 维修措施"},
    # 工单与保留类
    {"name": "涉及故障", "description": "维修工单 → 故障模式"},
    {"name": "采取措施", "description": "维修工单 → 维修措施"},
    {"name": "涉及部件", "description": "维修工单 → 部件"},
    {"name": "发生于航空器", "description": "维修工单 → 航空器"},
    {"name": "处理于", "description": "维修工单 → 人员"},
    {"name": "执行于", "description": "维修工单 → 维修站"},
    {"name": "产生保留", "description": "维修工单 → MEL保留"},
    {"name": "依据保留", "description": "MEL保留 → 手册"},
    {"name": "保留涉及", "description": "MEL保留 → 部件"},
    # 图计算产出
    {"name": "相似于", "description": "图计算产出：故障模式/原因 相似"},
    {"name": "潜在故障", "description": "图计算产出：部件 → 故障模式（需人工审核）"},
]


CONSTRAINTS: list[tuple[str, str, str]] = [
    # 结构类
    ("航空器", "属于机型", "机型"),
    ("机型", "包含系统", "系统"),
    ("部件", "组成", "系统"),
    ("部件", "组成", "部件"),
    ("部件", "装于", "机型"),
    ("部件", "监测于", "监控参数"),
    ("部件", "受控于", "适航文件"),
    # 故障因果类
    ("故障原因", "表现为", "故障模式"),
    ("故障原因", "征兆为", "故障征兆"),
    ("故障原因", "导致", "故障原因"),
    ("工况", "导致", "故障原因"),
    ("故障模式", "触发于", "工况"),
    ("故障模式", "上报代码", "故障代码"),
    ("故障模式", "发生于", "部件"),
    ("故障原因", "发生于", "部件"),
    ("故障模式", "归属系统", "系统"),
    ("故障原因", "归属系统", "系统"),
    # 维修类
    ("维修措施", "适用于", "故障原因"),
    ("维修措施", "需要", "航材"),
    ("维修措施", "需要", "工装工具"),
    ("维修措施", "引用", "手册"),
    ("维修措施", "依据", "适航文件"),
    ("航材", "互换", "航材"),
    ("航材", "适用于", "部件"),
    ("部件", "供应自", "厂商"),
    ("航材", "供应自", "厂商"),
    ("手册", "覆盖", "系统"),
    ("手册", "覆盖", "部件"),
    ("手册", "对应故障", "故障代码"),
    ("手册", "隔离步骤", "维修措施"),
    # 工单与保留类
    ("维修工单", "涉及故障", "故障模式"),
    ("维修工单", "采取措施", "维修措施"),
    ("维修工单", "涉及部件", "部件"),
    ("维修工单", "发生于航空器", "航空器"),
    ("维修工单", "处理于", "人员"),
    ("维修工单", "执行于", "维修站"),
    ("维修工单", "产生保留", "MEL保留"),
    ("MEL保留", "依据保留", "手册"),
    ("MEL保留", "保留涉及", "部件"),
    # 图计算产出
    ("故障模式", "相似于", "故障模式"),
    ("故障原因", "相似于", "故障原因"),
    ("部件", "潜在故障", "故障模式"),
]


ATTRIBUTE_TEMPLATES: list[dict] = [
    {
        "name": "ATA分类属性",
        "description": "跨本体复用的 ATA 章节分类信息",
        "attributes": [
            {"name": "ATA章节", "code": "ata_chapter", "data_type": "string", "is_required": False,
             "description": "ATA 章节号，如 29"},
            {"name": "系统名称", "code": "system_name", "data_type": "string"},
            {"name": "英文缩写", "code": "abbreviation", "data_type": "string"},
        ],
    },
    {
        "name": "件号基础属性",
        "description": "件号级实体的通用属性（部件、航材、工装工具）",
        "attributes": [
            {"name": "件号PN", "code": "part_number", "data_type": "string", "is_required": True},
            {"name": "名称", "code": "name", "data_type": "string", "is_required": True},
            {"name": "制造商", "code": "manufacturer", "data_type": "string"},
            {"name": "ATA", "code": "ata_chapter", "data_type": "string"},
        ],
    },
    {
        "name": "时间区间属性",
        "description": "含起止时间与时长的通用属性",
        "attributes": [
            {"name": "开始时间", "code": "start_time", "data_type": "datetime"},
            {"name": "结束时间", "code": "end_time", "data_type": "datetime"},
            {"name": "时长", "code": "duration", "data_type": "number", "description": "单位：小时"},
        ],
    },
    {
        "name": "时寿属性",
        "description": "时寿件通用属性（部件、航材）",
        "attributes": [
            {"name": "是否时寿件", "code": "is_life_limited", "data_type": "boolean"},
            {"name": "寿命限制", "code": "life_limit", "data_type": "string"},
            {"name": "已用寿命", "code": "used_life", "data_type": "number"},
        ],
    },
]

# 模板绑定规则：{本体名: [模板名列表]}
TEMPLATE_BINDINGS: dict[str, list[str]] = {
    "系统": ["ATA分类属性"],
    "部件": ["ATA分类属性", "件号基础属性", "时寿属性"],
    "故障代码": ["ATA分类属性"],
    "故障模式": ["ATA分类属性"],
    "故障原因": ["ATA分类属性"],
    "航材": ["件号基础属性", "时寿属性"],
    "工装工具": ["件号基础属性"],
    "维修工单": ["ATA分类属性", "时间区间属性"],
    "MEL保留": ["时间区间属性"],
    "手册": ["ATA分类属性"],
}


# ───────────────────────────── 核心逻辑 ─────────────────────────────

async def seed(force: bool = False) -> None:
    await init_db()

    async with async_session() as db:
        existing = await db.execute(
            select(OntologyCategory).where(OntologyCategory.name == CATEGORY_NAME)
        )
        existing_cat = existing.scalar_one_or_none()

        if existing_cat and not force:
            print(f"本体类别「{CATEGORY_NAME}」已存在（id={existing_cat.id}）。")
            print("如需重新生成，请使用 --force 参数。")
            return

        if existing_cat and force:
            cat_id = existing_cat.id
            print(f"强制重新生成：删除类别 {cat_id} 及其从属数据...")
            await _clear_category(db, cat_id)
            await db.commit()

        # 1. 创建类别
        category = OntologyCategory(
            name=CATEGORY_NAME,
            description=CATEGORY_DESC,
        )
        db.add(category)
        await db.flush()
        await db.refresh(category)
        cat_id = category.id
        print(f"已创建本体类别：{CATEGORY_NAME}（id={cat_id}）")

        # 2. 创建属性模板（全局，不归属类别；先建模板便于后续绑定）
        template_id_by_name: dict[str, str] = {}
        for tdef in ATTRIBUTE_TEMPLATES:
            # 注意：Result 只能消费一次，必须先取出再判断
            existing_tpl = (await db.execute(
                select(OntologyAttributeTemplate).where(OntologyAttributeTemplate.name == tdef["name"])
            )).scalar_one_or_none()

            if existing_tpl and not force:
                print(f"  属性模板「{tdef['name']}」已存在，跳过。")
                template_id_by_name[tdef["name"]] = existing_tpl.id
                continue

            if existing_tpl and force:
                # 同时清理已引用该模板的绑定，避免留下悬空引用
                await db.execute(
                    delete(OntologyTemplateBinding).where(
                        OntologyTemplateBinding.template_id == existing_tpl.id
                    )
                )
                await db.execute(
                    delete(OntologyTemplateAttribute).where(
                        OntologyTemplateAttribute.template_id == existing_tpl.id
                    )
                )
                await db.execute(
                    delete(OntologyAttributeTemplate).where(
                        OntologyAttributeTemplate.id == existing_tpl.id
                    )
                )
                print(f"  已删除旧属性模板：{tdef['name']}")
            template = OntologyAttributeTemplate(
                name=tdef["name"],
                description=tdef["description"],
            )
            db.add(template)
            await db.flush()
            await db.refresh(template)
            template_id_by_name[tdef["name"]] = template.id
            for idx, adef in enumerate(tdef["attributes"]):
                db.add(OntologyTemplateAttribute(
                    template_id=template.id,
                    name=adef["name"],
                    code=adef.get("code"),
                    data_type=adef["data_type"],
                    description=adef.get("description", ""),
                    is_required=int(adef.get("is_required", False)),
                    default_value=adef.get("default_value"),
                    sort_order=idx,
                ))
            print(f"  已创建属性模板：{tdef['name']}（{len(tdef['attributes'])} 个属性）")

        # 3. 创建本体
        ontology_id_by_name: dict[str, str] = {}
        sort_base = {"航空器结构类": 100, "故障类": 200, "维修类": 300, "适航文件类": 400}
        for idx, odef in enumerate(ONTOLOGIES):
            sort_order = sort_base[odef["group"]] + idx % 100
            ontology = Ontology(
                category_id=cat_id,
                name=odef["name"],
                description=odef["description"],
                color=COLORS[odef["group"]],
                sort_order=sort_order,
            )
            db.add(ontology)
            await db.flush()
            await db.refresh(ontology)
            ontology_id_by_name[odef["name"]] = ontology.id
        print(f"已创建 {len(ONTOLOGIES)} 个本体")

        # 4. 创建属性
        attr_count = 0
        for ont_name, attrs in ATTRIBUTES.items():
            ont_id = ontology_id_by_name[ont_name]
            for idx, adef in enumerate(attrs):
                db.add(OntologyAttribute(
                    ontology_id=ont_id,
                    name=adef["name"],
                    code=adef.get("code"),
                    data_type=adef["data_type"],
                    description=adef.get("description", ""),
                    is_required=int(adef.get("is_required", False)),
                    default_value=adef.get("default_value"),
                    sort_order=idx,
                ))
                attr_count += 1
        print(f"已创建 {attr_count} 个本体属性")

        # 5. 绑定属性模板
        binding_count = 0
        for ont_name, template_names in TEMPLATE_BINDINGS.items():
            ont_id = ontology_id_by_name.get(ont_name)
            if not ont_id:
                continue
            for tname in template_names:
                tid = template_id_by_name.get(tname)
                if not tid:
                    continue
                db.add(OntologyTemplateBinding(
                    ontology_id=ont_id,
                    template_id=tid,
                    sort_order=binding_count,
                ))
                binding_count += 1
        print(f"已创建 {binding_count} 条模板绑定")

        # 6. 创建关系字典
        relation_id_by_name: dict[str, str] = {}
        for rdef in RELATIONS:
            relation = OntologyRelation(
                category_id=cat_id,
                name=rdef["name"],
                description=rdef["description"],
            )
            db.add(relation)
            await db.flush()
            await db.refresh(relation)
            relation_id_by_name[rdef["name"]] = relation.id
        print(f"已创建 {len(RELATIONS)} 个关系定义")

        # 7. 创建三元组约束
        constraint_count = 0
        for src_name, rel_name, tgt_name in CONSTRAINTS:
            src_id = ontology_id_by_name.get(src_name)
            rel_id = relation_id_by_name.get(rel_name)
            tgt_id = ontology_id_by_name.get(tgt_name)
            if not (src_id and rel_id and tgt_id):
                print(f"  ⚠️ 跳过无效约束：({src_name}, {rel_name}, {tgt_name})")
                continue
            db.add(OntologyRelationConstraint(
                category_id=cat_id,
                source_ontology_id=src_id,
                relation_id=rel_id,
                target_ontology_id=tgt_id,
            ))
            constraint_count += 1
        print(f"已创建 {constraint_count} 条三元组约束")

        await db.commit()
        print("\n✅ 民航维修领域本体数据已生成完毕。")
        print(f"   类别：{CATEGORY_NAME}")
        print(f"   本体数：{len(ONTOLOGIES)}")
        print(f"   属性数：{attr_count}")
        print(f"   关系数：{len(RELATIONS)}")
        print(f"   约束数：{constraint_count}")
        print(f"   属性模板数：{len(template_id_by_name)}")


async def _clear_category(db: AsyncSession, cat_id: str) -> None:
    """删除指定类别下的全部从属数据（本体定义层）。"""
    # 先查本体 id 列表
    result = await db.execute(select(Ontology.id).where(Ontology.category_id == cat_id))
    ont_ids = [r[0] for r in result.all()]

    if ont_ids:
        await db.execute(delete(OntologyTemplateBinding).where(OntologyTemplateBinding.ontology_id.in_(ont_ids)))
        await db.execute(delete(OntologyAttribute).where(OntologyAttribute.ontology_id.in_(ont_ids)))
        await db.execute(delete(Ontology).where(Ontology.category_id == cat_id))

    await db.execute(delete(OntologyRelationConstraint).where(OntologyRelationConstraint.category_id == cat_id))
    await db.execute(delete(OntologyRelation).where(OntologyRelation.category_id == cat_id))
    await db.execute(delete(OntologyCategory).where(OntologyCategory.id == cat_id))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed 民航维修领域本体")
    parser.add_argument("--force", action="store_true", help="若类别已存在则删除重建")
    args = parser.parse_args()
    asyncio.run(seed(args.force))


if __name__ == "__main__":
    main()
