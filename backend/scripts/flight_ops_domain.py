"""民航航班运行监控领域本体定义（纯数据）。

被 seed_flight_ops_ontology.py（定义层）与
seed_flight_ops_entities.py（实例层）共用。

领域模型一句话：
    航班(Flight) 按日期拆分为 航段(FlightLeg)；
    航段的全生命周期以 OOOI 里程碑推进：
    撤轮档(Out/AOBT) → 起飞(Off/ATOT) → 落地(On/ALDT) → 挡轮档(In/AIBT)，
    中间穿插 登机/滑出/爬升/巡航/下降/进近/滑入 等状态事件；
    异常（延误/取消/备降/复飞）与告警挂在航段上，
    机场/机位/跑道/机型/航空器/机组 提供资源维度。
"""

CATEGORY_NAME = "民航航班运行监控本体"
CATEGORY_DESC = (
    "面向航空公司运行控制（OCC/AOC）与航班全生命周期状态监控的领域本体。"
    "以航段（FlightLeg）为核心执行实例，围绕 ETD/ETA 预计时间与"
    "撤轮档(AOBT)→滑出→起飞(ATOT)→巡航→落地(ALDT)→滑入→挡轮档(AIBT)"
    "的 OOOI 里程碑节点，串起飞段状态事件、延误异常与运行告警，"
    "支撑函数、派生属性与运行监控工作流的构建。"
)

COLORS = {
    "航班运行核心类": "#5470c6",
    "运行状态事件类": "#ee6666",
    "延误异常类": "#fac858",
    "监控告警类": "#91cc75",
    "保障资源类": "#73c0de",
}

ONTOLOGIES: list[dict] = [
    {"name": "航班", "code": "Flight", "group": "航班运行核心类",
     "description": "航班号层面的计划对象，如 MU5137；同一航班号按日期派生航段实例"},
    {"name": "航段", "code": "FlightLeg", "group": "航班运行核心类",
     "description": "某日某航班的执行实例，运行监控核心对象，携带 STD/ETD/AOBT/ATOT/"
                    "ALDT/ETA/SIBT/AIBT 全生命周期里程碑时间"},
    {"name": "机场", "code": "Airport", "group": "航班运行核心类",
     "description": "起降机场，以 IATA 三字码/ICAO 四字码唯一标识，如 PVG/ZSPD"},
    {"name": "机型", "code": "OpsAircraftType", "group": "航班运行核心类",
     "description": "机型/系列，如 A320neo、B737-800、C919（与维修域机型同名，"
                    "编码加 Ops 前缀保持图标签隔离）"},
    {"name": "航空器", "code": "OpsAircraft", "group": "航班运行核心类",
     "description": "具体一架执行飞机，以注册号（如 B-1234）唯一标识"
                    "（与维修域航空器同名，编码加 Ops 前缀保持图标签隔离）"},
    {"name": "航段状态事件", "code": "LegStatusEvent", "group": "运行状态事件类",
     "description": "航段全生命周期状态节点：计划发布/登机/关门/撤轮档(Out)/滑出/跑道等待/"
                    "起飞(Off)/爬升/巡航/下降/进近/落地(On)/滑入/挡轮档(In)/到达结束；"
                    "同航段事件按事件序号串成生命周期链"},
    {"name": "延误记录", "code": "DelayRecord", "group": "延误异常类",
     "description": "航段出发/到达延误记录，含民航局统计口径原因分类与延误时长"},
    {"name": "异常事件", "code": "AbnormalEvent", "group": "延误异常类",
     "description": "非正常运行：取消、返航、备降、复飞、空中等待、中断起飞、滑回"},
    {"name": "运行告警", "code": "OpsAlert", "group": "监控告警类",
     "description": "运行监控告警：撤轮档超时、滑出时间过长、过站时间不足、延误超阈值、"
                    "预计衔接冲突、状态失联等"},
    {"name": "机位", "code": "Stand", "group": "保障资源类",
     "description": "机场机位，撤轮档与挡轮档的发生位置，分廊桥位与远机位"},
    {"name": "跑道", "code": "Runway", "group": "保障资源类",
     "description": "机场跑道，如 36L/18R，起飞与落地的发生位置"},
    {"name": "机组", "code": "Crew", "group": "保障资源类",
     "description": "飞行机组/乘务组成员，如机长、副驾驶、乘务长"},
]

