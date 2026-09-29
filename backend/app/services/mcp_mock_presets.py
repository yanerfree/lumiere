"""MCP Mock 预置服务 —— 开箱即用的一批典型 MCP 服务端。

⚠ **一个服务里的工具必须围着同一件事转。** 这条是 2026-09-29 用户提的，
   而且是这份文件最容易退化的地方：按技术维度（认证/校验）分服务时，
   最省事的写法是每个服务塞几个 `echo` / `list_users` / `submit_order`
   凑数 —— 结果是「基础服务」里既能回显文字又能列用户还能查指标，
   **现实里没有任何一个 MCP 服务长这样**。拿它去演示接入，对面第一眼就知道是假的。

   所以现在每个预置服务都照着一个**真实存在的服务**来写（12306、麦当劳、
   高德、和风天气、快递100、GitHub、支付网关），工具名、参数、返回的 JSON
   都按那家的真实形状写。**技术维度是摊在这些服务上的，不是拿来分服务的**：

       传输方式    Streamable HTTP ×5 / SSE ×2
       认证方式    不认证 ×3 / Bearer Token ×2 / 自定义 API Key 请求头 ×2
       入参校验    严格 ×4 / 宽松 ×2 / 不校验 ×1
       异常场景    工具报错 / 响应很慢 / 返回空（都在「聚合支付」那个里）

   加/改预置时先看一眼 `tests/test_mcp_mock_presets.py` 的覆盖封样：
   四个维度每一档都必须还有人占着，少一档页面上看不出来 —— 那一类接入场景
   就是**悄悄地测不到了**。

⚠ 预置服务**不许删**（`builtin=True`，见 app/models/mcp_mock.py 的注释）：
   它每次启动都会按定义补回来，删掉的效果只是「重启前它不在、重启后又回来了」。
   不想用就停用。

⚠ **归属规则：锁着 = 平台的，解锁 = 你的。**
   预置服务默认锁上。锁着的时候内容跟着平台版本走（`PRESET_REV` 一变就整套刷新，
   改名、换工具、加参数都会跟着变）；**一旦有人解锁，平台立刻不再碰它的内容**，
   只把当前版本号记一笔当「这版我见过」。这样才不会出现「我昨天改好的工具，
   重启一次变回去了」—— 那种错不报错，只是安静地把人的活撤销掉。

⚠ 预置里的 token / API Key 是**写死的明文示例值**，故意的：它就是给人拿去
   连一下试试的。**别把它当凭证管理的样板** —— 真凭证走环境变量，不进这张表。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mcp_mock import McpMockLog, McpMockServer, McpMockTool

logger = logging.getLogger("mcp_mock")

# 预置内容的版本号。**改了下面任何一个服务/工具就把它往后挪一格** ——
# 不挪的话已经建出来的那些行不会刷新，页面上还是老样子，而代码里明明改了。
PRESET_REV = "2026-09-29.2"

# 预置服务里写死的示例凭证 —— 页面上直接显示，供人复制去连。
MCD_TOKEN = "mcd-demo-token-2026"
GITHUB_TOKEN = "ghp_LumiereMockToken2026DemoOnly"
AMAP_KEY_HEADER = "X-Amap-Key"
AMAP_KEY = "amap-demo-key-2026"
KD100_KEY_HEADER = "X-Api-Key"
KD100_KEY = "kd100-demo-key-2026"


def _p(name: str, ptype: str = "string", **kw: Any) -> dict:
    return {"name": name, "type": ptype, **kw}


PRESET_SERVERS: list[dict] = [
    # ── 1. 12306 · HTTP · 不认证 · 严格 ────────────────────────────────
    {
        "slug": "train-12306",
        "name": "12306 火车票",
        "description": "查票、下单、退票一条龙。Streamable HTTP + 不要认证 + 入参卡得死（日期格式、席别枚举都拦）。先拿它确认客户端连得上、工具列得出来。",
        "instructions": "12306 铁路购票（Mock）。可以查余票、查经停站、下单、查订单、退票。数据是假的，下单不会真扣钱。",
        "transport": "streamable-http",
        "auth_type": "none",
        "validate_mode": "strict",
        "tools": [
            {
                "name": "search_tickets",
                "description": "查某天某两站之间的余票",
                "params": [
                    _p("date", "string", required=True, description="乘车日期，YYYY-MM-DD", minLength=10, maxLength=10),
                    _p("from_station", "string", required=True, description="出发站中文名，如 北京南"),
                    _p("to_station", "string", required=True, description="到达站中文名，如 上海虹桥"),
                    _p("train_type", "string", description="车次类型", default="全部", enum=["全部", "G高铁", "D动车", "K普快"]),
                ],
                "success_data": {
                    "date": "2026-10-01", "from": "北京南", "to": "上海虹桥",
                    "trains": [
                        {"train_no": "G1", "depart": "07:00", "arrive": "11:29", "duration": "4小时29分",
                         "seats": {"商务座": 3, "一等座": "有", "二等座": "有"}, "price": {"二等座": 553.0}},
                        {"train_no": "G3", "depart": "09:00", "arrive": "13:28", "duration": "4小时28分",
                         "seats": {"商务座": 0, "一等座": 12, "二等座": "有"}, "price": {"二等座": 553.0}},
                        {"train_no": "D703", "depart": "10:05", "arrive": "20:41", "duration": "10小时36分",
                         "seats": {"一等座": 2, "二等座": 47, "动卧": "无"}, "price": {"二等座": 309.5}},
                    ],
                },
            },
            {
                "name": "get_train_stops",
                "description": "查一趟车的经停站和到发时刻",
                "params": [
                    _p("train_no", "string", required=True, description="车次，如 G1"),
                    _p("date", "string", description="乘车日期，YYYY-MM-DD"),
                ],
                "success_data": {
                    "train_no": "G1", "date": "2026-10-01",
                    "stops": [
                        {"no": 1, "station": "北京南", "arrive": "----", "depart": "07:00", "stop_minutes": 0},
                        {"no": 2, "station": "南京南", "arrive": "09:23", "depart": "09:25", "stop_minutes": 2},
                        {"no": 3, "station": "上海虹桥", "arrive": "11:29", "depart": "----", "stop_minutes": 0},
                    ],
                },
            },
            {
                "name": "create_order",
                "description": "下单占座（Mock，不会真扣钱）",
                "params": [
                    _p("train_no", "string", required=True, description="车次"),
                    _p("date", "string", required=True, description="乘车日期，YYYY-MM-DD", minLength=10, maxLength=10),
                    _p("seat_type", "string", required=True, description="席别", enum=["商务座", "一等座", "二等座", "无座"]),
                    _p("passengers", "array", required=True, description="乘车人列表", itemsType="object"),
                    _p("contact_phone", "string", description="联系手机号", minLength=11, maxLength=11),
                ],
                "success_data": {
                    "order_no": "E123456789", "status": "待支付", "train_no": "G1", "date": "2026-10-01",
                    "seat_type": "二等座", "total_amount": 553.0,
                    "pay_deadline": "2026-09-29T16:30:00+08:00",
                },
            },
            {
                "name": "get_order",
                "description": "按订单号查订单（含座位号）",
                "params": [_p("order_no", "string", required=True, description="订单号，如 E123456789")],
                "success_data": {
                    "order_no": "E123456789", "status": "已支付", "train_no": "G1", "date": "2026-10-01",
                    "seat": "02车08F", "total_amount": 553.0,
                    "passengers": [{"name": "张*三", "id_card": "110***********1234", "ticket_type": "成人票"}],
                },
            },
            {
                "name": "refund_ticket",
                "description": "退票，按开车前时长扣手续费",
                "params": [
                    _p("order_no", "string", required=True, description="订单号"),
                    _p("reason", "string", description="退票原因", enum=["行程变更", "买错车次", "其他"]),
                ],
                "success_data": {
                    "order_no": "E123456789", "status": "已退票",
                    "refund_amount": 497.7, "fee": 55.3, "fee_rate": "10%",
                },
            },
        ],
    },
    # ── 2. 麦当劳 · HTTP · Bearer · 严格 ───────────────────────────────
    {
        "slug": "mcdonalds",
        "name": "麦当劳点餐",
        "description": f"找门店、看菜单、下单取餐。要求请求头带 Authorization: Bearer {MCD_TOKEN}，不带或带错一律 401 —— 用来验客户端的认证配置对不对。",
        "instructions": "麦当劳点餐（Mock）。需要 Bearer Token。可以查附近门店、拉菜单、下单、查取餐码、取消订单。",
        "transport": "streamable-http",
        "auth_type": "bearer",
        "auth_config": {"token": MCD_TOKEN},
        "validate_mode": "strict",
        "tools": [
            {
                "name": "search_stores",
                "description": "按城市和关键词找门店",
                "params": [
                    _p("city", "string", required=True, description="城市名，如 北京"),
                    _p("keyword", "string", description="门店关键词，如 望京"),
                    _p("limit", "integer", description="最多返回几家", default=5, minimum=1, maximum=20),
                ],
                "success_data": {
                    "city": "北京", "total": 2,
                    "stores": [
                        {"store_id": "MC0731", "name": "麦当劳(望京SOHO店)", "address": "北京市朝阳区望京SOHO T1 一层",
                         "distance_m": 320, "open": True, "business_hours": "06:30-23:00",
                         "services": ["麦乐送", "甜品站", "24小时"]},
                        {"store_id": "MC0518", "name": "麦当劳(望京凯德MALL店)", "address": "北京市朝阳区望京广顺南大街",
                         "distance_m": 860, "open": True, "business_hours": "07:00-22:30", "services": ["麦乐送"]},
                    ],
                },
            },
            {
                "name": "get_menu",
                "description": "拉某家门店的在售菜单",
                "params": [
                    _p("store_id", "string", required=True, description="门店编号，如 MC0731"),
                    _p("category", "string", description="按分类筛", default="全部",
                       enum=["全部", "汉堡", "小食", "饮品", "甜品", "早餐"]),
                ],
                "success_data": {
                    "store_id": "MC0731", "category": "全部",
                    "items": [
                        {"sku": "BM001", "name": "巨无霸", "category": "汉堡", "price": 24.0, "available": True},
                        {"sku": "BM014", "name": "麦辣鸡腿堡", "category": "汉堡", "price": 21.5, "available": True},
                        {"sku": "SD002", "name": "薯条(大)", "category": "小食", "price": 12.0, "available": True},
                        {"sku": "DR005", "name": "可口可乐(中)", "category": "饮品", "price": 9.0, "available": True},
                        {"sku": "DS011", "name": "香草味圆筒", "category": "甜品", "price": 4.0, "available": False},
                    ],
                },
            },
            {
                "name": "create_order",
                "description": "下单，返回取餐码",
                "params": [
                    _p("store_id", "string", required=True, description="门店编号"),
                    _p("items", "array", required=True, description="要买的东西，每项 {sku, qty}", itemsType="object"),
                    _p("dine_type", "string", description="就餐方式", default="堂食", enum=["堂食", "外带", "麦乐送"]),
                    _p("remark", "string", description="备注，如 不要酸黄瓜", maxLength=50),
                ],
                "success_data": {
                    "order_no": "MCD20260929000123", "status": "制作中", "pickup_code": "A17",
                    "store_id": "MC0731", "dine_type": "堂食",
                    "items": [{"sku": "BM001", "name": "巨无霸", "qty": 1, "price": 24.0},
                              {"sku": "SD002", "name": "薯条(大)", "qty": 1, "price": 12.0},
                              {"sku": "DR005", "name": "可口可乐(中)", "qty": 1, "price": 9.0}],
                    "total_amount": 45.0, "eta_minutes": 8,
                },
            },
            {
                "name": "get_order",
                "description": "查订单做到哪一步了",
                "params": [_p("order_no", "string", required=True, description="订单号")],
                "success_data": {
                    "order_no": "MCD20260929000123", "status": "可取餐", "pickup_code": "A17",
                    "counter": "3 号取餐口", "total_amount": 45.0,
                    "items": [{"name": "巨无霸", "qty": 1}, {"name": "薯条(大)", "qty": 1}, {"name": "可口可乐(中)", "qty": 1}],
                },
            },
            {
                "name": "cancel_order",
                "description": "取消订单并原路退款",
                "params": [
                    _p("order_no", "string", required=True, description="订单号"),
                    _p("reason", "string", required=True, description="取消原因", enum=["点错了", "等太久", "不想要了"]),
                ],
                "success_data": {
                    "order_no": "MCD20260929000123", "status": "已取消",
                    "refund_amount": 45.0, "refund_channel": "原支付方式", "refund_eta": "1-3 个工作日",
                },
            },
        ],
    },
    # ── 3. 高德地图 · HTTP · API Key 请求头 · 严格 ──────────────────────
    {
        "slug": "amap",
        "name": "高德地图",
        "description": f"地址转坐标、周边搜索、算路线。要求自定义请求头 {AMAP_KEY_HEADER}: {AMAP_KEY} —— 和 Bearer 的区别是头的名字自己定，很多国内服务是这种。",
        "instructions": "高德地图开放平台（Mock）。需要 API Key 请求头。可以做地理编码、逆地理编码、周边搜索、驾车路线规划。",
        "transport": "streamable-http",
        "auth_type": "apikey",
        "auth_config": {"headerName": AMAP_KEY_HEADER, "apiKey": AMAP_KEY},
        "validate_mode": "strict",
        "tools": [
            {
                "name": "geocode",
                "description": "地址转坐标（地理编码）",
                "params": [
                    _p("address", "string", required=True, description="结构化地址，如 北京市朝阳区望京SOHO", minLength=2),
                    _p("city", "string", description="指定城市，缩小范围"),
                ],
                "success_data": {
                    "status": "1", "info": "OK", "count": "1",
                    "geocodes": [{
                        "formatted_address": "北京市朝阳区望京SOHO", "province": "北京市", "city": "北京市",
                        "district": "朝阳区", "adcode": "110105",
                        "location": "116.481488,39.990464", "level": "商务住宅",
                    }],
                },
            },
            {
                "name": "regeocode",
                "description": "坐标转地址（逆地理编码）",
                "params": [
                    _p("location", "string", required=True, description="经纬度，逗号分隔，如 116.481488,39.990464"),
                    _p("radius", "integer", description="搜索半径（米）", default=1000, minimum=0, maximum=3000),
                ],
                "success_data": {
                    "status": "1", "info": "OK",
                    "regeocode": {
                        "formatted_address": "北京市朝阳区望京街道望京SOHO",
                        "addressComponent": {"province": "北京市", "city": "北京市", "district": "朝阳区",
                                             "township": "望京街道", "adcode": "110105"},
                    },
                },
            },
            {
                "name": "around_search",
                "description": "搜某个坐标周边的地点",
                "params": [
                    _p("location", "string", required=True, description="中心点经纬度"),
                    _p("keywords", "string", description="关键词，如 咖啡"),
                    _p("radius", "integer", description="半径（米）", default=1000, minimum=100, maximum=50000),
                    _p("types", "string", description="POI 类型编码，逗号分隔"),
                ],
                "success_data": {
                    "status": "1", "info": "OK", "count": "2",
                    "pois": [
                        {"id": "B0FFKEPXXX", "name": "星巴克(望京SOHO店)", "type": "餐饮服务;咖啡厅",
                         "address": "望京SOHO T3 一层", "location": "116.480,39.991", "distance": "120"},
                        {"id": "B0FFKEPYYY", "name": "瑞幸咖啡(望京SOHO店)", "type": "餐饮服务;咖啡厅",
                         "address": "望京SOHO T2 一层", "location": "116.482,39.990", "distance": "180"},
                    ],
                },
            },
            {
                "name": "driving_route",
                "description": "驾车路线规划",
                "params": [
                    _p("origin", "string", required=True, description="起点经纬度"),
                    _p("destination", "string", required=True, description="终点经纬度"),
                    _p("strategy", "string", description="算路策略", default="速度优先",
                       enum=["速度优先", "费用优先", "距离优先", "避免拥堵"]),
                ],
                "success_data": {
                    "status": "1", "info": "OK",
                    "route": {
                        "origin": "116.481488,39.990464", "destination": "116.397428,39.909187",
                        "paths": [{"distance": "12400", "duration": "1680", "tolls": "0",
                                   "traffic_lights": "18", "strategy": "速度优先"}],
                    },
                },
            },
        ],
    },
    # ── 4. 和风天气 · SSE · 不认证 · 宽松 ───────────────────────────────
    {
        "slug": "weather",
        "name": "和风天气",
        "description": "实况、预报、空气质量、预警。走的是老一代传输方式 SSE（握手方式和 HTTP 那几个不一样），而且入参很松 —— 天数传成字符串 \"3\" 也认。用来验客户端支不支持 SSE、传参不规范时还能不能用。",
        "instructions": "和风天气 API（Mock）。SSE 传输。可以查实时天气、7 天预报、空气质量、灾害预警。",
        "transport": "sse",
        "auth_type": "none",
        "validate_mode": "loose",
        "tools": [
            {
                "name": "weather_now",
                "description": "查实时天气",
                "params": [_p("location", "string", required=True, description="城市名或 LocationID，如 北京 / 101010100")],
                "success_data": {
                    "code": "200", "updateTime": "2026-09-29T15:02+08:00",
                    "now": {"obsTime": "2026-09-29T15:00+08:00", "temp": "21", "feelsLike": "20",
                            "text": "多云", "windDir": "东北风", "windScale": "2",
                            "humidity": "58", "precip": "0.0", "vis": "16"},
                },
            },
            {
                "name": "weather_forecast",
                "description": "查未来几天的预报",
                "params": [
                    _p("location", "string", required=True, description="城市名或 LocationID"),
                    _p("days", "integer", description="要几天，3~7", default=7, minimum=3, maximum=7),
                ],
                "success_data": {
                    "code": "200",
                    "daily": [
                        {"fxDate": "2026-09-29", "tempMax": "23", "tempMin": "13", "textDay": "多云", "textNight": "晴"},
                        {"fxDate": "2026-09-30", "tempMax": "24", "tempMin": "14", "textDay": "晴", "textNight": "晴"},
                        {"fxDate": "2026-10-01", "tempMax": "22", "tempMin": "12", "textDay": "阴", "textNight": "小雨"},
                    ],
                },
            },
            {
                "name": "air_quality",
                "description": "查空气质量",
                "params": [_p("location", "string", required=True, description="城市名或 LocationID")],
                "success_data": {
                    "code": "200",
                    "now": {"pubTime": "2026-09-29T15:00+08:00", "aqi": "56", "level": "2", "category": "良",
                            "primary": "PM10", "pm2p5": "28", "pm10": "62", "o3": "84"},
                },
            },
            {
                "name": "weather_warning",
                "description": "查当地正在生效的灾害预警",
                "params": [_p("location", "string", required=True, description="城市名或 LocationID")],
                "success_data": {
                    "code": "200",
                    "warning": [{"id": "10101010020260929", "title": "北京市气象台发布大风蓝色预警",
                                 "severity": "Blue", "severityColor": "蓝色", "startTime": "2026-09-29T14:00+08:00",
                                 "text": "预计今天夜间到明天白天，本市有 4 级左右偏北风，阵风 7 级左右，请注意防范。"}],
                },
            },
        ],
    },
    # ── 5. 快递100 · HTTP · API Key 请求头 · 宽松 ───────────────────────
    {
        "slug": "express100",
        "name": "快递100 物流查询",
        "description": f"查物流轨迹、认快递公司、算运费。要求请求头 {KD100_KEY_HEADER}: {KD100_KEY}，但入参很松：重量传成 \"2.5\" 也认、漏了必填的补默认值。",
        "instructions": "快递100（Mock）。需要 API Key 请求头。可以查快递轨迹、按单号识别快递公司、查预计送达、试算运费。",
        "transport": "streamable-http",
        "auth_type": "apikey",
        "auth_config": {"headerName": KD100_KEY_HEADER, "apiKey": KD100_KEY},
        "validate_mode": "loose",
        "tools": [
            {
                "name": "track",
                "description": "查一个单号的物流轨迹",
                "params": [
                    _p("company", "string", required=True, description="快递公司",
                       enum=["顺丰速运", "中通快递", "圆通速递", "京东物流", "邮政EMS"]),
                    _p("waybill_no", "string", required=True, description="快递单号"),
                    _p("phone", "string", description="收件人手机后四位，顺丰必填"),
                ],
                "success_data": {
                    "message": "ok", "state": "3", "state_text": "已签收",
                    "company": "顺丰速运", "waybill_no": "SF1234567890123",
                    "data": [
                        {"time": "2026-09-29 09:12:33", "context": "【北京市】快件已签收，签收人：本人"},
                        {"time": "2026-09-29 07:40:02", "context": "【北京市】快件正在派送中，快递员：李师傅 138****6677"},
                        {"time": "2026-09-28 20:05:11", "context": "【北京市】快件已到达 北京望京营业点"},
                        {"time": "2026-09-28 03:22:47", "context": "【深圳市】快件已发出，下一站 北京集散中心"},
                    ],
                },
            },
            {
                "name": "guess_company",
                "description": "只有单号时，猜是哪家快递",
                "params": [_p("waybill_no", "string", required=True, description="快递单号")],
                "success_data": {
                    "waybill_no": "SF1234567890123",
                    "candidates": [{"code": "shunfeng", "name": "顺丰速运", "confidence": 0.97},
                                   {"code": "shentong", "name": "申通快递", "confidence": 0.11}],
                },
            },
            {
                "name": "estimate_time",
                "description": "查两地之间的预计送达时间",
                "params": [
                    _p("company", "string", required=True, description="快递公司"),
                    _p("from_city", "string", required=True, description="寄件城市"),
                    _p("to_city", "string", required=True, description="收件城市"),
                ],
                "success_data": {"company": "顺丰速运", "from": "深圳", "to": "北京",
                                 "hours": 26, "eta": "2026-09-30 11:00", "type": "标准快递"},
            },
            {
                "name": "estimate_price",
                "description": "按重量试算运费",
                "params": [
                    _p("company", "string", required=True, description="快递公司"),
                    _p("weight", "number", required=True, description="重量（公斤），传 \"2.5\" 也认", minimum=0),
                    _p("from_city", "string", description="寄件城市"),
                    _p("to_city", "string", description="收件城市"),
                ],
                "success_data": {"company": "顺丰速运", "weight": 2.5, "first_weight_price": 23.0,
                                 "extra_price": 5.0, "price": 28.0, "currency": "CNY"},
            },
        ],
    },
    # ── 6. GitHub · SSE · Bearer · 严格 ────────────────────────────────
    {
        "slug": "github",
        "name": "GitHub 代码仓库",
        "description": f"搜仓库、读文件、提 issue、开 PR。SSE 传输 + Bearer Token（真 GitHub 也是把 Personal Access Token 放 Authorization 头里）。示例 token：{GITHUB_TOKEN}",
        "instructions": "GitHub（Mock）。SSE 传输，需要 Bearer Token。可以搜仓库、读文件内容、列/建 issue、开 PR。",
        "transport": "sse",
        "auth_type": "bearer",
        "auth_config": {"token": GITHUB_TOKEN},
        "validate_mode": "strict",
        "tools": [
            {
                "name": "search_repositories",
                "description": "搜仓库",
                "params": [
                    _p("query", "string", required=True, description="搜索词，支持 GitHub 搜索语法", minLength=1),
                    _p("sort", "string", description="排序字段", default="best-match",
                       enum=["best-match", "stars", "forks", "updated"]),
                    _p("per_page", "integer", description="每页条数", default=30, minimum=1, maximum=100),
                    _p("page", "integer", description="页码", default=1, minimum=1),
                ],
                "success_data": {
                    "total_count": 2, "incomplete_results": False,
                    "items": [
                        {"id": 821004311, "full_name": "modelcontextprotocol/servers", "private": False,
                         "html_url": "https://github.com/modelcontextprotocol/servers",
                         "description": "Model Context Protocol Servers", "language": "TypeScript",
                         "stargazers_count": 18240, "forks_count": 1932, "open_issues_count": 87,
                         "updated_at": "2026-09-28T11:20:03Z"},
                        {"id": 799112045, "full_name": "modelcontextprotocol/python-sdk", "private": False,
                         "html_url": "https://github.com/modelcontextprotocol/python-sdk",
                         "description": "The official Python SDK for MCP", "language": "Python",
                         "stargazers_count": 9421, "forks_count": 764, "open_issues_count": 41,
                         "updated_at": "2026-09-27T08:02:55Z"},
                    ],
                },
            },
            {
                "name": "get_file_contents",
                "description": "读仓库里某个文件的内容",
                "params": [
                    _p("owner", "string", required=True, description="仓库所有者"),
                    _p("repo", "string", required=True, description="仓库名"),
                    _p("path", "string", required=True, description="文件路径，如 README.md"),
                    _p("ref", "string", description="分支/标签/commit，默认主分支"),
                ],
                "success_data": {
                    "name": "README.md", "path": "README.md", "sha": "9a1b2c3d4e5f60718293a4b5c6d7e8f901234567",
                    "size": 4096, "encoding": "base64", "type": "file",
                    "html_url": "https://github.com/modelcontextprotocol/servers/blob/main/README.md",
                    "content": "IyBNb2RlbCBDb250ZXh0IFByb3RvY29sIFNlcnZlcnMK",
                },
            },
            {
                "name": "list_issues",
                "description": "列 issue",
                "params": [
                    _p("owner", "string", required=True, description="仓库所有者"),
                    _p("repo", "string", required=True, description="仓库名"),
                    _p("state", "string", description="状态", default="open", enum=["open", "closed", "all"]),
                    _p("labels", "array", description="按标签筛", itemsType="string"),
                    _p("page", "integer", description="页码", default=1, minimum=1),
                ],
                "success_data": {
                    "total": 2,
                    "items": [
                        {"number": 141, "title": "SSE transport drops session on reconnect", "state": "open",
                         "user": {"login": "octocat"}, "labels": ["bug", "transport"], "comments": 6,
                         "created_at": "2026-09-26T03:14:02Z",
                         "html_url": "https://github.com/modelcontextprotocol/servers/issues/141"},
                        {"number": 139, "title": "Docs: clarify tool input schema ordering", "state": "open",
                         "user": {"login": "hubot"}, "labels": ["documentation"], "comments": 2,
                         "created_at": "2026-09-24T12:41:37Z",
                         "html_url": "https://github.com/modelcontextprotocol/servers/issues/139"},
                    ],
                },
            },
            {
                "name": "create_issue",
                "description": "提一个 issue",
                "params": [
                    _p("owner", "string", required=True, description="仓库所有者"),
                    _p("repo", "string", required=True, description="仓库名"),
                    _p("title", "string", required=True, description="标题", minLength=1, maxLength=200),
                    _p("body", "string", description="正文，支持 Markdown"),
                    _p("labels", "array", description="标签", itemsType="string"),
                    _p("assignees", "array", description="指派给谁", itemsType="string"),
                ],
                "success_data": {
                    "number": 142, "title": "(mock) 新建的 issue", "state": "open",
                    "user": {"login": "lumiere-bot"}, "labels": ["bug"], "comments": 0,
                    "created_at": "2026-09-29T07:11:04Z",
                    "html_url": "https://github.com/modelcontextprotocol/servers/issues/142",
                },
            },
            {
                "name": "create_pull_request",
                "description": "开一个 PR",
                "params": [
                    _p("owner", "string", required=True, description="仓库所有者"),
                    _p("repo", "string", required=True, description="仓库名"),
                    _p("title", "string", required=True, description="PR 标题", minLength=1, maxLength=200),
                    _p("head", "string", required=True, description="源分支"),
                    _p("base", "string", required=True, description="目标分支"),
                    _p("body", "string", description="PR 说明"),
                    _p("draft", "boolean", description="是不是草稿 PR", default=False),
                ],
                "success_data": {
                    "number": 88, "title": "(mock) 新建的 PR", "state": "open", "draft": False,
                    "head": {"ref": "feat/sse-reconnect"}, "base": {"ref": "main"},
                    "user": {"login": "lumiere-bot"}, "mergeable": True,
                    "html_url": "https://github.com/modelcontextprotocol/servers/pull/88",
                },
            },
        ],
    },
    # ── 7. 聚合支付（故意不稳定）· HTTP · 不校验 ────────────────────────
    {
        "slug": "pay-gateway",
        "name": "聚合支付网关（故意不稳定）",
        "description": "三个工具分别演：调用报错（502）、响应很慢（3 秒）、返回空。客户端的重试、超时、空结果处理靠它验。入参一个字都不看。",
        "instructions": "聚合支付网关（Mock）。**故意不稳定**：发起支付必定报错、查支付结果要等 3 秒、下载对账单返回空。用于客户端容错验证。",
        "transport": "streamable-http",
        "auth_type": "none",
        "validate_mode": "off",
        "tools": [
            {
                "name": "create_payment",
                "description": "发起支付 —— **必定报错**，用来验客户端的错误处理",
                "params": [
                    _p("out_trade_no", "string", description="商户订单号"),
                    _p("amount", "number", description="金额（元）"),
                    _p("channel", "string", description="支付渠道，如 wechat / alipay"),
                ],
                "mode": "error",
                "error_message": "Mock error: 上游支付网关返回 502 Bad Gateway（这是故意的，用来验重试）",
            },
            {
                "name": "query_payment",
                "description": "查支付结果 —— **要等 3 秒**才返回，用来验客户端超时设置",
                "params": [_p("out_trade_no", "string", description="商户订单号")],
                "delay_ms": 3000,
                "success_data": {
                    "out_trade_no": "PAY20260929000777", "trade_no": "4200002026092912345678",
                    "trade_state": "SUCCESS", "amount": 45.0, "channel": "wechat",
                    "paid_at": "2026-09-29T15:02:11+08:00",
                },
            },
            {
                "name": "download_bill",
                "description": "下载对账单 —— **返回空**，用来验客户端对空结果的处理",
                "params": [_p("bill_date", "string", description="账单日期 YYYY-MM-DD")],
                "success_data": {},
            },
        ],
    },
]

PRESET_SLUGS = {s["slug"] for s in PRESET_SERVERS}

# 预置服务上要跟着平台刷的字段。**`enabled` 故意不在里面** ——
# 停用不需要解锁（页面上锁着也能关），把它刷回去等于「我关掉的服务重启又自己开了」。
_SERVER_SYNC_FIELDS = (
    "name", "description", "instructions",
    "transport", "auth_type", "auth_config", "validate_mode",
)


def _apply_tool(tool: McpMockTool, t: dict, order: int) -> None:
    tool.description = t.get("description", "")
    tool.params = t.get("params") or []
    tool.mode = t.get("mode", "success")
    tool.success_data = t.get("success_data")
    tool.custom_data = t.get("custom_data")
    tool.custom_is_error = bool(t.get("custom_is_error", False))
    tool.error_message = t.get("error_message", "Mock error: tool call failed")
    tool.delay_ms = int(t.get("delay_ms", 0))
    tool.sort_order = order


async def _sync_tools(session: AsyncSession, server: McpMockServer, spec: dict) -> int:
    """把一个预置服务的工具刷成 spec 的样子：同名的改、缺的加、多的删。

    按**名字**对齐而不是整批删了重建：重建会换掉工具 id，页面上正开着的那条
    详情会指向一个不存在的 id，表现是「点一下空白」。
    """
    rows = (await session.execute(
        select(McpMockTool).where(McpMockTool.server_id == server.id)
    )).scalars().all()
    by_name = {r.name: r for r in rows}
    wanted = {t["name"] for t in spec.get("tools", [])}

    for order, t in enumerate(spec.get("tools", [])):
        row = by_name.get(t["name"])
        if row is None:
            row = McpMockTool(server_id=server.id, name=t["name"], enabled=True, locked=True)
            session.add(row)
        _apply_tool(row, t, order)

    for name, row in by_name.items():
        if name not in wanted:
            await session.delete(row)
    return len(wanted)


async def ensure_preset_servers(session: AsyncSession) -> dict[str, int]:
    """幂等落地预置服务。返回 {servers_created, tools_created, resynced, retired}。

    · 没这个 slug            → 按定义整套建出来，builtin=True、locked=True
    · 有、锁着、版本旧        → 按定义**整套刷新**（说明 + 认证 + 校验 + 工具）
    · 有、但已被人解锁        → 一个字不动，只把版本号记成「这版我见过」
    · 平台已经不发的老预置    → 还锁着就删掉；被人解锁过就转成普通服务留给他
    """
    stats = {"servers_created": 0, "tools_created": 0, "resynced": 0, "retired": 0}
    existing = {
        s.slug: s for s in (await session.execute(select(McpMockServer))).scalars().all()
    }

    for idx, spec in enumerate(PRESET_SERVERS):
        slug = spec["slug"]
        row = existing.get(slug)

        if row is None:
            server = McpMockServer(
                slug=slug,
                name=spec["name"],
                description=spec.get("description", ""),
                instructions=spec.get("instructions", ""),
                transport=spec.get("transport", "streamable-http"),
                auth_type=spec.get("auth_type", "none"),
                auth_config=spec.get("auth_config"),
                validate_mode=spec.get("validate_mode", "strict"),
                enabled=True,
                builtin=True,
                locked=True,
                sort_order=idx,
                preset_rev=PRESET_REV,
            )
            session.add(server)
            await session.flush()
            stats["servers_created"] += 1
            stats["tools_created"] += await _sync_tools(session, server, spec)
            continue

        row.builtin = True
        row.sort_order = idx

        if not row.locked:
            # 人解锁接管了 —— 平台不再碰内容，只记一笔「这版我见过」，
            # 免得他哪天锁回去就被覆盖掉。
            row.preset_rev = PRESET_REV
            continue

        if row.preset_rev == PRESET_REV:
            continue

        for f in _SERVER_SYNC_FIELDS:
            if f in spec:
                setattr(row, f, spec[f])
            elif f == "auth_config":
                row.auth_config = None
        row.preset_rev = PRESET_REV
        await _sync_tools(session, row, spec)
        stats["resynced"] += 1

    # 平台不再发的老预置
    for slug, row in existing.items():
        if not row.builtin or slug in PRESET_SLUGS:
            continue
        if row.locked:
            # 还锁着 = 没人动过 = 纯粹的平台数据，删干净（连日志一起，
            # 否则留下一堆指向不存在服务的日志行，页面上哪儿都看不见）
            await session.execute(delete(McpMockTool).where(McpMockTool.server_id == row.id))
            await session.execute(delete(McpMockLog).where(McpMockLog.server_id == row.id))
            await session.delete(row)
            stats["retired"] += 1
        else:
            # 人解锁改过，那就是他的东西了 —— 不删，转成普通服务
            row.builtin = False

    await session.flush()
    return stats


async def seed_preset_servers() -> dict[str, int] | None:
    """启动时调用的包装：自己开会话、自己提交，出错只记日志不拦启动。"""
    from app.deps.db import async_session_factory

    try:
        async with async_session_factory() as session:
            stats = await ensure_preset_servers(session)
            await _migrate_legacy_tools(session)
            await session.commit()
        if stats["servers_created"] or stats["resynced"] or stats["retired"]:
            logger.info(
                "MCP Mock 预置服务：新建 %d 个、刷新 %d 个、下架 %d 个（rev=%s）",
                stats["servers_created"], stats["resynced"], stats["retired"], PRESET_REV,
            )
        return stats
    except Exception as e:  # noqa: BLE001
        logger.warning("MCP Mock 预置服务落地失败（不影响服务启动）: %s", e)
        return None


LEGACY_SLUG = "legacy"
_LEGACY_FILE_NAME = "mcp_mock_tools.json"


async def _migrate_legacy_tools(session: AsyncSession) -> int:
    """把老的单服务 JSON 文件（.mock_state/mcp_mock_tools.json）搬进一个普通服务。

    老版本只有一个 MCP 服务，工具存在 JSON 文件里，人可能已经改过、加过。
    改成多服务之后那个文件不再被读 —— **不搬的话，人配过的东西会在某次升级后
    静悄悄地消失**，而页面上只会显示「预置服务好几个」，看不出少了什么。

    搬进来的服务是**普通服务**（builtin=False），可以改可以删 —— 那是人自己的东西，
    不是平台发的。搬完把文件改名成 `.migrated`，再启动就不重复搬了。
    """
    import json
    from pathlib import Path

    legacy_file = Path(__file__).resolve().parent.parent.parent / ".mock_state" / _LEGACY_FILE_NAME
    if not legacy_file.exists():
        return 0
    try:
        tools = json.loads(legacy_file.read_text())
    except Exception:  # noqa: BLE001
        legacy_file.rename(legacy_file.with_suffix(".json.broken"))
        return 0
    if not isinstance(tools, list) or not tools:
        legacy_file.rename(legacy_file.with_suffix(".json.migrated"))
        return 0

    exists = await session.scalar(select(McpMockServer).where(McpMockServer.slug == LEGACY_SLUG))
    if exists is not None:
        legacy_file.rename(legacy_file.with_suffix(".json.migrated"))
        return 0

    from app.services.mcp_mock_validate import normalize_params

    server = McpMockServer(
        slug=LEGACY_SLUG,
        name="原有工具（升级前配的）",
        description="改成多服务之前那一份 MCP Mock 的工具，原样搬过来了。这是你自己的服务，可以改也可以删。",
        instructions="Lumiere MCP Mock —— 升级前配置的工具集合。",
        transport="streamable-http",
        auth_type="none",
        validate_mode="off",
        enabled=True,
        builtin=False,
        locked=False,
        sort_order=100,
    )
    session.add(server)
    await session.flush()

    count = 0
    for order, t in enumerate(tools):
        if not isinstance(t, dict) or not t.get("name"):
            continue
        session.add(McpMockTool(
            server_id=server.id,
            name=str(t["name"])[:100],
            description=str(t.get("description") or ""),
            params=normalize_params(t.get("params")),
            mode=t.get("mode", "success"),
            success_data=t.get("successData"),
            custom_data=t.get("customData"),
            custom_is_error=bool(t.get("customIsError", False)),
            enabled=bool(t.get("enabled", True)),
            locked=bool(t.get("locked", False)),
            sort_order=order,
        ))
        count += 1
    await session.flush()
    legacy_file.rename(legacy_file.with_suffix(".json.migrated"))
    logger.info("MCP Mock：老的 %d 个工具已搬进「原有工具」服务", count)
    return count
