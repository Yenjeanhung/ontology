# -*- coding: utf-8 -*-
"""
民航维修领域实体数据生成器
目标: 生成约 10 万真实实体(对齐本体类别) -> 灌入 Neo4j + Milvus
数据真实性来源:
  - 机型/发动机: corpus FAA_AD 目录(13类真实)
  - 适航指令(AD): 903篇真实 FAA AD 文档 (解析抽取)
  - ATA章节: 标准民航 ATA 100 章节(真实)
  - 部件: 真实民航维修部件词表 (按 ATA 章节组织)
  - 机号: 真实中国机号段 B-xxxx
  - 维修任务(工卡): 基于真实 机型x系统x检查类型 规模化扩展
"""
import os, re, json, glob, random
from datetime import date, timedelta

random.seed(42)
CORPUS = os.path.join(os.path.dirname(__file__), "..", "data", "corpus", "aviation_maintenance", "FAA_AD")
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "generated")
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------------------
# 1. 解析真实 FAA AD 语料
# ---------------------------------------------------------------------------
AD_TITLE_RE = re.compile(r"适用/?产品[:：]\s*(.+)", re.I)
ATA_RE = re.compile(r"ATA章节[:：]\s*([0-9]{2})\s*(.*)")
ADNUM_RE = re.compile(r"(20\d\d-\d{2}-\d{2}[A-Z]?\d*)")