# 生命周期事件类型（有序）——事件与航段里程碑时间的对应关系
LIFECYCLE_EVENTS = [
    "计划发布", "开始登机", "登机结束", "关闭舱门", "撤轮档", "滑出", "跑道等待",
    "起飞", "爬升", "巡航", "下降", "进近", "落地", "滑入", "挡轮档", "到达结束",
]

ATTRIBUTES: dict[str, list[dict]] = {
    "航班": [
        {"name": "航班号", "code": "flight_no", "data_type": "string", "is_required": True,
         "description": "承运人二字码+数字，如 MU5137 / CA1832"},
        {"name": "承运人代码", "code": "carrier_code", "data_type": "string",
         "description": "IATA 二字码，如 MU / CA / CZ"},
        {"name": "承运人名称", "code": "carrier_name", "data_type": "string"},
        {"name": "航班类型", "code": "flight_kind", "data_type": "string",
         "description": "国内 / 国际 / 地区（港澳台）"},
        {"name": "航线描述", "code": "route_desc", "data_type": "string",
         "description": "如 上海虹桥-北京首都"},
        {"name": "生效开始日期", "code": "effective_from", "data_type": "date"},
        {"name": "生效结束日期", "code": "effective_to", "data_type": "date"},
    ],
    "航段": [
        {"name": "航段号", "code": "leg_no", "data_type": "string", "is_required": True,
         "description": "业务主键：航班号+日期+序号，如 MU5137-20260908-01"},
        {"name": "航班日期", "code": "flight_date", "data_type": "date", "is_required": True,
         "description": "计划撤轮档日期（本地），运行监控业务日期"},
        {"name": "航班号", "code": "flight_no", "data_type": "string", "is_required": True},
        {"name": "出发机场", "code": "dep_airport", "data_type": "string", "is_required": True,
         "description": "IATA 三字码，如 SHA"},
        {"name": "到达机场", "code": "arr_airport", "data_type": "string", "is_required": True},
        {"name": "计划撤轮档时间", "code": "sobt", "data_type": "datetime", "is_required": True,
         "description": "SOBT/STD，Scheduled Off-Block Time"},
        {"name": "计划挡轮档时间", "code": "sibt", "data_type": "datetime", "is_required": True,
         "description": "SIBT/STA，Scheduled In-Block Time"},
        {"name": "预计撤轮档时间", "code": "etd", "data_type": "datetime",
         "description": "ETD/EOBT，Estimated Off-Block Time，随运行态势滚动更新"},
        {"name": "预计挡轮档时间", "code": "eta", "data_type": "datetime",
         "description": "ETA/EIBT，Estimated In-Block Time，随运行态势滚动更新"},
        {"name": "实际撤轮档时间", "code": "aobt", "data_type": "datetime",
         "description": "AOBT，Actual Off-Block Time，「撤轮档」里程碑（OOOI-Out）"},
        {"name": "实际起飞时间", "code": "atot", "data_type": "datetime",
         "description": "ATOT，Actual Take-Off Time，「起飞」里程碑（OOOI-Off）"},
        {"name": "实际落地时间", "code": "aldt", "data_type": "datetime",
         "description": "ALDT，Actual Landing Time，「落地」里程碑（OOOI-On）"},
        {"name": "实际挡轮档时间", "code": "aibt", "data_type": "datetime",
         "description": "AIBT，Actual In-Block Time，「挡轮档」里程碑（OOOI-In）"},
        {"name": "当前状态", "code": "leg_status", "data_type": "string",
         "description": "枚举：计划/登机/撤轮档/滑出/起飞/巡航/下降/进近/落地/滑入/"
                        "挡轮档/到达/取消/备降/返航"},
        {"name": "巡航高度", "code": "cruise_altitude", "data_type": "number",
         "description": "单位：米，如 10668（FL350）"},
        {"name": "计划过站时长", "code": "sched_turnaround_min", "data_type": "number",
         "description": "本段到达至下一衔接段撤轮档的计划过站时间（分钟）"},
        {"name": "旅客数", "code": "pax_count", "data_type": "number"},
        {"name": "数据来源", "code": "data_source", "data_type": "string",
         "description": "CDM / A-CDM / ACARS / ADS-B / OMIS / 人工"},
    ],
    "机场": [
        {"name": "IATA三字码", "code": "iata_code", "data_type": "string", "is_required": True},
        {"name": "ICAO四字码", "code": "icao_code", "data_type": "string",
         "description": "如 ZSPD / ZSSS / ZBAA"},
        {"name": "名称", "code": "name", "data_type": "string", "is_required": True},
        {"name": "城市", "code": "city", "data_type": "string"},
        {"name": "国家地区", "code": "country", "data_type": "string"},
        {"name": "机场类型", "code": "airport_type", "data_type": "string",
         "description": "国际 / 国内 / 地区"},
        {"name": "时区", "code": "timezone", "data_type": "string",
         "description": "如 Asia/Shanghai"},
        {"name": "UTC偏移", "code": "utc_offset", "data_type": "string", "description": "如 +08:00"},
    ],
    "机型": [
        {"name": "机型代码", "code": "type_code", "data_type": "string", "is_required": True,
         "description": "如 A320neo / B737-800 / A350-900 / C919"},
        {"name": "制造商", "code": "manufacturer", "data_type": "string",
         "description": "AIRBUS / BOEING / COMAC"},
        {"name": "宽窄体", "code": "body_type", "data_type": "string",
         "description": "宽体 / 窄体 / 支线"},
        {"name": "座位数", "code": "seat_count", "data_type": "number"},
        {"name": "巡航速度", "code": "cruise_speed", "data_type": "number",
         "description": "典型马赫数，如 0.78"},
        {"name": "最大航程", "code": "max_range_km", "data_type": "number", "description": "公里"},
    ],
    "航空器": [
        {"name": "注册号", "code": "registration", "data_type": "string", "is_required": True,
         "description": "如 B-1234"},
        {"name": "MSN", "code": "msn", "data_type": "string"},
        {"name": "所属机型", "code": "aircraft_type", "data_type": "string", "is_required": True},
        {"name": "所属航司", "code": "carrier_code", "data_type": "string"},
        {"name": "引进日期", "code": "entry_date", "data_type": "date"},
        {"name": "当前状态", "code": "status", "data_type": "string",
         "description": "在役 / 停场 / 定检 / 退役"},
    ],
    "航段状态事件": [
        {"name": "事件编号", "code": "event_no", "data_type": "string", "is_required": True,
         "description": "如 EV-MU5137-20260908-05"},
        {"name": "事件类型", "code": "event_type", "data_type": "string", "is_required": True,
         "description": "枚举（生命周期顺序）：" + "/".join(LIFECYCLE_EVENTS)},
        {"name": "事件时间", "code": "event_time", "data_type": "datetime", "is_required": True,
         "description": "事件实际发生（或预计发生）时刻"},
        {"name": "时间性质", "code": "time_kind", "data_type": "string",
         "description": "实际 / 预计 / 计划"},
        {"name": "事件序号", "code": "event_seq", "data_type": "number",
         "description": "同航段内生命周期先后序号，从小到大"},
        {"name": "数据来源", "code": "data_source", "data_type": "string",
         "description": "ACARS / ADS-B / A-CDM / CDM / OMIS / 人工"},
        {"name": "事件说明", "code": "remark", "data_type": "text"},
    ],
    "延误记录": [
        {"name": "延误编号", "code": "delay_no", "data_type": "string", "is_required": True},
        {"name": "延误类型", "code": "delay_type", "data_type": "string",
         "description": "出发延误 / 到达延误 / 取消"},
        {"name": "延误时长", "code": "delay_minutes", "data_type": "number",
         "description": "实际-计划偏差（分钟），民航统计口径 ≥15 分钟为延误"},
        {"name": "原因分类", "code": "cause_category", "data_type": "string",
         "description": "民航局统计口径：天气/航空公司/空管/旅客/军事活动/公共安全/"
                        "机场保障/联检/离港系统/其他"},
        {"name": "原因明细", "code": "cause_detail", "data_type": "text"},
        {"name": "责任认定", "code": "responsibility", "data_type": "string",
         "description": "承运人责任 / 非承运人责任"},
        {"name": "记录时间", "code": "recorded_at", "data_type": "datetime"},
    ],
    "异常事件": [
        {"name": "事件编号", "code": "abn_no", "data_type": "string", "is_required": True},
        {"name": "异常类型", "code": "abnormal_type", "data_type": "string", "is_required": True,
         "description": "枚举：取消/返航/备降/复飞/空中等待/中断起飞/滑回"},
        {"name": "发生时间", "code": "occurred_at", "data_type": "datetime"},
        {"name": "处置结果", "code": "disposal", "data_type": "string",
         "description": "如 备降长沙黄花后补班 / 盘旋25分钟后正常进近"},
        {"name": "原因描述", "code": "reason", "data_type": "text"},
        {"name": "影响程度", "code": "severity", "data_type": "string",
         "description": "轻微 / 一般 / 严重"},
    ],
    "运行告警": [
        {"name": "告警编号", "code": "alert_no", "data_type": "string", "is_required": True},
        {"name": "告警类型", "code": "alert_type", "data_type": "string", "is_required": True,
         "description": "枚举：撤轮档超时/滑出时间过长/空中延误超阈值/过站时间不足/"
                        "预计衔接冲突/状态失联/备降通知"},
        {"name": "严重级别", "code": "severity", "data_type": "string",
         "description": "提示 / 警告 / 严重"},
        {"name": "触发时间", "code": "triggered_at", "data_type": "datetime"},
        {"name": "触发阈值", "code": "threshold", "data_type": "string",
         "description": "如 计划撤轮档后30分钟仍未撤轮档"},
        {"name": "告警内容", "code": "message", "data_type": "text"},
        {"name": "处理状态", "code": "handle_status", "data_type": "string",
         "description": "待处理 / 已确认 / 已解除 / 误报"},
        {"name": "处理人", "code": "handler", "data_type": "string"},
    ],
    "机位": [
        {"name": "机位号", "code": "stand_no", "data_type": "string", "is_required": True,
         "description": "如 SHA-215 / PVG-D62"},
        {"name": "所属机场", "code": "airport", "data_type": "string", "is_required": True,
         "description": "IATA 三字码"},
        {"name": "机位类型", "code": "stand_type", "data_type": "string",
         "description": "廊桥位 / 远机位 / 除冰位 / 隔离位"},
        {"name": "适配机型", "code": "compatible_types", "data_type": "string",
         "description": "如 C/D/E/F 类机位"},
    ],
    "跑道": [
        {"name": "跑道号", "code": "runway_no", "data_type": "string", "is_required": True,
         "description": "如 36L / 18R"},
        {"name": "所属机场", "code": "airport", "data_type": "string", "is_required": True,
         "description": "IATA 三字码"},
        {"name": "长度", "code": "length_m", "data_type": "number", "description": "米"},
        {"name": "运行方向", "code": "direction", "data_type": "string",
         "description": "如 北向 / 南向"},
    ],
    "机组": [
        {"name": "工号", "code": "employee_no", "data_type": "string", "is_required": True},
        {"name": "姓名", "code": "name", "data_type": "string", "is_required": True},
        {"name": "岗位", "code": "role", "data_type": "string",
         "description": "机长 / 副驾驶 / 乘务长 / 乘务员"},
        {"name": "执照类型", "code": "license_type", "data_type": "string",
         "description": "如 ATPL / CPL"},
        {"name": "机型资质", "code": "type_rating", "data_type": "string"},
        {"name": "累计飞行小时", "code": "total_hours", "data_type": "number"},
    ],
}

