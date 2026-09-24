"""seed_schema_ontology：国航旅客运输域 演示数据 + 数据源本体 播种（幂等）。

方案：doc/智能体/多智能体/多智能体DataAgent·本体驱动NL2SQL.md §7。

落库分离（业务数据不入平台实体库）：
- 业务数据 → 独立 PG 业务库（默认）：与平台主库同一 PG 服务器自动新建
  biz_aviation 库（--dsn 省略时按 DATABASE_URL 自动推导，需 CREATEDB 权限）；
  --target sqlite 降为文件库 backend/data/biz_aviation.db（CI 兜底）；
- 平台主库 → 只存本体映射（类别/表/字段/关系），类别上 datasource_dialect +
  datasource_dsn 指向独立实例，NL2SQL 只读执行器按 DSN 连业务实例；
- --dsn 若指向平台主库（与 DATABASE_URL 同 host/dbname）会被拒绝，防误灌。

用法（backend 目录，conda 环境 ontology）：
    python scripts/seed_schema_ontology.py                      # 默认：自动建 biz_aviation PG 业务库并灌数
    python scripts/seed_schema_ontology.py --dsn postgresql://...  # 显式指定独立 PG 业务库
    python scripts/seed_schema_ontology.py --target sqlite      # CI 兜底：sqlite 文件库
    python scripts/seed_schema_ontology.py --update             # 刷新本体别名/枚举（不重灌数）
    python scripts/seed_schema_ontology.py --drop               # 清独立实例本域表 + 平台库本体类别

要点：
- 确定性伪随机（seed=42）可复现；里程流水与 members.miles 严格对账；
- 只碰本域表（CREATE IF NOT EXISTS + 空表才灌），--drop 也只删本域表。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import random
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SEED = 42
TODAY = dt.date.today()
BIZ_SQLITE = Path(__file__).resolve().parents[1] / "data" / "biz_aviation.db"
CATEGORY_NAME = "数据源本体·国航旅客运输"


def _pg_key(dsn: str) -> tuple:
    """PG DSN → (host, dbname) 指纹，用于识别「--dsn 指向平台主库」。"""
    from urllib.parse import unquote, urlsplit
    u = urlsplit(dsn)
    return ((u.hostname or "").lower(), unquote(u.path or "").lstrip("/").lower())


PG_BIZ_DBNAME = "biz_aviation"


def _derive_biz_dsn(platform_dsn: str) -> str:
    """平台 DATABASE_URL → 同服务器独立业务库 dsn（换 dbname=biz_aviation，
    scheme 归一化为 asyncpg 原生 postgresql://）。"""
    from urllib.parse import urlsplit, urlunsplit
    u = urlsplit(platform_dsn)
    u = u._replace(scheme="postgresql", path=f"/{PG_BIZ_DBNAME}")
    return urlunsplit(u)


async def _ensure_pg_database(biz_dsn: str) -> str:
    """独立业务库不存在则自动创建（同一 PG 服务器，需 CREATEDB 权限）。返回可连 dsn。"""
    from urllib.parse import urlsplit, urlunsplit
    import asyncpg
    u = urlsplit(biz_dsn)
    dbname = u.path.lstrip("/")
    admin_dsn = urlunsplit(u._replace(path="/postgres"))
    conn = await asyncpg.connect(admin_dsn)
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{dbname}" ENCODING \'UTF8\'')
            print(f"[seed] 已创建独立业务库 {dbname}（与平台主库同服务器不同库）")
    finally:
        await conn.close()
    if biz_dsn.startswith("postgresql+asyncpg://"):
        biz_dsn = "postgresql://" + biz_dsn[len("postgresql+asyncpg://"):]
    return biz_dsn

# ────────────────────── 表规格（DDL 列序 = 数据元组序 = 本体字段序） ──────────────────────
# col 规格：C(code, 中文名, data_type, alias, enums, unit, required, description)

AIRPORTS_DDL = """CREATE TABLE IF NOT EXISTS airports (
  airport_code TEXT PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL,
  is_international INTEGER NOT NULL DEFAULT 0)"""
FLIGHTS_DDL = """CREATE TABLE IF NOT EXISTS flights (
  flight_no TEXT PRIMARY KEY, dep_airport TEXT NOT NULL, arr_airport TEXT NOT NULL,
  dep_datetime TEXT NOT NULL, arr_datetime TEXT NOT NULL, aircraft_type TEXT NOT NULL,
  dep_gate TEXT, status TEXT NOT NULL, delay_min INTEGER NOT NULL DEFAULT 0)"""
MEMBERS_DDL = """CREATE TABLE IF NOT EXISTS members (
  member_id TEXT PRIMARY KEY, name TEXT NOT NULL, level TEXT NOT NULL,
  miles INTEGER NOT NULL DEFAULT 0, join_date TEXT NOT NULL)"""
PASSENGERS_DDL = """CREATE TABLE IF NOT EXISTS passengers (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, gender TEXT NOT NULL,
  phone TEXT NOT NULL, member_id TEXT)"""
TICKETS_DDL = """CREATE TABLE IF NOT EXISTS tickets (
  ticket_no TEXT PRIMARY KEY, passenger_id TEXT NOT NULL, flight_no TEXT NOT NULL,
  cabin TEXT NOT NULL, fare REAL NOT NULL, seat_no TEXT NOT NULL, status TEXT NOT NULL)"""