def parse_ads():
    ads = []
    dirs = [d for d in os.listdir(CORPUS) if os.path.isdir(os.path.join(CORPUS, d))]
    for d in dirs:
        dp = os.path.join(CORPUS, d)
        for fn in glob.glob(os.path.join(dp, "*.txt")):
            with open(fn, "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            adnum = ""
            m = ADNUM_RE.search(txt)
            if m:
                adnum = m.group(1)
            # 标题行: 第二行通常是 标题：...
            title = ""
            for line in txt.splitlines():
                if "标题" in line or "Subject" in line:
                    title = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                    break
            ata = ""
            for line in txt.splitlines():
                mm = ATA_RE.search(line)
                if mm:
                    ata = mm.group(1)
                    break
            # 适用机型
            applies = d
            status = "final_rule"
            for line in txt.splitlines():
                if "状态" in line:
                    status = line.split("：", 1)[-1].strip()
                if "制造商" in line or "型号" in line:
                    applies = line.split("：", 1)[-1].strip()[:60]
            if adnum:
                ads.append({
                    "ad_number": adnum,
                    "title": title or f"Airworthiness Directive {adnum}",
                    "ata_chapter": ata,
                    "applicability": applies,
                    "status": status,
                    "source_model": d,
                })
    return ads

# ---------------------------------------------------------------------------
# 2. 机型 / 发动机 (真实, 来自语料目录)
# ---------------------------------------------------------------------------
MODELS = [
    # (model_code, family, manufacturer, engine, type)
    ("A318", "A320", "Airbus", "CFM56", "narrowbody"),
    ("A319", "A320", "Airbus", "CFM56/V2500", "narrowbody"),
    ("A320", "A320", "Airbus", "CFM56/V2500", "narrowbody"),
    ("A321", "A320", "Airbus", "CFM56/V2500", "narrowbody"),
    ("A320neo", "A320neo", "Airbus", "PW1100G/LEAP-1A", "narrowbody"),
    ("A330", "A330", "Airbus", "Trent700", "widebody"),
    ("A350", "A350", "Airbus", "TrentXWB", "widebody"),
    ("A380", "A380", "Airbus", "Trent900", "widebody"),
    ("B737-700", "B737NG", "Boeing", "CFM56", "narrowbody"),
    ("B737-800", "B737NG", "Boeing", "CFM56", "narrowbody"),
    ("B737MAX", "B737MAX", "Boeing", "LEAP-1B", "narrowbody"),
    ("B747-400", "B747", "Boeing", "CF6/PW4000", "widebody"),
    ("B747-8", "B747", "Boeing", "GEnx", "widebody"),
    ("B757", "B757", "Boeing", "RB211", "narrowbody"),
    ("B767", "B767", "Boeing", "CF6/PW4000", "widebody"),
    ("B777-200", "B777", "Boeing", "GE90", "widebody"),
    ("B777-300", "B777", "Boeing", "GE90", "widebody"),
    ("B787-8", "B787", "Boeing", "GEnx/Trent1000", "widebody"),
    ("B787-9", "B787", "Boeing", "GEnx/Trent1000", "widebody"),
    ("MD80", "MD80", "McDonnellDouglas", "JT8D", "narrowbody"),
]
ENGINES = ["CFM56", "V2500", "GE90", "Trent700", "TrentXWB", "Trent900",
           "PW1100G", "LEAP-1A", "LEAP-1B", "CF6", "PW4000", "RB211",
           "GEnx", "Trent1000", "JT8D", "PW4000"]

# ---------------------------------------------------------------------------
# 3. ATA 100 标准章节 (真实民航维修系统划分)
# ---------------------------------------------------------------------------
ATA = {
    "21": "空调(Air Conditioning)",
    "22": "自动飞行(Auto Flight)",
    "23": "通讯(Communications)",
    "24": "电源(Electrical Power)",
    "25": "设备与装饰(Equipment/Furnishings)",
    "26": "防火(Fire Protection)",
    "27": "飞行操纵(Flight Controls)",
    "28": "燃油(Fuel)",
    "29": "液压(Hydraulic Power)",
    "30": "防冰防雨(Ice/Rain Protection)",
    "31": "指示记录(Indicating/Recording)",
    "32": "起落架(Landing Gear)",
    "33": "灯光(Lights)",
    "34": "导航(Navigation)",
    "35": "氧气(Oxygen)",
    "36": "气源(Pneumatic)",
    "37": "真空/污水(Vacuum/Waste)",
    "38": "水/水/盥洗(Water/Sanitary)",
    "45": "中央维护(Central Maintenance)",
    "49": "辅助动力装置(APU)",
    "51": "结构(Structures)",
    "52": "门(Doors)",
    "53": "机身(Fuselage)",
    "54": "吊架(Nacelles)",
    "55": "安定面(Stabilizers)",
    "56": "窗户(Windows)",
    "57": "机翼(Wings)",
    "70": "发动机燃油控(Eng Fuel/Control)",
    "71": "动力装置(Power Plant)",
    "72": "发动机涡轮(Turbine Engine)",
    "73": "发动机燃油控(Eng Fuel/Control)",
    "74": "点火(Ignition)",
    "75": "空气(Eng Air)",
    "76": "发动机控制(Eng Control)",
    "77": "发动机指示(Eng Indicating)",
    "78": "排气(Eng Exhaust)",
    "79": "滑油(Eng Oil)",
    "80": "起动(Eng Starting)",
    "81": "引气(Eng Air)",
}

# 真实民航维修部件词表 (按 ATA 章节) —— 用于生成 Component 实体
COMPONENT_DICT = {
    "21": ["主空调组件(PCK)", "再循环风扇", "区域温控阀", "冷凝器", "蒸发器", "增压控制器", "客舱温度传感器"],
    "22": ["FCC飞行控制计算机", "自动驾驶作动器", "偏航阻尼器", "方式控制板(MCP)", "自动驾驶脱开电门"],
    "23": ["VHF收发机", "HF收发机", "卫星通信天线", "ACARS管理单元", "客舱内话手机"],
    "24": ["主导发电机(IDG)", "电瓶", "变压整流器(TRU)", "静变流机", "外部电源接口", "汇流条"],
    "25": ["旅客座椅", "厨房组件", "行李架", "乘务员座椅", "PSU旅客服务组件"],
    "26": ["发动机火警探测器", "货舱烟雾探测器", "灭火瓶", "火警控制面板", "APU火警探测器"],
    "27": ["副翼作动器(PCU)", "升降舵作动器", "方向舵作动器", "飞行扰流板", "襟翼控制计算机", "副翼配平"],
    "28": ["燃油泵(增压)", "燃油增压泵", "燃油量指示计算机", "加油/抽油接口", "交输活门", "燃油滤"],
    "29": ["液压泵(EDP)", "液压油箱", "储压器", "方向舵伺服阀", "液压油滤", "压力组件"],
    "32": ["主起落架支柱", "前起落架", "机轮", "刹车组件(碳)", "防滞刹车控制", "转弯作动器", "胎压传感器"],
    "33": ["舱顶灯", "着陆灯", "航行灯", "应急灯", "驾驶舱灯"],
    "34": ["ADIRU大气数据计算机", "VOR接收机", "ILS接收机", "GPS天线", "TCAS计算机", "气象雷达"],
    "35": ["旅客氧气瓶", "机组氧气面罩", "氧气发生器", "氧气面罩释放单元"],
    "36": ["引气压力调节阀(PRSOV)", "预冷器", "引气活门", "过压保护阀", "引气控制计算机"],
    "49": ["APU起动机", "APU发电机", "APU滑油冷却器", "APU引气阀", "APU排气消音器"],
    "71": ["发动机吊架", "反推装置", "发动机整流罩", "防火墙"],
    "72": ["高压压气机(HPC)", "高压涡轮(HPT)", "低压压气机(LPC)", "燃烧室", "风扇叶片", "涡轮盘"],
    "73": ["燃油控制组件(MEC)", "燃油泵(发动机)", "燃油滤(发动机)", "伺服燃油加热器"],
    "74": ["点火激励器", "点火电嘴", "点火导线"],
    "75": ["发动机引气压力调节", "发动机引气活门", "高压引气"],
    "76": ["EEC发动机电子控制", "推力控制", "功率管理组件"],
    "77": ["EGT热电偶", "N1转速传感器", "N2转速传感器", "振动传感器", "燃油流量传感器"],
    "78": ["排气喷管", "反推格栅", "排气温度探头", "消音器"],
    "79": ["滑油泵", "滑油滤", "滑油冷却器", "滑油温度传感器", "滑油箱"],
    "80": ["起动机(空气涡轮)", "起动机活门", "起动控制组件"],
}

# 监测参数 (真实)
PARAMETERS = {
    "EGT": "排气温度", "N1": "风扇转速", "N2": "核心机转速", "VIB": "振动值",
    "FF": "燃油流量", "OILP": "滑油压力", "OILT": "滑油温度", "FUELQ": "燃油量",
    "HYDP": "液压压力", "ACV": "交流电压", "DCV": "直流电压", "CABT": "客舱温度",
    "TIREP": "轮胎压力", "BRAKE_T": "刹车温度", "BLEEDP": "引气压力", "OXY_P": "氧气压力",
}

# 故障模式 (真实民航维修)
FAILURE_MODES = [
    "裂纹(Crack)", "磨损(Wear)", "腐蚀(Corrosion)", "渗漏(Leak)", "卡阻(Jam)",
    "超温(Overtemp)", "振动超限(High Vibration)", "指示失效(Indication Loss)",
    "电气短路(Short Circuit)", "断路(Open Circuit)", "疲劳(Fatigue)", "松动(Loosening)",
    "密封失效(Seal Failure)", "性能衰退(Performance Degradation)", "结冰(Icing)",
    "过热(Overheat)", "压力丧失(Pressure Loss)", "轴承失效(Bearing Failure)",
    "雷击损伤(Lightning Strike)", "鸟击(Bird Strike)", "外来物损伤(FOD)",
]

# 航空公司: 仅中国国际航空(Air China)使用本系统
OPERATORS = ["中国国航"]

# ── 新增本体：故障/维修/组织/文件类基础词表 ──
ROOT_CAUSES = [
    "密封老化", "机械磨损", "疲劳裂纹", "腐蚀损伤", "电气短路", "接触不良",
    "传感器漂移", "软件版本缺陷", "线缆磨损", "接头松动", "滑油污染", "燃油污染",
    "外来物损伤", "热应力累积", "振动超标", "制造缺陷", "装配不当", "维护疏漏",
    "设计裕度不足", "材料退化", "氧化", "电化学腐蚀", "应力集中", "润滑失效",
    "散热不足", "过压冲击", "欠压启动", "潮气侵入", "灰尘积聚", "鸟击",
]

SYMPTOMS = [
    "振动值偏高", "排气温度超限", "滑油压力低", "燃油流量异常", "客舱温度波动",
    "液压压力低", "引气压力低", "电瓶电压低", "起落架未锁定指示", "自动驾驶脱开",
    "发动机喘振", "反推解锁灯亮", "货舱烟雾警告", "氧气压力低", "舱门未关好",
    "TCAS TA/RA", "气象雷达失效", "VHF通讯中断", "GPS信号丢失", "ADIRU漂移",
    "刹车温度高", "轮胎压力低", "APU引气不可用", "发动机点火失败", "中央维护信息",
    "燃油不平衡", "飞行操纵感觉重", "襟翼不对称", "安定面配平失效", "饮用水系统低压",
    "发动机滑油温度高", "N1转速异常", "N2转速异常", "EGT指示跳变",
]

CONDITIONS = [
    {"name": "巡航阶段", "desc": "巡航高度、稳定推力"},
    {"name": "爬升阶段", "desc": "大功率、高温环境"},
    {"name": "下降阶段", "desc": "低功率、高下降率"},
    {"name": "地面滑行", "desc": "低滑油压力、高振动"},
    {"name": "高高原机场", "desc": "低气压、低温、跑道短"},
    {"name": "湿热环境", "desc": "高温高湿、盐雾腐蚀"},
    {"name": "寒冷环境", "desc": "低温、结冰风险"},
    {"name": "沙尘环境", "desc": "灰尘侵入、FOD风险"},
    {"name": "雷暴天气", "desc": "强颠簸、雷击风险"},
    {"name": "夜航运行", "desc": "照明、人员疲劳"},
    {"name": "短跑道起降", "desc": "高刹车负荷、大推力"},
    {"name": "高载重运行", "desc": "超重着陆、结构负荷大"},
    {"name": "频繁起降", "desc": "循环密集、热循环累积"},
    {"name": "湿跑道", "desc": "防滑、刹车性能降级"},
    {"name": "冬季除冰", "desc": "除冰液、传感器覆盖"},
    {"name": "机库停放", "desc": "静止、湿度、啮齿动物"},
    {"name": "外站过站", "desc": "工具航材受限、时间短"},
    {"name": "定检停场", "desc": "深度检修、部件拆装"},
    {"name": "AOG保障", "desc": "应急抢修、非计划停场"},
    {"name": "新件磨合", "desc": "初期故障、参数不稳定"},
]

ACTIONS = [
    {"name": "更换LRU", "type": "replace"},
    {"name": "清洁传感器", "type": "clean"},
    {"name": "调节间隙", "type": "adjust"},
    {"name": "修复裂纹", "type": "repair"},
    {"name": "润滑", "type": "lubricate"},
    {"name": "功能测试", "type": "test"},
    {"name": "保留并监控", "type": "defer"},
    {"name": "执行AD改装", "type": "modify"},
    {"name": "校准", "type": "calibrate"},
    {"name": "紧固连接件", "type": "tighten"},
    {"name": "更换封圈", "type": "replace"},
    {"name": "更换轴承", "type": "replace"},
    {"name": "清洗油滤", "type": "clean"},
    {"name": "排放积水", "type": "clean"},
    {"name": "更换线束", "type": "replace"},
    {"name": "软件升级", "type": "modify"},
    {"name": "复位跳开关", "type": "reset"},
    {"name": "隔离故障", "type": "isolate"},
    {"name": "串件验证", "type": "test"},
    {"name": "无损检测", "type": "test"},
    {"name": "更换液压油", "type": "replace"},
    {"name": "检查插头", "type": "inspect"},
    {"name": "更换电瓶", "type": "replace"},
    {"name": "风扇配平", "type": "adjust"},
    {"name": "校装操纵面", "type": "adjust"},
    {"name": "更换点火电嘴", "type": "replace"},
    {"name": "孔探检查", "type": "inspect"},
    {"name": "磁堵检查", "type": "inspect"},
    {"name": "称重与配平", "type": "calibrate"},
    {"name": "系统冲洗", "type": "clean"},
]

TOOLS = [
    {"name": "力矩扳手", "type": "manual", "calib": True},
    {"name": "液压测试台", "type": "tester", "calib": True},
    {"name": "航电综合测试仪", "type": "tester", "calib": True},
    {"name": "孔探设备", "type": "inspector", "calib": True},
    {"name": "振动分析仪", "type": "analyzer", "calib": True},
    {"name": "万用表", "type": "meter", "calib": True},
    {"name": "兆欧表", "type": "meter", "calib": True},
    {"name": "超声波探伤仪", "type": "ndt", "calib": True},
    {"name": "磁粉探伤机", "type": "ndt", "calib": True},
    {"name": "涡流探伤仪", "type": "ndt", "calib": True},
    {"name": "发动机吊具", "type": "lifting", "calib": False},
    {"name": "起落架安装车", "type": "support", "calib": False},
    {"name": "千斤顶", "type": "support", "calib": False},
    {"name": "液压油车", "type": "service", "calib": False},
    {"name": "滑油车", "type": "service", "calib": False},
    {"name": "氮气瓶", "type": "service", "calib": False},
    {"name": "氧气瓶", "type": "service", "calib": False},
    {"name": "除冰车", "type": "ground", "calib": False},
    {"name": "电源车", "type": "ground", "calib": False},
    {"name": "气源车", "type": "ground", "calib": False},
    {"name": "高精度电子秤", "type": "meter", "calib": True},
    {"name": "热成像仪", "type": "inspector", "calib": True},
    {"name": "烟雾测试仪", "type": "tester", "calib": True},
    {"name": "电缆测试仪", "type": "tester", "calib": True},
    {"name": "专用拆装夹具", "type": "manual", "calib": False},
    {"name": "导管弯曲机", "type": "manual", "calib": False},
    {"name": "压接工具", "type": "manual", "calib": False},
    {"name": "激光对中仪", "type": "analyzer", "calib": True},
    {"name": "燃油油样分析仪", "type": "analyzer", "calib": True},
    {"name": "轮胎拆装机", "type": "manual", "calib": False},
    {"name": "刹车测试台", "type": "tester", "calib": True},
    {"name": "客舱压力测试仪", "type": "tester", "calib": True},
    {"name": "货舱门测试装置", "type": "tester", "calib": True},
    {"name": "滑梯气瓶称", "type": "meter", "calib": True},
    {"name": "钢索张力计", "type": "meter", "calib": True},
    {"name": "深度游标卡尺", "type": "meter", "calib": True},
    {"name": "表面粗糙度仪", "type": "meter", "calib": True},
    {"name": "铆钉枪", "type": "manual", "calib": False},
    {"name": "喷漆枪", "type": "manual", "calib": False},
    {"name": "真空泵", "type": "service", "calib": False},
    {"name": "清洗喷枪", "type": "manual", "calib": False},
    {"name": "热风枪", "type": "manual", "calib": False},
    {"name": "防静电手环", "type": "ppe", "calib": False},
]

MANUAL_TYPES = ["AMM", "FIM", "TSM", "IPC", "SSM", "SWPM", "MEL", "CDL"]

PERSONNEL_ROLES = [
    "机电工程师", "航电工程师", "结构工程师", "放行人员",
    "检验员", "维修员", "计划工程师", "可靠性工程师", "质量工程师",
]

STATIONS = [
    {"name": "北京首都基地", "type": "主基地", "city": "北京"},
    {"name": "上海虹桥基地", "type": "分公司基地", "city": "上海"},
    {"name": "上海浦东航站", "type": "航站", "city": "上海"},
    {"name": "广州白云基地", "type": "分公司基地", "city": "广州"},
    {"name": "深圳宝安航站", "type": "航站", "city": "深圳"},
    {"name": "成都双流基地", "type": "分公司基地", "city": "成都"},
    {"name": "杭州萧山航站", "type": "航站", "city": "杭州"},
    {"name": "西安咸阳航站", "type": "航站", "city": "西安"},
    {"name": "重庆江北航站", "type": "航站", "city": "重庆"},
    {"name": "昆明长水航站", "type": "航站", "city": "昆明"},
    {"name": "青岛胶东航站", "type": "航站", "city": "青岛"},
    {"name": "大连周水子航站", "type": "航站", "city": "大连"},
    {"name": "南京禄口航站", "type": "航站", "city": "南京"},
    {"name": "武汉天河航站", "type": "航站", "city": "武汉"},
    {"name": "厦门高崎航站", "type": "航站", "city": "厦门"},
    {"name": "长沙黄花航站", "type": "航站", "city": "长沙"},
    {"name": "沈阳桃仙航站", "type": "航站", "city": "沈阳"},
    {"name": "郑州新郑航站", "type": "航站", "city": "郑州"},
    {"name": "乌鲁木齐地窝堡航站", "type": "航站", "city": "乌鲁木齐"},
    {"name": "哈尔滨太平航站", "type": "航站", "city": "哈尔滨"},
    {"name": "拉萨贡嘎航站", "type": "外站", "city": "拉萨"},
    {"name": "三亚凤凰航站", "type": "航站", "city": "三亚"},
    {"name": "海口美兰航站", "type": "航站", "city": "海口"},
    {"name": "天津滨海航站", "type": "航站", "city": "天津"}
]

# 维修任务类型 (真实工卡类型)
TASK_TYPES = ["A检", "C检", "航线检查", "定检", "故障排故", "改装", "时控件更换",
              "适航指令执行(AD)", "服务通告执行(SB)", "无损检测(NDI)", "功能测试", "润滑"]

def gen():
    data = {}
    # AD (真实)
    ads = parse_ads()
    data["ads"] = ads
    # 机型
    data["models"] = [{"code": m[0], "family": m[1], "manufacturer": m[2],
                       "engine": m[3], "type": m[4]} for m in MODELS]
    data["engines"] = ENGINES
    # ATA 系统
    data["ata"] = [{"chapter": k, "name": v} for k, v in ATA.items()]
    # 部件 (机型 x ATA 系统 x 部件名)
    comps = []
    cid = 0
    for m in MODELS:
        code = m[0]
        for ch, name in ATA.items():
            parts = COMPONENT_DICT.get(ch, ["通用部件"])
            for p in parts:
                cid += 1
                pn = f"{ch}-{code[:3].upper()}-{cid:05d}"
                comps.append({
                    "comp_id": f"COMP_{cid:06d}",
                    "name": f"{p}",
                    "pn": pn,
                    "ata_chapter": ch,
                    "model": code,
                    "manufacturer": m[2],
                    "mtbf_hours": random.randint(2000, 30000),
                })
    data["components"] = comps
    # 参数
    data["parameters"] = [{"pid": f"PARAM_{k}", "name": v, "symbol": k} for k, v in PARAMETERS.items()]
    # 故障模式
    data["failure_modes"] = [{"fid": f"FM_{i:03d}", "name": fm} for i, fm in enumerate(FAILURE_MODES, 1)]
    # 航空公司
    data["operators"] = [{"oid": f"OP_{i:02d}", "name": op} for i, op in enumerate(OPERATORS, 1)]
    # 飞机: 全部归属中国国航, 机号 B-xxxx, 航班号 CA 开头
    aircraft = []
    reg = 1001
    # 国航单机队规模(需撑起约10万工卡): 每机 ~280工卡 -> 约360架
    fleet_size = 360
    aid = 0
    for _ in range(fleet_size):
        aid += 1
        reg += 1
        code = random.choice([m[0] for m in MODELS])
        model = next(mm for mm in MODELS if mm[0] == code)
        tail = f"B-{reg}"
        # 国航航班号: CA + 2~4位数字
        flight_no = f"CA{random.randint(1000, 9999)}"
        ac_year = random.randint(2008, 2023)
        msn = f"{random.randint(1000,9999)}{random.randint(10,99)}"
        aircraft.append({
            "acid": f"AC_{aid:04d}",
            "tail": tail,
            "flight_no": flight_no,
            "model": code,
            "manufacturer": model[2],
            "engine": model[3],
            "msn": msn,
            "operator": "OP_01",
            "entry_year": ac_year,
            "total_cycles": random.randint(5000, 60000),
            "total_fh": random.randint(8000, 90000),
        })
    data["aircraft"] = aircraft
    # 维修任务 (工卡) —— 主体量, 每机 x 系统 x 任务类型 抽样
    tasks = []
    tid = 0
    for ac in aircraft:
        # 每架飞机随机 200~350 个工卡
        n_task = random.randint(200, 350)
        ch_list = list(ATA.keys())
        for _ in range(n_task):
            tid += 1
            ch = random.choice(ch_list)
            tt = random.choice(TASK_TYPES)
            due_cyc = random.choice([random.randint(50, 6000) for _ in range(1)] + [None])
            interval_days = random.choice([30, 90, 180, 365, 730])
            due_date = date(2026, 1, 1) + timedelta(days=random.randint(0, 900))
            # 关联一个部件
            related = [c for c in comps if c["model"] == ac["model"] and c["ata_chapter"] == ch]
            comp = random.choice(related) if related else random.choice(comps)
            # 关联 AD (同机型或同系统)
            ad = random.choice(ads) if ads else None
            # 中文工单名称: 任务类型 + 部件名 + 机型/机号
            task_name = f"{tt}-{comp['name']}({ac['model']}/{ac['tail']})"
            tasks.append({
                "taskid": f"TASK_{tid:07d}",
                "name": task_name,
                "tail": ac["tail"],
                "acid": ac["acid"],
                "model": ac["model"],
                "ata_chapter": ch,
                "task_type": tt,
                "comp_id": comp["comp_id"],
                "comp_name": comp["name"],
                "ad_number": ad["ad_number"] if (ad and tt in ("适航指令执行(AD)",)) else "",
                "interval_days": interval_days,
                "due_date": due_date.isoformat(),
                "status": random.choice(["计划", "到期", "超期", "完成", "关闭"]),
                "manhours": random.randint(1, 40),
            })
            if tid >= 95000:
                break
        if tid >= 95000:
            break
    data["tasks"] = tasks

    # ── 新增：补全空本体类别实体 ──
    # 故障代码
    fault_codes = []
    for i in range(1, 601):
        comp = random.choice(comps)
        ch = comp["ata_chapter"]
        fault_codes.append({
            "fc_id": f"FC_{i:06d}",
            "code": f"{ch}-{random.randint(1000,9999)}",
            "name": f"{comp['name']}{random.choice(['信号异常','温度过高','压力低','指示失效','渗漏','卡阻','振动大'])}-{ch}",
            "ata_chapter": ch,
            "model": comp["model"],
            "comp_id": comp["comp_id"],
            "severity": random.choice(["MINOR", "MAJOR", "HAZARDOUS"]),
        })
    data["fault_codes"] = fault_codes

    # 故障原因
    data["root_causes"] = [
        {"rc_id": f"RC_{i:03d}", "name": rc, "category": random.choice(["机械", "电气", "材料", "环境", "人为"])}
        for i, rc in enumerate(ROOT_CAUSES, 1)
    ]

    # 故障征兆
    data["symptoms"] = [
        {"sym_id": f"SYM_{i:03d}", "name": sym, "level": random.choice(["轻微", "明显", "严重"])}
        for i, sym in enumerate(SYMPTOMS, 1)
    ]

    # 工况
    data["conditions"] = [
        {"cond_id": f"COND_{i:03d}", "name": c["name"], "description": c["desc"]}
        for i, c in enumerate(CONDITIONS, 1)
    ]

    # 维修措施
    data["actions"] = [
        {"act_id": f"ACT_{i:03d}", "name": a["name"], "action_type": a["type"]}
        for i, a in enumerate(ACTIONS, 1)
    ]

    # 工装工具
    data["tools"] = [
        {"tool_id": f"TOOL_{i:03d}", "name": t["name"], "tool_type": t["type"], "calibration_required": t["calib"]}
        for i, t in enumerate(TOOLS, 1)
    ]

    # 手册
    manuals = []
    mid = 0
    for m in MODELS:
        for mt in MANUAL_TYPES:
            mid += 1
            manuals.append({
                "man_id": f"MAN_{mid:05d}",
                "doc_type": mt,
                "model": m[0],
                "name": f"{mt} {m[0]}",
                "revision": f"Rev {random.randint(10, 50)}",
            })
    data["manuals"] = manuals

    # 人员
    personnel = []
    for i in range(1, 101):
        family = random.choice(["张", "李", "王", "赵", "刘", "陈", "杨", "黄", "周"])
        given = random.choice(["伟", "强", "军", "明", "华", "建", "敏", "磊", "涛", "杰", "波", "刚"])
        personnel.append({
            "per_id": f"PER_{i:04d}",
            "name": f"{family}{given}-{i:03d}",
            "role": random.choice(PERSONNEL_ROLES),
            "license": f"ME-{random.randint(1000,9999)}",
            "station": random.choice(STATIONS)["name"],
        })
    data["personnel"] = personnel

    # 维修站
    data["stations"] = [
        {"st_id": f"ST_{i:03d}", "name": s["name"], "station_type": s["type"], "city": s["city"]}
        for i, s in enumerate(STATIONS, 1)
    ]

    # MEL保留
    mel_deferrals = []
    for i, ac in enumerate(aircraft, 1):
        mel_deferrals.append({
            "mel_id": f"MEL_{i:04d}",
            "deferral_code": f"MEL-{random.choice(['21','24','29','32','34','36'])}-{i:03d}",
            "item": random.choice(["空调组件", "发电机", "液压泵", "刹车", "导航接收机", "引气活门", "旅客氧气"]),
            "category": random.choice(["A", "B", "C", "D"]),
            "tail": ac["tail"],
            "due_date": (date(2026, 1, 1) + timedelta(days=random.randint(1, 120))).isoformat(),
        })
    data["mel_deferrals"] = mel_deferrals

    # 航材（补充）
    spare_parts = []
    for i, comp in enumerate(comps[:1000], 1):
        spare_parts.append({
            "sp_id": f"SP_{i:06d}",
            "name": f"{comp['name']}备件",
            "pn": f"SP-{comp['pn']}",
            "ata_chapter": comp["ata_chapter"],
            "model": comp["model"],
        })
    data["spare_parts"] = spare_parts

    # 关系：用于后续同步到运行时图库（按本地 ID 关联）
    relations = []
    # 工单 -> 故障代码 / 措施 / 工具 / 人员
    for i, t in enumerate(tasks):
        fc = fault_codes[i % len(fault_codes)]
        relations.append({"start_type": "WorkOrder", "start_id": t["taskid"], "rel_type": "报告故障", "end_type": "FaultCode", "end_id": fc["fc_id"]})
        if i % 3 == 0:
            act = ACTIONS[i % len(ACTIONS)]
            relations.append({"start_type": "WorkOrder", "start_id": t["taskid"], "rel_type": "执行措施", "end_type": "Action", "end_id": f"ACT_{(i % len(ACTIONS)) + 1:03d}"})
        if i % 5 == 0:
            tool = TOOLS[i % len(TOOLS)]
            relations.append({"start_type": "WorkOrder", "start_id": t["taskid"], "rel_type": "使用工具", "end_type": "Tool", "end_id": f"TOOL_{(i % len(TOOLS)) + 1:03d}"})
        if i % 7 == 0:
            per = personnel[i % len(personnel)]
            relations.append({"start_type": "WorkOrder", "start_id": t["taskid"], "rel_type": "指派给", "end_type": "Personnel", "end_id": per["per_id"]})

    # 故障代码 -> 系统 / 部件 / 原因 / 工况 / 征兆
    for i, fc in enumerate(fault_codes):
        relations.append({"start_type": "FaultCode", "start_id": fc["fc_id"], "rel_type": "属于系统", "end_type": "ATASystem", "end_id": fc["ata_chapter"]})
        relations.append({"start_type": "FaultCode", "start_id": fc["fc_id"], "rel_type": "涉及部件", "end_type": "Component", "end_id": fc["comp_id"]})
        rc = data["root_causes"][i % len(data["root_causes"])]
        relations.append({"start_type": "FaultCode", "start_id": fc["fc_id"], "rel_type": "由原因导致", "end_type": "RootCause", "end_id": rc["rc_id"]})
        cond = data["conditions"][i % len(data["conditions"])]
        relations.append({"start_type": "FaultCode", "start_id": fc["fc_id"], "rel_type": "发生于工况", "end_type": "Condition", "end_id": cond["cond_id"]})
        sym = data["symptoms"][i % len(data["symptoms"])]
        relations.append({"start_type": "FaultCode", "start_id": fc["fc_id"], "rel_type": "表现为征兆", "end_type": "Symptom", "end_id": sym["sym_id"]})

    # 措施 -> 工具
    for i in range(min(len(ACTIONS), len(TOOLS))):
        relations.append({"start_type": "Action", "start_id": f"ACT_{i+1:03d}", "rel_type": "使用工具", "end_type": "Tool", "end_id": f"TOOL_{i+1:03d}"})

    # 部件 -> 手册
    for i, comp in enumerate(comps[:200]):
        man = manuals[i % len(manuals)]
        relations.append({"start_type": "Component", "start_id": comp["comp_id"], "rel_type": "参考手册", "end_type": "Manual", "end_id": man["man_id"]})

    # 飞机 -> 维修站 / MEL保留
    for i, ac in enumerate(aircraft):
        st = STATIONS[i % len(STATIONS)]
        relations.append({"start_type": "Aircraft", "start_id": ac["acid"], "rel_type": "驻场", "end_type": "Station", "end_id": f"ST_{(i % len(STATIONS)) + 1:03d}"})
        mel = mel_deferrals[i % len(mel_deferrals)]
        relations.append({"start_type": "Aircraft", "start_id": ac["acid"], "rel_type": "有保留项", "end_type": "MELDeferral", "end_id": mel["mel_id"]})

    # 人员 -> 维修站
    for i, per in enumerate(personnel):
        st = STATIONS[i % len(STATIONS)]
        relations.append({"start_type": "Personnel", "start_id": per["per_id"], "rel_type": "所属站点", "end_type": "Station", "end_id": f"ST_{(i % len(STATIONS)) + 1:03d}"})

    # MEL保留 -> 故障代码
    for i, mel in enumerate(mel_deferrals[:200]):
        fc = fault_codes[i % len(fault_codes)]
        relations.append({"start_type": "MELDeferral", "start_id": mel["mel_id"], "rel_type": "关联故障", "end_type": "FaultCode", "end_id": fc["fc_id"]})

    # 航材 -> 部件
    for i, sp in enumerate(spare_parts):
        comp = comps[i % len(comps)]
        relations.append({"start_type": "SparePart", "start_id": sp["sp_id"], "rel_type": "替换", "end_type": "Component", "end_id": comp["comp_id"]})

    data["relations"] = relations

    # 统计
    summary = {k: len(v) for k, v in data.items()}
    summary["TOTAL"] = sum(len(v) for v in data.values())
    return data, summary

if __name__ == "__main__":
    data, summary = gen()
    with open(os.path.join(OUT, "entities.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print("SUMMARY:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
