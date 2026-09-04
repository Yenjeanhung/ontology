#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""民航维修领域真实知识库（用于图谱数据生成的骨架）。

本文件只放**行业公开的权威枚举**，不做任何虚构：
  - ATA 100 章节体系（ATA Specification 100，航空出版物国际编号标准）
  - 真实在役机型 / 发动机型号
  - 真实维修场景中的标准故障模式与检查/维修措施措辞

之所以把这些固化成常量而不是"随机生成"，是因为图谱的价值来自
「术语体系是真的、关系是符合工程实际的」，实例（机号/工单号）才允许按
业务规则派生。这样图计算（中心性、社区发现、故障传播、NFF 识别）的结果
才有可解释性。
"""

from __future__ import annotations

# ─────────────────────────── ATA 100 章节体系 ───────────────────────────
# (章节号, 中文名, 英文名)
ATA_CHAPTERS: list[tuple[str, str, str]] = [
    ("05", "时限与维护检查", "Time Limits / Maintenance Checks"),
    ("06", "尺寸与区域", "Dimensions and Areas"),
    ("07", "顶升与支撑", "Lifting and Shoring"),
    ("08", "校平与称重", "Leveling and Weighing"),
    ("09", "牵引与滑行", "Towing and Taxiing"),
    ("10", "停放与系留", "Parking, Mooring, Storage and Return to Service"),
    ("11", "标牌与标记", "Placards and Markings"),
    ("12", "勤务", "Servicing"),
    ("20", "标准施工（机体）", "Standard Practices - Airframe"),
    ("21", "空调", "Air Conditioning"),
    ("22", "自动飞行", "Auto Flight"),
    ("23", "通信", "Communications"),
    ("24", "电源", "Electrical Power"),
    ("25", "设备与装饰", "Equipment / Furnishings"),
    ("26", "防火", "Fire Protection"),
    ("27", "飞行操纵", "Flight Controls"),
    ("28", "燃油", "Fuel"),
    ("29", "液压", "Hydraulic Power"),
    ("30", "防冰与排雨", "Ice and Rain Protection"),
    ("31", "指示与记录系统", "Indicating / Recording Systems"),
    ("32", "起落架", "Landing Gear"),
    ("33", "照明", "Lights"),
    ("34", "导航", "Navigation"),
    ("35", "氧气", "Oxygen"),
    ("36", "气源", "Pneumatic"),
    ("38", "给水与排污", "Water / Waste"),
    ("45", "机载维护系统", "Onboard Maintenance Systems"),
    ("46", "信息系统", "Information Systems"),
    ("49", "辅助动力装置", "Airborne Auxiliary Power"),
    ("51", "标准施工与结构", "Standard Practices and Structures - General"),
    ("52", "舱门", "Doors"),
    ("53", "机身", "Fuselage"),
    ("54", "短舱与吊挂", "Nacelles / Pylons"),
    ("55", "安定面", "Stabilizers"),
    ("56", "窗户", "Windows"),
    ("57", "机翼", "Wings"),
    ("61", "螺旋桨", "Propellers"),
    ("65", "旋翼", "Rotors"),
    ("71", "动力装置", "Power Plant"),
    ("72", "发动机", "Engine"),
    ("73", "发动机燃油与控制", "Engine Fuel and Control"),
    ("74", "点火", "Ignition"),
    ("75", "空气", "Air"),
    ("76", "发动机控制", "Engine Controls"),
    ("77", "发动机指示", "Engine Indicating"),
    ("78", "排气", "Exhaust"),
    ("79", "滑油", "Oil"),
    ("80", "起动", "Starting"),
    ("81", "涡轮", "Turbines"),
    ("82", "喷水", "Water Injection"),
    ("83", "附件齿轮箱", "Accessory Gear Boxes"),
    ("84", "推进增强", "Propulsion Augmentation"),
]

ATA_BY_CODE = {code: (zh, en) for code, zh, en in ATA_CHAPTERS}

# ─────────────────────────── 真实机型 / 发动机 ───────────────────────────
# (型号, 制造商, 类别, 典型发动机选型)
AIRCRAFT_TYPES: list[tuple[str, str, str, list[str]]] = [
    ("737-700", "Boeing", "narrow_body", ["CFM56-7B"]),
    ("737-800", "Boeing", "narrow_body", ["CFM56-7B"]),
    ("737-900ER", "Boeing", "narrow_body", ["CFM56-7B"]),
    ("737 MAX 8", "Boeing", "narrow_body", ["LEAP-1B"]),
    ("737 MAX 9", "Boeing", "narrow_body", ["LEAP-1B"]),
    ("757-200", "Boeing", "narrow_body", ["PW2000", "RB211-535"]),
    ("767-300ER", "Boeing", "wide_body", ["PW4000", "CF6-80C2"]),
    ("777-200ER", "Boeing", "wide_body", ["GE90-94B", "PW4000"]),
    ("777-300ER", "Boeing", "wide_body", ["GE90-115B"]),
    ("777F", "Boeing", "wide_body", ["GE90-110B"]),
    ("787-8", "Boeing", "wide_body", ["GEnx-1B", "Trent 1000"]),
    ("787-9", "Boeing", "wide_body", ["GEnx-1B", "Trent 1000"]),
    ("747-400", "Boeing", "wide_body", ["PW4000", "CF6-80C2"]),
    ("747-8F", "Boeing", "wide_body", ["GEnx-2B"]),
    ("A319-100", "Airbus", "narrow_body", ["CFM56-5B", "V2500-A5"]),
    ("A320-200", "Airbus", "narrow_body", ["CFM56-5B", "V2500-A5"]),
    ("A320neo", "Airbus", "narrow_body", ["LEAP-1A", "PW1100G-JM"]),
    ("A321-200", "Airbus", "narrow_body", ["CFM56-5B", "V2500-A5"]),
    ("A321neo", "Airbus", "narrow_body", ["LEAP-1A", "PW1100G-JM"]),
    ("A330-200", "Airbus", "wide_body", ["Trent 700", "CF6-80E1", "PW4000"]),
    ("A330-300", "Airbus", "wide_body", ["Trent 700", "CF6-80E1"]),
    ("A350-900", "Airbus", "wide_body", ["Trent XWB"]),
    ("A350-1000", "Airbus", "wide_body", ["Trent XWB-97"]),
    ("C919", "COMAC", "narrow_body", ["LEAP-1C"]),
    ("ARJ21-700", "COMAC", "regional", ["CF34-10A"]),
]

ENGINE_TYPES: list[tuple[str, str]] = [
    ("CFM56-3", "CFM International"),
    ("CFM56-5B", "CFM International"),
    ("CFM56-7B", "CFM International"),
    ("LEAP-1A", "CFM International"),
    ("LEAP-1B", "CFM International"),
    ("LEAP-1C", "CFM International"),
    ("V2500-A5", "International Aero Engines"),
    ("PW1100G-JM", "Pratt & Whitney"),
    ("PW2000", "Pratt & Whitney"),
    ("PW4000", "Pratt & Whitney"),
    ("PW6000", "Pratt & Whitney"),
    ("GE90-94B", "General Electric"),
    ("GE90-110B", "General Electric"),
    ("GE90-115B", "General Electric"),
    ("GEnx-1B", "General Electric"),
    ("GEnx-2B", "General Electric"),
    ("CF6-80C2", "General Electric"),
    ("CF6-80E1", "General Electric"),
    ("CF34-10A", "General Electric"),
    ("Trent 700", "Rolls-Royce"),
    ("Trent 1000", "Rolls-Royce"),
    ("Trent XWB", "Rolls-Royce"),
    ("Trent XWB-97", "Rolls-Royce"),
    ("RB211-535", "Rolls-Royce"),
]

# ───────────────── 标准故障模式（工程措辞，来自维修实践）─────────────────
# (故障模式中文, 英文关键词, 典型 ATA 章节)
FAULT_MODES: list[tuple[str, str, list[str]]] = [
    ("疲劳裂纹", "fatigue crack", ["53", "57", "55", "32"]),
    ("应力腐蚀开裂", "stress corrosion cracking", ["53", "57", "32"]),
    ("腐蚀", "corrosion", ["53", "57", "51"]),
    ("磨损", "wear", ["32", "27", "72"]),
    ("渗漏", "leakage", ["29", "28", "79", "36"]),
    ("导线磨损/绝缘损伤", "chafing", ["27", "24", "31"]),
    ("松动/紧固件缺失", "loose or missing fastener", ["53", "54", "27"]),
    ("卡阻", "binding", ["27", "32"]),
    ("过热", "overheat", ["21", "26", "36", "72"]),
    ("短路", "short circuit", ["24", "26"]),
    ("指示错误", "erroneous indication", ["31", "77", "34"]),
    ("性能衰减", "performance degradation", ["72", "21", "73"]),
    ("滑油消耗超标", "excessive oil consumption", ["79", "72"]),
    ("振动异常", "abnormal vibration", ["72", "71", "61"]),
    ("异响", "abnormal noise", ["32", "72", "21"]),
    ("封严失效", "seal failure", ["29", "78", "32"]),
    ("堵塞", "blockage", ["28", "30", "38", "73"]),
    ("结冰", "ice accretion", ["30", "28"]),
    ("雷击损伤", "lightning strike damage", ["53", "57", "23"]),
    ("外来物损伤", "foreign object damage", ["72", "71"]),
    ("鸟击损伤", "bird strike damage", ["72", "53", "71"]),
    ("轮胎磨损/爆胎", "tire wear or burst", ["32"]),
    ("刹车磨损", "brake wear", ["32"]),
    ("舵面间隙超差", "excessive freeplay", ["27", "55"]),
    ("作动器失效", "actuator failure", ["27", "32", "29"]),
    ("泵失效", "pump failure", ["29", "28", "73"]),
    ("阀门卡滞", "valve sticking", ["29", "36", "21"]),
    ("传感器失效", "sensor failure", ["31", "73", "77"]),
    ("软件逻辑缺陷", "software logic deficiency", ["22", "31", "45"]),
    ("结构件脱层", "delamination", ["53", "57", "51"]),
]

# ───────────────── 标准维修措施（工程措辞）─────────────────
MAINTENANCE_ACTIONS: list[tuple[str, str, str]] = [
    ("一般目视检查", "general visual inspection", "GVI"),
    ("详细目视检查", "detailed visual inspection", "DET"),
    ("特别详细检查", "special detailed inspection", "SDI"),
    ("涡流探伤", "eddy current inspection", "NDT-EC"),
    ("超声波探伤", "ultrasonic inspection", "NDT-UT"),
    ("磁粉探伤", "magnetic particle inspection", "NDT-MPI"),
    ("渗透探伤", "penetrant inspection", "NDT-PT"),
    ("X射线检查", "radiographic inspection", "NDT-RT"),
    ("孔探检查", "borescope inspection", "BSI"),
    ("更换部件", "replacement", "RPL"),
    ("修理", "repair", "RPR"),
    ("改装", "modification", "MOD"),
    ("功能测试", "operational test", "OPC"),
    ("系统测试", "system test", "SYS"),
    ("润滑", "lubrication", "LUB"),
    ("清洁", "cleaning", "CLN"),
    ("调节/校装", "adjustment", "ADJ"),
    ("翻修", "overhaul", "OHL"),
    ("无损评估", "non-destructive evaluation", "NDE"),
    ("记录核查", "records check", "REC"),
    ("重复检查", "repetitive inspection", "RII"),
    ("防腐处理", "corrosion prevention treatment", "CPT"),
]

# ───────────────── 各 ATA 章节下的典型部件（真实部件名）─────────────────
# key = ATA 章节号，value = 该章节下的典型可更换单元/部件名
COMPONENTS_BY_ATA: dict[str, list[str]] = {
    "21": ["空调组件", "热交换器", "冲压空气门作动器", "再循环风扇", "温度控制活门", "空气分配管路", "座舱压力控制器", "外流活门"],
    "22": ["自动驾驶计算机", "飞行控制计算机", "自动油门计算机", "模式控制面板", "偏航阻尼器", "安定面配平作动器"],
    "23": ["甚高频电台", "高频电台", "卫星通信终端", "音频管理组件", "天线耦合器", "客舱广播放大器", "麦克风"],
    "24": ["发电机", "变压整流器", "蓄电池", "汇流条", "断路器", "静变流机", "发电机控制组件", "外部电源接触器"],
    "25": ["旅客座椅", "厨房", "盥洗室", "氧气面罩组件", "旅客服务组件", "地毯与侧壁板", "货舱衬板", "逃生滑梯"],
    "26": ["灭火瓶", "火警探测环路", "灭火控制面板", "发动机火警探测器", "货舱烟雾探测器", "灭火剂管路"],
    "27": ["副翼", "升降舵", "方向舵", "扰流板", "襟翼", "缝翼", "襟翼作动器", "扰流板作动筒", "钢索与滑轮", "机械力限制器", "配平作动器"],
    "28": ["燃油泵", "燃油关断活门", "燃油量传感器", "燃油箱", "通气系统", "加油控制面板", "燃油滤", "输油活门", "电容式油量探头"],
    "29": ["液压泵", "液压油箱", "液压作动器", "液压管路", "液压滤", "蓄压器", "液压关断活门", "优先活门", "液压油散热器"],
    "30": ["机翼防冰活门", "发动机防冰活门", "风挡加温控制器", "结冰探测器", "排雨剂系统", "皮托管加温元件", "风挡玻璃"],
    "31": ["主飞行显示器", "导航显示器", "发动机指示与机组警戒系统", "飞行数据记录器", "驾驶舱话音记录器", "时钟", "打印机", "警告灯面板"],
    "32": ["主起落架", "前起落架", "减震支柱", "机轮", "刹车组件", "刹车液压软管", "防滑活门", "轮速传感器", "转弯作动筒", "起落架收放作动筒", "上位锁", "舱门作动器"],
    "33": ["航行灯", "着陆灯", "滑行灯", "防撞灯", "客舱照明", "应急灯", "频闪灯"],
    "34": ["大气数据计算机", "惯性基准组件", "全球定位接收机", "测距机", "伏尔接收机", "仪表着陆接收机", "无线电高度表", "气象雷达", "近地警告计算机", "应答机", "皮托管", "静压孔", "迎角传感器"],
    "35": ["氧气瓶", "旅客氧气发生器", "机组氧气面罩", "氧气压力调节器", "化学氧气发生器", "氧气分配管路"],
    "36": ["引气活门", "预冷器", "压力调节关断活门", "引气管路", "交输引气活门", "高压级活门"],
    "38": ["水箱", "污水箱", "真空泵", "水加热器", "马桶组件", "排水阀"],
    "45": ["中央维护计算机", "机载维护终端", "便携式维护计算机接口", "故障数据记录器"],
    "49": ["辅助动力装置", "APU起动马达", "APU燃油控制器", "APU发电机", "APU进气门作动器", "APU排气消音器"],
    "51": ["复合材料壁板", "蜂窝结构", "密封胶", "紧固件", "垫片", "修理补片"],
    "52": ["登机门", "货舱门", "应急出口", "门框", "门锁机构", "门作动器", "逃生滑梯包", "门封严条"],
    "53": ["机身蒙皮", "隔框", "长桁", "搭接带", "龙骨梁", "客舱地板梁", "增压隔框", "剪切带", "止裂带", "检修口盖"],
    "54": ["发动机短舱", "吊挂", "反推装置", "进气道", "风扇整流罩", "吊挂接头", "短舱舱门"],
    "55": ["水平安定面", "垂直安定面", "升降舵铰接接头", "方向舵铰接接头", "安定面配平螺杆", "背鳍"],
    "56": ["风挡玻璃", "客舱窗户", "窗户密封件", "驾驶舱侧窗"],
    "57": ["机翼蒙皮", "翼肋", "翼梁", "前缘缝翼", "后缘襟翼", "副翼铰接接头", "扰流板铰链", "翼身整流罩", "翼尖小翼", "燃油通气口"],
    "71": ["发动机吊挂", "发动机整流罩", "发动机安装节", "发动机罩锁扣", "反推格栅"],
    "72": ["风扇叶片", "压气机叶片", "涡轮叶片", "发动机轴承", "发动机机匣", "转子", "静子", "进气锥", "风扇盘"],
    "73": ["燃油计量组件", "燃油喷嘴", "燃油泵", "燃油滤", "燃油控制器", "燃油流量传感器"],
    "74": ["点火激励器", "点火电嘴", "点火导线", "点火选择器"],
    "75": ["可变放气活门", "可调静子叶片", "增压级", "放气活门作动器", "引气活门"],
    "76": ["发动机电子控制器", "油门杆解算器", "发动机控制钢索", "推力手柄"],
    "77": ["发动机振动传感器", "排气温度传感器", "转速传感器", "滑油压力传感器", "发动机指示组件"],
    "78": ["反推装置", "尾喷管", "中心体", "消音衬套", "排气锥"],
    "79": ["滑油泵", "滑油滤", "滑油散热器", "滑油温度传感器", "磁堵探测器", "滑油箱", "油气分离器"],
    "80": ["起动机", "起动活门", "空气涡轮起动机", "起动控制器"],
}

# ───────────────── 故障征兆（维修工单上的飞行员/机务描述措辞）─────────────────
SYMPTOMS: list[str] = [
    "驾驶舱出现警告信息",
    "机组反映操纵力偏大",
    "巡航中出现异常抖动",
    "滑油压力指示偏低",
    "液压油量下降过快",
    "座舱增压异常",
    "空调出口温度偏高",
    "着陆后刹车温度过高",
    "起落架收放时间超差",
    "无线电通话有杂音",
    "导航显示出现偏差",
    "燃油量指示跳变",
    "发动机排气温度超温",
    "起动时间偏长",
    "舱门关闭指示不亮",
    "客舱照明闪烁",
    "风挡加温不工作",
    "APU 起动失败",
    "反推指示故障",
    "襟翼放出不同步",
    "扰流板升起不一致",
    "自动驾驶脱开",
    "货舱烟雾警告误触发",
    "氧气压力低于标准",
    "污水系统异味",
    "机组反映异响",
    "轮胎气压偏低",
    "发动机振动值偏高",
    "无线电高度表数据跳变",
    "气象雷达回波异常",
]

# ───────────────── 维修工单类型与站位 ─────────────────
WORK_ORDER_TYPES = ["航线排故", "定检发现", "过站检查", "重复故障", "AD/指令执行", "监控告警", "机组报告"]
MAINTENANCE_STATIONS = ["北京首都", "上海浦东", "广州白云", "深圳宝安", "成都天府", "西安咸阳", "昆明长水", "重庆江北", "杭州萧山", "南京禄口"]
NFF_RATE = 0.18  # 无故障发现（No Fault Found）在民航维修中的典型占比约 15%~25%