BAGGAGE_DDL = """CREATE TABLE IF NOT EXISTS baggage (
  bag_tag TEXT PRIMARY KEY, ticket_no TEXT NOT NULL, weight_kg REAL NOT NULL,
  status TEXT NOT NULL, special TEXT)"""
CREW_DDL = """CREATE TABLE IF NOT EXISTS crew_members (
  crew_id TEXT PRIMARY KEY, name TEXT NOT NULL, gender TEXT NOT NULL,
  role TEXT NOT NULL, base_airport TEXT NOT NULL, hire_date TEXT NOT NULL)"""
CREW_ASSIGN_DDL = """CREATE TABLE IF NOT EXISTS crew_assignments (
  id INTEGER PRIMARY KEY, flight_no TEXT NOT NULL, crew_id TEXT NOT NULL,
  duty_role TEXT NOT NULL)"""
SERVICE_DDL = """CREATE TABLE IF NOT EXISTS service_requests (
  request_no TEXT PRIMARY KEY, passenger_id TEXT, flight_no TEXT,
  type TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL,
  satisfaction INTEGER, created_at TEXT NOT NULL, closed_at TEXT)"""
MILEAGE_DDL = """CREATE TABLE IF NOT EXISTS mileage_records (
  id INTEGER PRIMARY KEY, member_id TEXT NOT NULL, ticket_no TEXT,
  type TEXT NOT NULL, miles INTEGER NOT NULL, created_at TEXT NOT NULL)"""
COMP_DDL = """CREATE TABLE IF NOT EXISTS compensations (
  id INTEGER PRIMARY KEY, flight_no TEXT NOT NULL, passenger_id TEXT NOT NULL,
  reason TEXT NOT NULL, amount REAL NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL)"""

TABLE_DDL = [
    ("airports", AIRPORTS_DDL), ("flights", FLIGHTS_DDL), ("members", MEMBERS_DDL),
    ("passengers", PASSENGERS_DDL), ("tickets", TICKETS_DDL), ("baggage", BAGGAGE_DDL),
    ("crew_members", CREW_DDL), ("crew_assignments", CREW_ASSIGN_DDL),
    ("service_requests", SERVICE_DDL), ("mileage_records", MILEAGE_DDL),
    ("compensations", COMP_DDL),
]

# 关系：15 条边 (编码, 关系名, 别名, 反向名, source, target, join)
# 编码 rel_<src>_<tgt>[_<语义>]：该类别内唯一（同表多边用语义后缀区分）
RELATIONS = [
    ("rel_flights_tickets", "航班售出客票", "售出,承运", "客票所属航班", "flights", "tickets",
     [{"left": "flight_no", "right": "flight_no"}]),
    ("rel_passengers_tickets", "旅客购买客票", "购票,客票", "客票所属旅客", "passengers", "tickets",
     [{"left": "id", "right": "passenger_id"}]),
    ("rel_members_passengers", "会员关联旅客", "注册旅客", "旅客关联会员", "members", "passengers",
     [{"left": "member_id", "right": "member_id"}]),
    ("rel_tickets_baggage", "客票托运行李", "托运,挂行李", "行李所属客票", "tickets", "baggage",
     [{"left": "ticket_no", "right": "ticket_no"}]),
    ("rel_airports_flights_dep", "航班起飞机场", "出发机场,始发", "起飞航班", "airports", "flights",
     [{"left": "airport_code", "right": "dep_airport"}]),
    ("rel_airports_flights_arr", "航班到达机场", "目的机场,抵达", "到达航班", "airports", "flights",
     [{"left": "airport_code", "right": "arr_airport"}]),
    ("rel_flights_crew_assignments", "航班排班机组", "执飞安排,排班", "排班所属航班", "flights", "crew_assignments",
     [{"left": "flight_no", "right": "flight_no"}]),
    ("rel_crew_members_crew_assignments", "机组执飞航段", "执飞,飞行", "执飞机组", "crew_members", "crew_assignments",
     [{"left": "crew_id", "right": "crew_id"}]),
    ("rel_airports_crew_members", "机场驻地机组", "驻地,基地", "驻地机场", "airports", "crew_members",
     [{"left": "airport_code", "right": "base_airport"}]),
    ("rel_passengers_service_requests", "旅客发起服务请求", "提交工单,发起请求", "请求所属旅客", "passengers", "service_requests",
     [{"left": "id", "right": "passenger_id"}]),
    ("rel_flights_service_requests", "航班产生服务请求", "航班工单", "请求关联航班", "flights", "service_requests",
     [{"left": "flight_no", "right": "flight_no"}]),
    ("rel_members_mileage_records", "会员产生里程流水", "里程记录,积分记录", "流水所属会员", "members", "mileage_records",
     [{"left": "member_id", "right": "member_id"}]),
    ("rel_tickets_mileage_records", "客票关联里程流水", "乘机累积", "流水关联客票", "tickets", "mileage_records",
     [{"left": "ticket_no", "right": "ticket_no"}]),
    ("rel_flights_compensations", "航班产生补偿", "航班赔付", "补偿所属航班", "flights", "compensations",
     [{"left": "flight_no", "right": "flight_no"}]),
    ("rel_passengers_compensations", "旅客获得补偿", "旅客赔付", "补偿所属旅客", "passengers", "compensations",
     [{"left": "id", "right": "passenger_id"}]),
]