RELATIONS: list[dict] = [
    {"name": "拆分航段", "code": "HAS_LEG",
     "description": "航班 → 航段：同一航班号不同日期的执行实例",
     "inverse": "属于航班"},
    {"name": "出发于", "code": "DEPARTS_FROM",
     "description": "航段 → 机场：出发机场", "inverse": "出港航段"},
    {"name": "到达于", "code": "ARRIVES_AT",
     "description": "航段 → 机场：到达机场", "inverse": "进港航段"},
    {"name": "计划机型", "code": "PLANNED_AC_TYPE",
     "description": "航段 → 机型：计划执行的机型", "inverse": "计划航段"},
    {"name": "执飞航空器", "code": "OPERATED_BY_AC",
     "description": "航段 → 航空器：实际或指派的执行飞机",
     "inverse": "执行航段"},
    {"name": "属于机型", "code": "OPS_BELONGS_TO_AC_TYPE", "description": "航空器 → 机型"},
    {"name": "产生事件", "code": "HAS_EVENT",
     "description": "航段 → 航段状态事件：全生命周期节点",
     "inverse": "所属航段"},
    {"name": "后继事件", "code": "NEXT_EVENT",
     "description": "航段状态事件 → 航段状态事件：生命周期先后顺序",
     "inverse": "前继事件"},
    {"name": "发生于机位", "code": "AT_STAND",
     "description": "航段状态事件 → 机位：地面节点位置"},
    {"name": "发生于跑道", "code": "ON_RUNWAY",
     "description": "航段状态事件 → 跑道：起飞/落地节点位置"},
    {"name": "衔接前序", "code": "PRECEDING_LEG",
     "description": "航段 → 航段：同一航空器过站衔接的上一航段，"
                    "用于过站时长与衔接冲突监控", "inverse": "衔接后继"},
    {"name": "产生延误", "code": "HAS_DELAY",
     "description": "航段 → 延误记录", "inverse": "延误航段"},
    {"name": "发生异常", "code": "HAS_ABNORMAL",
     "description": "航段 → 异常事件", "inverse": "异常航段"},
    {"name": "备降于", "code": "DIVERTED_TO",
     "description": "异常事件 → 机场：备降机场"},
    {"name": "源于异常", "code": "CAUSED_BY_ABNORMAL",
     "description": "延误记录 → 异常事件：延误由该异常导致"},
    {"name": "触发告警", "code": "TRIGGERS_ALERT",
     "description": "航段 → 运行告警：监控规则命中", "inverse": "告警航段"},
    {"name": "拥有机位", "code": "HAS_STAND", "description": "机场 → 机位"},
    {"name": "拥有跑道", "code": "HAS_RUNWAY", "description": "机场 → 跑道"},
    {"name": "撤轮档于", "code": "OFF_BLOCK_AT",
     "description": "航段 → 机位：出发机位"},
    {"name": "挡轮档于", "code": "ON_BLOCK_AT",
     "description": "航段 → 机位：到达机位"},
    {"name": "由机组执飞", "code": "CREWED_BY",
     "description": "航段 → 机组：执飞机组成员", "inverse": "执飞航段"},
]