def C(code, name, dtype, alias="", enums=None, unit="", required=1, desc=""):
    return {"code": code, "name": name, "type": dtype, "alias": alias,
            "enums": enums or [], "unit": unit, "required": required, "desc": desc}


TABLE_SPECS = [
    {"code": "airports", "name": "机场表", "display_name": "机场", "alias": "机场,airport,通航点",
     "desc": "国航通航机场维表：三字码/城市/国际属性", "ddl": AIRPORTS_DDL,
     "cols": [
         C("airport_code", "三字码", "string", "机场代码,三字码", required=1, desc="IATA 三字码，如 PEK"),
         C("name", "机场名称", "string", "机场名", required=1),
         C("city", "城市", "string", "所在城市", required=1),
         C("is_international", "是否国际", "integer", "国际机场", enums=["0", "1"], required=1, desc="1=国际机场"),
     ]},
    {"code": "flights", "name": "航班表", "display_name": "航班动态", "alias": "航班,flight,航段,班次",
     "desc": "国航航班运行主表：起降机场/计划时刻/机型/登机口/状态/延误", "ddl": FLIGHTS_DDL,
     "cols": [
         C("flight_no", "航班号", "string", "航班,机号", required=1, desc="CA+数字，如 CA1501"),
         C("dep_airport", "起飞机场", "string", "出发机场,始发机场", required=1),
         C("arr_airport", "到达机场", "string", "目的机场,抵达机场", required=1),
         C("dep_datetime", "计划起飞时间", "timestamp", "起飞时间,离港时间", required=1),
         C("arr_datetime", "计划到达时间", "timestamp", "到达时间,到港时间", required=1),
         C("aircraft_type", "机型", "string", "飞机型号", enums=["A321", "A330", "A350", "B737", "B787", "C919"], required=1),
         C("dep_gate", "登机口", "string", "登机门", required=0),
         C("status", "航班状态", "string", "航班动态", enums=["计划", "起飞", "到达", "取消"], required=1),
         C("delay_min", "延误分钟", "integer", "延误时长,延误时间", unit="分钟", required=1),
     ]},
    {"code": "members", "name": "会员表", "display_name": "凤凰知音会员", "alias": "会员,常旅客,凤凰知音,member",
     "desc": "凤凰知音常旅客会员：等级/里程余额/入会日期", "ddl": MEMBERS_DDL,
     "cols": [
         C("member_id", "会员号", "string", "会员ID,会员编号", required=1),
         C("name", "姓名", "string", "会员姓名", required=1),
         C("level", "会员等级", "string", "等级,卡级", enums=["普通卡", "银卡", "金卡", "白金卡", "终身白金卡"], required=1),
         C("miles", "里程余额", "integer", "里程,可用里程", unit="公里", required=1),
         C("join_date", "入会日期", "date", "加入日期", required=1),
     ]},
    {"code": "passengers", "name": "旅客表", "display_name": "旅客", "alias": "旅客,乘客,乘机人,passenger",
     "desc": "乘机旅客：联系方式脱敏；member_id 可空（未注册会员）", "ddl": PASSENGERS_DDL,
     "cols": [
         C("id", "旅客ID", "string", "旅客编号", required=1),
         C("name", "姓名", "string", "旅客姓名", required=1),
         C("gender", "性别", "string", "", enums=["男", "女"], required=1),
         C("phone", "联系电话", "string", "手机号,电话", required=1, desc="脱敏：138****1234"),
         C("member_id", "会员号", "string", "会员ID", required=0, desc="可空：未注册凤凰知音"),
     ]},
    {"code": "tickets", "name": "客票表", "display_name": "客票航段", "alias": "客票,机票,票,ticket",
     "desc": "客票航段 = 旅客×航班 多对多桥；舱位/票价/座位/状态", "ddl": TICKETS_DDL,
     "cols": [
         C("ticket_no", "票号", "string", "客票号,电子票号", required=1, desc="999- 开头"),
         C("passenger_id", "旅客ID", "string", "旅客编号", required=1),
         C("flight_no", "航班号", "string", "航班", required=1),
         C("cabin", "舱位", "string", "舱位等级,舱型", enums=["头等舱", "公务舱", "高端经济舱", "经济舱"], required=1),
         C("fare", "票价", "decimal", "票款,价格", unit="元", required=1),
         C("seat_no", "座位号", "string", "座位", required=1),
         C("status", "客票状态", "string", "票状态", enums=["已出票", "已值机", "已登机", "已成行", "退票"], required=1),
     ]},
    {"code": "baggage", "name": "行李表", "display_name": "托运行李", "alias": "行李,行李牌,baggage",
     "desc": "托运行李：挂客票航段；含异常滞留状态", "ddl": BAGGAGE_DDL,
     "cols": [
         C("bag_tag", "行李牌号", "string", "牌号,行李标签", required=1, desc="999 开头"),
         C("ticket_no", "票号", "string", "客票号", required=1),
         C("weight_kg", "重量", "decimal", "行李重量", unit="公斤", required=1),
         C("status", "行李状态", "string", "行李动态", enums=["已托运", "已装机", "运输中", "已到达", "已提取", "异常滞留"], required=1),
         C("special", "特殊标记", "string", "特殊行李", enums=["超规", "易碎"], required=0, desc="可空"),
     ]},
    {"code": "crew_members", "name": "机组表", "display_name": "机组", "alias": "机组,机组成员,乘务,飞行员,crew",
     "desc": "机组名册：机长/副驾驶/乘务长/乘务员，驻地机场", "ddl": CREW_DDL,
     "cols": [
         C("crew_id", "工号", "string", "员工号", required=1),
         C("name", "姓名", "string", "机组姓名", required=1),
         C("gender", "性别", "string", "", enums=["男", "女"], required=1),
         C("role", "岗位", "string", "角色,职务", enums=["机长", "副驾驶", "乘务长", "乘务员"], required=1),
         C("base_airport", "驻地机场", "string", "基地", required=1),
         C("hire_date", "入职日期", "date", "参加工作", required=1),
     ]},
    {"code": "crew_assignments", "name": "排班表", "display_name": "机组排班", "alias": "排班,执飞,航班排班",
     "desc": "航班×机组 多对多桥：该航段执飞岗位", "ddl": CREW_ASSIGN_DDL,
     "cols": [
         C("id", "排班ID", "integer", "", required=1),
         C("flight_no", "航班号", "string", "航班", required=1),
         C("crew_id", "工号", "string", "机组工号", required=1),
         C("duty_role", "执飞岗位", "string", "岗位", enums=["机长", "副驾驶", "乘务长", "乘务员"], required=1),
     ]},
    {"code": "service_requests", "name": "服务请求表", "display_name": "服务请求/投诉", "alias": "服务请求,投诉,工单,服务单",
     "desc": "服务请求/投诉工单：旅客与航班均可空（匿名/非航班类）", "ddl": SERVICE_DDL,
     "cols": [
         C("request_no", "请求编号", "string", "工单号,单号", required=1),
         C("passenger_id", "旅客ID", "string", "旅客", required=0, desc="可空：匿名/代理提交"),
         C("flight_no", "航班号", "string", "关联航班", required=0, desc="可空：非航班类咨询"),
         C("type", "类型", "string", "请求类型", enums=["餐食", "住宿", "接送机", "补偿", "票务", "投诉"], required=1),
         C("channel", "渠道", "string", "来源渠道", enums=["App", "小程序", "柜台", "电话"], required=1),
         C("status", "状态", "string", "处理状态", enums=["待受理", "处理中", "已办结"], required=1),
         C("satisfaction", "满意度", "integer", "评价分数", enums=["1", "2", "3", "4", "5"], unit="分", required=0, desc="1-5，办结后评价"),
         C("created_at", "提交时间", "timestamp", "创建时间", required=1),
         C("closed_at", "办结时间", "timestamp", "关闭时间", required=0),
     ]},
    {"code": "mileage_records", "name": "里程流水表", "display_name": "里程流水", "alias": "里程,里程记录,积分流水",
     "desc": "里程流水：累积为正/兑换过期为负，可对账 members.miles", "ddl": MILEAGE_DDL,
     "cols": [
         C("id", "流水ID", "integer", "", required=1),
         C("member_id", "会员号", "string", "会员ID", required=1),
         C("ticket_no", "票号", "string", "客票号", required=0, desc="可空：活动赠送等非乘机累积"),
         C("type", "类型", "string", "流水类型", enums=["乘机累积", "活动赠送", "兑换", "过期调整"], required=1),
         C("miles", "里程变动", "integer", "变动里程", unit="公里", required=1, desc="正=累积 负=兑换/过期"),
         C("created_at", "发生时间", "timestamp", "流水时间", required=1),
     ]},
    {"code": "compensations", "name": "延误补偿表", "display_name": "延误补偿", "alias": "补偿,赔付,延误补偿",
     "desc": "不正常航班旅客补偿：原因/金额/发放状态", "ddl": COMP_DDL,
     "cols": [
         C("id", "补偿ID", "integer", "", required=1),
         C("flight_no", "航班号", "string", "航班", required=1),
         C("passenger_id", "旅客ID", "string", "旅客", required=1),
         C("reason", "原因", "string", "补偿原因", enums=["延误4小时以上", "航班取消", "超售"], required=1),
         C("amount", "金额", "decimal", "补偿金额", unit="元", required=1),
         C("status", "发放状态", "string", "状态", enums=["应发", "已发", "已冲正"], required=1),
         C("created_at", "登记时间", "timestamp", "创建时间", required=1),
     ]},
]