CONSTRAINTS: list[tuple[str, str, str]] = [
    ("航班", "拆分航段", "航段"),
    ("航段", "出发于", "机场"),
    ("航段", "到达于", "机场"),
    ("航段", "计划机型", "机型"),
    ("航段", "执飞航空器", "航空器"),
    ("航空器", "属于机型", "机型"),
    ("航段", "产生事件", "航段状态事件"),
    ("航段状态事件", "后继事件", "航段状态事件"),
    ("航段状态事件", "发生于机位", "机位"),
    ("航段状态事件", "发生于跑道", "跑道"),
    ("航段", "衔接前序", "航段"),
    ("航段", "产生延误", "延误记录"),
    ("航段", "发生异常", "异常事件"),
    ("异常事件", "备降于", "机场"),
    ("延误记录", "源于异常", "异常事件"),
    ("航段", "触发告警", "运行告警"),
    ("机场", "拥有机位", "机位"),
    ("机场", "拥有跑道", "跑道"),
    ("航段", "撤轮档于", "机位"),
    ("航段", "挡轮档于", "机位"),
    ("航段", "由机组执飞", "机组"),
]

ATTRIBUTE_TEMPLATES: list[dict] = [
    {"name": "时间区间属性", "description": "含起止时间与时长的通用属性（延误、异常等）",
     "attributes": [
         {"name": "开始时间", "code": "start_time", "data_type": "datetime"},
         {"name": "结束时间", "code": "end_time", "data_type": "datetime"},
         {"name": "时长", "code": "duration", "data_type": "number", "description": "分钟"},
     ]},
    {"name": "监控数据溯源属性", "description": "监控实体的数据来源与置信信息（航段、状态事件）",
     "attributes": [
         {"name": "数据来源", "code": "data_source", "data_type": "string",
          "description": "CDM/A-CDM/ACARS/ADS-B/OMIS/人工"},
         {"name": "更新时间", "code": "updated_at", "data_type": "datetime"},
         {"name": "可信级别", "code": "confidence", "data_type": "string",
          "description": "高/中/低"},
     ]},
    {"name": "处置闭环属性", "description": "告警/异常处置跟踪的通用属性",
     "attributes": [
         {"name": "处理状态", "code": "handle_status", "data_type": "string"},
         {"name": "处理人", "code": "handler", "data_type": "string"},
         {"name": "处理时间", "code": "handled_at", "data_type": "datetime"},
         {"name": "处理备注", "code": "handle_note", "data_type": "text"},
     ]},
    {"name": "机场位置属性", "description": "带机场归属的保障资源通用属性（机位、跑道）",
     "attributes": [
         {"name": "所属机场", "code": "airport", "data_type": "string", "is_required": True,
          "description": "IATA 三字码"},
         {"name": "名称", "code": "name", "data_type": "string", "is_required": True},
     ]},
]

TEMPLATE_BINDINGS: dict[str, list[str]] = {
    "航段": ["监控数据溯源属性"],
    "航段状态事件": ["监控数据溯源属性"],
    "延误记录": ["时间区间属性", "处置闭环属性"],
    "异常事件": ["时间区间属性", "处置闭环属性"],
    "运行告警": ["处置闭环属性"],
    "机位": ["机场位置属性"],
    "跑道": ["机场位置属性"],
}