# ────────────────────── 确定性数据生成（seed=42，可复现） ──────────────────────

SURNAMES = "王李张刘陈杨赵黄周吴徐孙马朱胡郭何林罗高郑梁谢宋唐许韩冯邓曹彭"
GIVEN = "伟芳娜敏静丽强磊军洋勇艳杰娟涛明超霞平刚桂华建文军辉丽丹宇宏斌鹏飞晓婷波欣怡浩"
AIRPORT_POOL = [  # (三字码, 名称, 城市, 国际)
    ("PEK", "北京首都国际机场", "北京", 1), ("PKX", "北京大兴国际机场", "北京", 1),
    ("SHA", "上海虹桥国际机场", "上海", 1), ("PVG", "上海浦东国际机场", "上海", 1),
    ("CAN", "广州白云国际机场", "广州", 1), ("SZX", "深圳宝安国际机场", "深圳", 1),
    ("CTU", "成都双流国际机场", "成都", 1), ("TFU", "成都天府国际机场", "成都", 1),
    ("CKG", "重庆江北国际机场", "重庆", 1), ("HGH", "杭州萧山国际机场", "杭州", 1),
    ("XIY", "西安咸阳国际机场", "西安", 1), ("WUH", "武汉天河国际机场", "武汉", 1),
    ("NKG", "南京禄口国际机场", "南京", 1), ("TAO", "青岛胶东国际机场", "青岛", 1),
    ("CSX", "长沙黄花国际机场", "长沙", 1), ("XMN", "厦门高崎国际机场", "厦门", 1),
    ("KMG", "昆明长水国际机场", "昆明", 1), ("URC", "乌鲁木齐地窝堡国际机场", "乌鲁木齐", 1),
    ("HRB", "哈尔滨太平国际机场", "哈尔滨", 1), ("SYX", "三亚凤凰国际机场", "三亚", 1),
    ("HAK", "海口美兰国际机场", "海口", 1), ("TNA", "济南遥墙国际机场", "济南", 0),
    ("HET", "呼和浩特白塔国际机场", "呼和浩特", 1), ("LHW", "兰州中川国际机场", "兰州", 0),
    ("NNG", "南宁吴圩国际机场", "南宁", 0),
]
LEVELS = [("普通卡", 0.42), ("银卡", 0.28), ("金卡", 0.18), ("白金卡", 0.09), ("终身白金卡", 0.03)]
CABINS = [("经济舱", 0.72, (600, 1800)), ("高端经济舱", 0.10, (1500, 2600)),
          ("公务舱", 0.12, (3000, 6500)), ("头等舱", 0.06, (6000, 12000))]
CREW_ROLES = [("机长", 30), ("副驾驶", 30), ("乘务长", 20), ("乘务员", 40)]
REQ_TYPES = [("餐食", 0.18), ("住宿", 0.14), ("接送机", 0.22), ("补偿", 0.16), ("票务", 0.18), ("投诉", 0.12)]


def _cn_name(rnd) -> str:
    return rnd.choice(SURNAMES) + rnd.choice(GIVEN) + (rnd.choice(GIVEN) if rnd.random() < 0.35 else "")


def _ts(day: dt.date, hour: int, minute: int) -> str:
    return f"{day.isoformat()} {hour:02d}:{minute:02d}:00"


def _pick_weighted(rnd, pairs):
    x, acc = rnd.random(), 0.0
    for item, w in pairs:
        acc += w
        if x <= acc:
            return item
    return pairs[-1][0]


def gen_all(rnd: random.Random) -> dict:
    """生成全部业务数据（互相引用一致；里程流水与 members.miles 对账）。"""
    now = dt.datetime.now()

    airports = [(c, n, city, intl) for c, n, city, intl in AIRPORT_POOL]

    flights = []
    for i in range(200):
        day = TODAY - dt.timedelta(days=6 - i // 29)          # 近 7 天铺开
        dep = rnd.choice(airports)
        arr = rnd.choice([a for a in airports if a[0] != dep[0]])
        d_h, d_m = rnd.randint(6, 21), rnd.choice((0, 10, 15, 20, 30, 40, 45, 50))
        dur = rnd.randint(95, 230)
        dep_dt = dt.datetime.combine(day, dt.time(d_h, d_m))
        arr_dt = dep_dt + dt.timedelta(minutes=dur)
        delay = 0 if rnd.random() < 0.45 else rnd.choice((5, 10, 15, 18, 22, 25, 30, 35, 45, 60, 75, 90, 120, 150, 180))
        if arr_dt < now:
            status = "取消" if rnd.random() < 0.03 else ("到达" if rnd.random() < 0.9 else "起飞")
        elif dep_dt < now:
            status = "起飞"
        else:
            status = "计划"
        flights.append((f"CA{1501 + i}", dep[0], arr[0], dep_dt.isoformat(sep=" "),
                        arr_dt.isoformat(sep=" "), rnd.choice(("A321", "A330", "A350", "B737", "B787", "C919")),
                        f"{rnd.randint(1, 30):02d}", status, delay))

    members = []
    for i in range(300):
        lvl = _pick_weighted(rnd, LEVELS)
        members.append((f"MP{100000 + i * 7}", _cn_name(rnd), lvl, 0,
                        (TODAY - dt.timedelta(days=rnd.randint(100, 3000))).isoformat()))

    passenger_ids, passengers = [], []
    for i in range(600):
        pid = f"P{i + 1:06d}"
        mid = rnd.choice(members)[0] if rnd.random() < 0.4 else None
        passenger_ids.append((pid, mid))
        passengers.append((pid, _cn_name(rnd), rnd.choice(("男", "女")),
                           f"1{rnd.randint(3, 9)}{rnd.randint(0, 9)}****{rnd.randint(1000, 9999)}", mid))

    tickets = []
    fare_range = {c: r for c, _, r in CABINS}
    for i in range(1800):
        pid, _ = rnd.choice(passenger_ids)
        fl = rnd.choice(flights)
        cabin = rnd.choices([c for c, _, _ in CABINS], weights=[w for _, w, _ in CABINS])[0]
        lo, hi = fare_range[cabin]
        fare = round(rnd.uniform(lo, hi), 0)
        if fl[7] == "到达":
            status = rnd.choice(("已成行", "已成行", "已成行", "退票"))
        elif fl[7] == "取消":
            status = rnd.choice(("退票", "已出票"))
        elif fl[7] == "起飞":
            status = rnd.choice(("已登机", "已成行", "已值机"))
        else:                                   # 计划：未来航班
            status = rnd.choice(("已出票", "已出票", "已值机", "退票"))
        tickets.append((f"999-{99990000 + i}", pid, fl[0], cabin, fare,
                        f"{rnd.randint(1, 40)}{rnd.choice('ABCDEFK')}", status))

    baggage = []
    tagged = [t for t in tickets if t[6] not in ("退票",)]
    for i in range(1300):
        tk = rnd.choice(tagged)
        st = "异常滞留" if rnd.random() < 0.02 else rnd.choice(
            ("已托运", "已装机", "运输中", "已到达", "已提取", "已提取"))
        baggage.append((f"999{1000000 + i}", tk[0], round(rnd.uniform(5, 32), 1), st,
                        rnd.choice((None, None, None, "超规", "易碎"))))

    crew, crews_by_role = [], {}
    n = 0
    for role, cnt in CREW_ROLES:
        for _ in range(cnt):
            cid = f"CR{n + 1:04d}"
            crew.append((cid, _cn_name(rnd), rnd.choice(("男", "女")), role,
                         rnd.choice(airports)[0],
                         (TODAY - dt.timedelta(days=rnd.randint(400, 9000))).isoformat()))
            crews_by_role.setdefault(role, []).append(cid)
            n += 1

    assigns, aid = [], 1
    for fl in flights:
        for role in ("机长", "副驾驶", "乘务长", "乘务员"):
            for _ in range(1 if role != "乘务员" else 2):
                assigns.append((aid, fl[0], rnd.choice(crews_by_role[role]), role))
                aid += 1

    reqs = []
    for i in range(450):
        pid = rnd.choice(passenger_ids)[0] if rnd.random() < 0.7 else None
        fl = rnd.choice(flights)[0] if rnd.random() < 0.6 else None
        day = TODAY - dt.timedelta(days=rnd.randint(0, 6))
        created = _ts(day, rnd.randint(7, 22), rnd.choice((0, 15, 30, 45)))
        status = _pick_weighted(rnd, [("已办结", 0.62), ("处理中", 0.23), ("待受理", 0.15)])
        sat = rnd.randint(1, 5) if status == "已办结" and rnd.random() < 0.7 else None
        closed = _ts(min(day + dt.timedelta(days=rnd.randint(1, 3)), TODAY),
                     rnd.randint(9, 20), 0) if status == "已办结" else None
        reqs.append((f"SR{2026}{i + 1:05d}", pid, fl,
                     _pick_weighted(rnd, REQ_TYPES), rnd.choice(("App", "小程序", "柜台", "电话")),
                     status, sat, created, closed))

    # 里程流水：乘机累积（挂已成行客票，里程≈票价×0.6 取整到 10）+ 活动赠送 − 兑换/过期
    mileage, mid2bal, x = [], {}, 1
    flown = [t for t in tickets if t[6] == "已成行"]
    for i in range(1500):
        mid = rnd.choice(members)[0]
        tk = rnd.choice(flown) if rnd.random() < 0.85 else None
        miles = int(tk[4] * 0.6 // 10 * 10) if tk else rnd.choice((500, 800, 1000, 2000, 3000))
        day = TODAY - dt.timedelta(days=rnd.randint(0, 6))
        mileage.append((x, mid, tk[0] if tk else None, "乘机累积" if tk else "活动赠送",
                        miles, _ts(day, rnd.randint(0, 23), rnd.randint(0, 59))))
        mid2bal[mid] = mid2bal.get(mid, 0) + miles
        x += 1
    for i in range(700):
        mid = rnd.choice(members)[0]
        miles = -rnd.choice((2000, 5000, 8000, 10000, 15000, 20000))
        day = TODAY - dt.timedelta(days=rnd.randint(0, 6))
        mileage.append((x, mid, None, rnd.choice(("兑换", "过期调整")), miles,
                        _ts(day, rnd.randint(0, 23), rnd.randint(0, 59))))
        mid2bal[mid] = mid2bal.get(mid, 0) + miles
        x += 1
    members = [(m[0], m[1], m[2], 30000 + mid2bal.get(m[0], 0), m[4]) for m in members]

    comp, cid2 = [], 1
    bad = [fl for fl in flights if fl[8] >= 120 or fl[7] == "取消"]
    for i in range(350):
        fl = rnd.choice(bad)
        tk = rnd.choice([t for t in tickets if t[2] == fl[0]] or tickets)
        reason = "航班取消" if fl[7] == "取消" else (
            "超售" if rnd.random() < 0.15 else "延误4小时以上")
        comp.append((cid2, fl[0], tk[1], reason,
                     float(rnd.choice((200, 300, 400, 500, 600, 800))),
                     _pick_weighted(rnd, [("已发", 0.72), ("应发", 0.2), ("已冲正", 0.08)]),
                     _ts(min(TODAY - dt.timedelta(days=rnd.randint(0, 5)), TODAY),
                         rnd.randint(8, 21), rnd.choice((0, 30))))
                     )
        cid2 += 1

    return {"airports": airports, "flights": flights, "members": members,
            "passengers": passengers, "tickets": tickets, "baggage": baggage,
            "crew_members": crew, "crew_assignments": assigns,
            "service_requests": reqs, "mileage_records": mileage,
            "compensations": comp}


# ────────────────────── 业务实例（独立库）建表灌数 ──────────────────────

async def seed_business(target: str, dsn: str = "", drop: bool = False) -> int:
    """独立业务实例 建表 + 空表才灌。返回灌入总行数。

    target=sqlite → BIZ_SQLITE 文件库（CI 兜底）；target=pg（默认）→ 独立 PG 业务库
    （dsn 省略时按 DATABASE_URL 自动推导同服务器 biz_aviation 库并自动建库；
    禁止指向平台主库）。
    """
    rnd = random.Random(SEED)
    data = gen_all(rnd)

    if target == "sqlite":
        import aiosqlite
        BIZ_SQLITE.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(BIZ_SQLITE))
        ph = lambda n: ",".join("?" * n)
    else:
        from config import settings
        if not dsn:
            dsn = _derive_biz_dsn(settings.DATABASE_URL)
        if _pg_key(dsn) == _pg_key(settings.DATABASE_URL):
            raise SystemExit(f"[seed] 拒绝执行：--dsn 与平台主库（DATABASE_URL）同库，"
                             f"业务数据须落独立实例")
        pg_dsn = await _ensure_pg_database(dsn)
        import asyncpg
        conn = await asyncpg.connect(pg_dsn)
        ph = lambda n: ",".join(f"${i + 1}" for i in range(n))

    try:
        total = 0
        for table, ddl in TABLE_DDL:
            await conn.execute(ddl)
        if drop:
            for table, _ in reversed(TABLE_DDL):
                await conn.execute(f"DROP TABLE IF EXISTS {table}")
            await conn.commit() if target == "sqlite" else None
            print(f"[seed] 已清理 {len(TABLE_DDL)} 张本域业务表（{target} 独立实例）")
            return 0
        for table, _ in TABLE_DDL:
            rows = data[table]
            cnt = await conn.fetchval(f"SELECT COUNT(*) FROM {table}") if target == "pg" \
                else (await (await conn.execute(f"SELECT COUNT(*) FROM {table}")).fetchone())[0]
            if cnt:
                print(f"[seed] {table}: 已有 {cnt} 行，跳过")
                continue
            await conn.executemany(f"INSERT INTO {table} VALUES ({ph(len(rows[0]))})", rows)
            total += len(rows)
            print(f"[seed] {table}: 灌入 {len(rows)} 行")
        await conn.commit() if target == "sqlite" else None
        return total
    finally:
        await conn.close()


# ────────────────────── 本体播种（写平台库，幂等 / --update / --drop） ──────────────────────

async def seed_ontology(target: str, dsn: str = "", update_only: bool = False,
                        drop: bool = False):
    """本体映射写平台主库；datasource_dialect/dsn 指向独立业务实例。"""
    import json as _json
    from database import async_session
    from models import (Ontology, OntologyAttribute, OntologyCategory,
                        OntologyRelation, OntologyRelationConstraint)

    if target == "sqlite":
        dialect = "sqlite"
        dsn = f"file:{BIZ_SQLITE.absolute().as_posix()}?mode=ro"
    else:
        dialect = "postgres"           # dsn = 用户显式给的独立 PG 业务库
        if dsn.startswith("postgresql+asyncpg://"):
            dsn = "postgresql://" + dsn[len("postgresql+asyncpg://"):]

    async with async_session() as db:
        cat = (await db.execute(
            select(OntologyCategory)
            .where(OntologyCategory.name == CATEGORY_NAME))).scalars().first()
        if drop:
            if cat:
                from sqlalchemy import delete
                ont_ids = [o.id for o in (await db.execute(
                    select(Ontology)
                    .where(Ontology.category_id == cat.id))).scalars().all()]
                rel_ids = [r.id for r in (await db.execute(
                    select(OntologyRelation)
                    .where(OntologyRelation.category_id == cat.id))).scalars().all()]
                if ont_ids:
                    await db.execute(delete(OntologyAttribute)
                                     .where(OntologyAttribute.ontology_id.in_(ont_ids)))
                    await db.execute(delete(Ontology)
                                     .where(Ontology.id.in_(ont_ids)))
                if rel_ids:
                    await db.execute(delete(OntologyRelationConstraint)
                                     .where(OntologyRelationConstraint.relation_id.in_(rel_ids)))
                    await db.execute(delete(OntologyRelation)
                                     .where(OntologyRelation.id.in_(rel_ids)))
                await db.delete(cat)
                await db.commit()
                print(f"[seed] 本体类别「{CATEGORY_NAME}」已清理")
            else:
                print("[seed] 本体类别不存在，无需清理")
            return

        if cat is None:
            cat = OntologyCategory(name=CATEGORY_NAME,
                                   description="NL2SQL 演示数据源：国航旅客运输全域"
                                               "（机场/航班/客票/旅客/行李/会员/机组/服务/里程/补偿）",
                                   datasource_dialect=dialect, datasource_dsn=dsn)
            db.add(cat)
            await db.flush()
        else:
            cat.datasource_dialect, cat.datasource_dsn = dialect, dsn
        print(f"[seed] 类别就绪：{CATEGORY_NAME}（dialect={dialect}, 业务实例={dsn}）")

        # 表 + 字段
        for spec in TABLE_SPECS:
            ont = (await db.execute(
                select(Ontology)
                .where(Ontology.category_id == cat.id, Ontology.code == spec["code"]))
            ).scalars().first()
            if ont is None:
                ont = Ontology(category_id=cat.id, name=spec["name"], code=spec["code"],
                               display_name=spec["display_name"], alias=spec["alias"],
                               description=spec["desc"])
                db.add(ont)
                await db.flush()
            else:
                ont.alias, ont.display_name, ont.description = (
                    spec["alias"], spec["display_name"], spec["desc"])
            existing = {a.code: a for a in (await db.execute(
                select(OntologyAttribute)
                .where(OntologyAttribute.ontology_id == ont.id))).scalars().all()}
            for idx, col in enumerate(spec["cols"]):
                attr = existing.get(col["code"])
                vals = {"name": col["name"], "data_type": col["type"], "alias": col["alias"],
                        "description": col["desc"], "enum_values": _json.dumps(col["enums"], ensure_ascii=False),
                        "unit": col["unit"], "is_required": col["required"], "sort_order": idx}
                if attr is None:
                    db.add(OntologyAttribute(ontology_id=ont.id, code=col["code"], **vals))
                else:
                    for k, v in vals.items():
                        setattr(attr, k, v)

        # 关系 + join_condition（幂等按 关系名+两表 匹配，缺则建）
        code2id = {o.code: o.id for o in (await db.execute(
            select(Ontology)
            .where(Ontology.category_id == cat.id))).scalars().all()}
        for code, name, alias, inverse, src, tgt, join in RELATIONS:
            rel = (await db.execute(
                select(OntologyRelation)
                .where(OntologyRelation.category_id == cat.id, OntologyRelation.name == name))
            ).scalars().first()
            if rel is None:
                rel = OntologyRelation(category_id=cat.id, name=name, code=code,
                                       alias=alias, inverse_name=inverse,
                                       description=f"{src}→{tgt}，join_condition 落库",
                                       cardinality="ONE_TO_MANY")
                db.add(rel)
                await db.flush()
            else:
                rel.alias, rel.inverse_name, rel.code = alias, inverse, code
            src_id, tgt_id = code2id[src], code2id[tgt]
            cons = (await db.execute(
                select(OntologyRelationConstraint)
                .where(OntologyRelationConstraint.relation_id == rel.id,
                       OntologyRelationConstraint.source_ontology_id == src_id,
                       OntologyRelationConstraint.target_ontology_id == tgt_id))
            ).scalars().first()
            jc = _json.dumps(join, ensure_ascii=False)
            if cons is None:
                db.add(OntologyRelationConstraint(
                    category_id=cat.id, relation_id=rel.id,
                    source_ontology_id=src_id, target_ontology_id=tgt_id,
                    join_condition=jc, description=f"{src}.{join[0]['left']} = {tgt}.{join[0]['right']}",
                    # 基数真源在约束层：ONE_TO_MANY → source 限 1（与 NL2SQL 1:N 判读一致）
                    source_max=1))
            else:
                cons.join_condition = jc
                cons.source_max = 1

        await db.commit()
        print(f"[seed] 本体播种完成：{len(TABLE_SPECS)} 表 / "
              f"{sum(len(s['cols']) for s in TABLE_SPECS)} 字段 / {len(RELATIONS)} 关系"
              + ("（--update 刷新）" if update_only else ""))


async def main():
    ap = argparse.ArgumentParser(description="国航旅客运输域 数据+本体 播种（业务数据入独立实例）")
    ap.add_argument("--target", choices=("pg", "sqlite"), default="pg",
                    help="独立业务实例类型：pg=独立 PG 业务库（默认，同平台服务器自动建"
                         " biz_aviation 库，--dsn 可显式指定其他实例）；"
                         "sqlite=文件库 backend/data/biz_aviation.db（CI 兜底）")
    ap.add_argument("--dsn", default="",
                    help="target=pg 时省略则按 DATABASE_URL 自动推导同服务器独立库；"
                         "显式给出时指向平台主库会被拒绝")
    ap.add_argument("--update", action="store_true", help="只刷新本体别名/枚举/关系，不重灌数")
    ap.add_argument("--drop", action="store_true", help="清理独立实例本域业务表与平台库本体类别后退出")
    args = ap.parse_args()
    if args.target == "pg" and not args.dsn:
        from config import settings
        args.dsn = _derive_biz_dsn(settings.DATABASE_URL)

    if args.drop:
        await seed_business(args.target, args.dsn, drop=True)
        await seed_ontology(args.target, args.dsn, drop=True)
        return
    total = await seed_business(args.target, args.dsn)
    if total or not args.update:
        print(f"[seed] 业务数据灌入 {total} 行（seed={SEED} 可复现，独立 {args.target} 实例）")
    await seed_ontology(args.target, args.dsn, update_only=args.update)
    print("[seed] 完成。平台库只存本体映射，业务数据在独立实例（dialect+dsn 指向），"
          "data_agent 取数链将自动启用 NL2SQL。")


if __name__ == "__main__":
    asyncio.run(main())
