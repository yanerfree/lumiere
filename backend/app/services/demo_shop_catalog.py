"""订单服务 Demo 的接口清单 —— 页面左边那一列的唯一出处。

为什么放后端而不是直接写在 jsx 里：清单一旦和真实路由对不上，页面上就会出现
一条调不通的接口（或者少一条谁也不知道存在的接口），而这两种都**不报错**。
`tests/test_demo_shop_seal.py` 拿这份清单和 app 上真实注册的路由做双向差集，
加路由不写清单 / 清单写了没这条路由，都会当场红。

`auth` 三档：none 不用登录 / user 任意账号 / admin 只有管理员。
`errors` 只列**故意设计出来的**那些（练手用例就是冲它们写的），不列兜底 500。

⚠ 和隔壁 Mock 页最大的区别：Mock 能配「它答什么」，这里一个字都配不了 ——
路径、方法、状态码、返回内容全由真实代码算出来，页面上只让改「你问什么」
（路径参数 / 查询参数 / 请求体 / 用哪个账号）。别在页面上做成可编辑的样子，
那会让人以为改了有用，而实际上改完发出去的还是同一条真路由。
"""

from __future__ import annotations

ENDPOINTS: list[dict] = [
    {
        "key": "health",
        "method": "GET", "path": "/health", "group": "系统",
        "name": "健康检查",
        "summary": "看服务活着没有，不用登录",
        "auth": "none", "query": [], "pathParams": [], "body": None,
        "sampleResponse": '{\n  "status": "ok",\n  "service": "demo-shop",\n  "time": "2026-09-24T07:00:00+00:00"\n}',
        "errors": [],
    },
    {
        "key": "login",
        "method": "POST", "path": "/api/login", "group": "登录",
        "name": "登录拿 token",
        "summary": "拿到 token，后面每个请求都要带它",
        "auth": "none", "query": [], "pathParams": [],
        # 这条接口的「账号」在**请求体**里，不在 Authorization 头里。
        # 不标出来的话，页面上那个「用哪个账号发」在这条上是**死的** ——
        # 点管理员/店员，body 一个字不变，发出去永远是同一个账号。
        # 看着像开关坏了，其实是这条接口压根不读那个头。
        "bodyAccount": True,
        "body": '{\n  "username": "admin",\n  "password": "admin123"\n}',
        "bodySchema": {
            "type": "object",
            "required": ["username", "password"],
            "properties": {
                "username": {"type": "string", "description": "账号，只有 admin / clerk 两个"},
                "password": {"type": "string", "description": "密码"},
            },
        },
        "sampleResponse": '{\n  "token": "…",\n  "username": "admin",\n  "role": "admin"\n}',
        "errors": [
            {"status": 401, "code": "BAD_CREDENTIALS",
             "when": "用户名或密码错。两种错返回同一句话，不告诉你账号存不存在"},
        ],
    },
    {
        "key": "me",
        "method": "GET", "path": "/api/me", "group": "登录",
        "name": "我是谁",
        "summary": "拿当前 token 换一下身份信息",
        "auth": "user", "query": [], "pathParams": [], "body": None,
        "sampleResponse": '{\n  "username": "admin",\n  "role": "admin",\n  "displayName": "管理员"\n}',
        "errors": [{"status": 401, "code": "UNAUTHORIZED", "when": "没带 token / token 伪造或过期"}],
    },
    {
        "key": "products",
        "method": "GET", "path": "/api/products", "group": "商品",
        "name": "商品列表",
        "summary": "可以按关键字搜、可以只看在售的",
        "auth": "user", "pathParams": [],
        "query": [
            {"name": "keyword", "sample": "", "desc": "按商品名或 SKU 模糊搜", "type": "string", "required": False},
            {"name": "onlyActive", "sample": "", "desc": "填 true 就只返回在售的", "type": "boolean", "required": False},
            {"name": "page", "sample": "1", "desc": "页码", "type": "integer", "required": False},
            {"name": "pageSize", "sample": "50", "desc": "每页条数", "type": "integer", "required": False},
        ],
        "body": None,
        "sampleResponse": '{\n  "data": [{"sku": "SKU-001", "name": "机械键盘 87 键", "price": "399.00", "stock": 50, "active": true}],\n  "total": 9\n}',
        "errors": [{"status": 401, "code": "UNAUTHORIZED", "when": "没带 token"}],
    },
    {
        "key": "product_detail",
        "method": "GET", "path": "/api/products/{sku}", "group": "商品",
        "name": "商品详情",
        "summary": "按商品编号查一个",
        "auth": "user", "query": [], "body": None,
        "pathParams": [{"name": "sku", "sample": "SKU-001", "desc": "商品编号", "type": "string", "required": True}],
        "sampleResponse": '{\n  "sku": "SKU-001",\n  "name": "机械键盘 87 键",\n  "stock": 50,\n  "active": true\n}',
        "errors": [
            {"status": 404, "code": "PRODUCT_NOT_FOUND", "when": "这个 SKU 不存在"},
            {"status": 401, "code": "UNAUTHORIZED", "when": "没带 token"},
        ],
    },
    {
        "key": "product_update",
        "method": "PATCH", "path": "/api/products/{sku}", "group": "商品",
        "name": "改商品",
        "summary": "改价、补库存、上下架 —— 只有管理员能改",
        "auth": "admin", "query": [],
        "pathParams": [{"name": "sku", "sample": "SKU-001", "desc": "商品编号", "type": "string", "required": True}],
        "body": '{\n  "price": "429.00",\n  "stock": 80,\n  "active": true\n}',
        # 一个必填都没有：给什么改什么，没给的字段原样不动（PATCH 的语义）。
        "bodySchema": {
            "type": "object",
            "required": [],
            "properties": {
                "name": {"type": "string", "description": "商品名"},
                "price": {"type": "string", "description": "单价，字符串形式的两位小数，不能为负"},
                "stock": {"type": "integer", "description": "库存，不能为负"},
                "active": {"type": "boolean", "description": "在售 / 下架"},
            },
        },
        "sampleResponse": '{\n  "sku": "SKU-001",\n  "price": "429.00",\n  "stock": 80\n}',
        "errors": [
            {"status": 403, "code": "FORBIDDEN", "when": "店员账号来改"},
            {"status": 404, "code": "PRODUCT_NOT_FOUND", "when": "这个 SKU 不存在"},
            {"status": 422, "code": "INVALID_PARAM", "when": "价格为负、库存为负"},
        ],
    },
    {
        "key": "orders",
        "method": "GET", "path": "/api/orders", "group": "订单",
        "name": "订单列表",
        "summary": "可以按状态筛、按订单号或客户名搜",
        "auth": "user", "pathParams": [],
        "query": [
            {"name": "status", "sample": "", "desc": "pending / paid / shipped / completed / cancelled", "type": "string", "required": False},
            {"name": "keyword", "sample": "", "desc": "订单号或客户名", "type": "string", "required": False},
            {"name": "page", "sample": "1", "desc": "页码", "type": "integer", "required": False},
            {"name": "pageSize", "sample": "20", "desc": "每页条数", "type": "integer", "required": False},
        ],
        "body": None,
        "sampleResponse": '{\n  "data": [{"orderNo": "SO202609240001", "status": "pending", "totalAmount": "399.00"}],\n  "total": 1\n}',
        "errors": [{"status": 401, "code": "UNAUTHORIZED", "when": "没带 token"}],
    },
    {
        "key": "order_detail",
        "method": "GET", "path": "/api/orders/{order_no}", "group": "订单",
        "name": "订单详情",
        "summary": "连每一行商品一起返回",
        "auth": "user", "query": [], "body": None,
        "pathParams": [{"name": "order_no", "sample": "SO202609240001", "desc": "订单号", "type": "string", "required": True}],
        "sampleResponse": '{\n  "orderNo": "SO202609240001",\n  "status": "pending",\n  "items": [{"sku": "SKU-001", "quantity": 1}]\n}',
        "errors": [
            {"status": 404, "code": "ORDER_NOT_FOUND", "when": "订单号不存在"},
            {"status": 401, "code": "UNAUTHORIZED", "when": "没带 token"},
        ],
    },
    {
        "key": "order_create",
        "successStatus": 201,
        "method": "POST", "path": "/api/orders", "group": "订单",
        "name": "下单",
        "summary": "会真的扣库存，成功返回 201",
        "auth": "user", "query": [], "pathParams": [],
        "body": '{\n  "customerName": "张三",\n  "customerPhone": "13800000000",\n  "remark": "",\n  "items": [\n    {"sku": "SKU-001", "quantity": 1}\n  ]\n}',
        "bodySchema": {
            "type": "object",
            "required": ["customerName", "items"],
            "properties": {
                "customerName": {"type": "string", "description": "客户名，不能为空"},
                "customerPhone": {"type": "string", "description": "手机号，可以不填"},
                "remark": {"type": "string", "description": "备注，可以不填"},
                "items": {
                    "type": "array", "minItems": 1, "description": "买哪些，至少一行",
                    "items": {
                        "type": "object",
                        "required": ["sku", "quantity"],
                        "properties": {
                            "sku": {"type": "string", "description": "商品编号"},
                            "quantity": {"type": "integer", "minimum": 1, "maximum": 99,
                                         "description": "数量，1 到 99"},
                        },
                    },
                },
            },
        },
        "sampleResponse": '{\n  "orderNo": "SO202609240001",\n  "status": "pending",\n  "totalAmount": "399.00"\n}',
        "errors": [
            {"status": 409, "code": "OUT_OF_STOCK", "when": "库存不够，报错里带还剩几件（拿 SKU-008 试，它库存就是 0）"},
            {"status": 409, "code": "PRODUCT_INACTIVE", "when": "买了已下架的商品（拿 SKU-009 试）"},
            {"status": 404, "code": "PRODUCT_NOT_FOUND", "when": "SKU 不存在"},
            {"status": 422, "code": "INVALID_PARAM", "when": "没填客户名、没选商品、数量为 0 或超过 99"},
        ],
    },
    {
        "key": "order_update",
        "method": "PUT", "path": "/api/orders/{order_no}", "group": "订单",
        "name": "改订单",
        "summary": "改客户名 / 手机 / 备注 —— 只有待支付的能改",
        "auth": "user", "query": [],
        "pathParams": [{"name": "order_no", "sample": "SO202609240001", "desc": "订单号", "type": "string", "required": True}],
        "body": '{\n  "customerName": "李四",\n  "customerPhone": "13900000000",\n  "remark": "换个收件人"\n}',
        # 三个都选填：只改传上来的那几个字段。
        "bodySchema": {
            "type": "object",
            "required": [],
            "properties": {
                "customerName": {"type": "string", "description": "客户名"},
                "customerPhone": {"type": "string", "description": "手机号"},
                "remark": {"type": "string", "description": "备注"},
            },
        },
        "sampleResponse": '{\n  "orderNo": "SO202609240001",\n  "customerName": "李四"\n}',
        "errors": [
            {"status": 409, "code": "ORDER_NOT_EDITABLE", "when": "已经支付过了还来改"},
            {"status": 404, "code": "ORDER_NOT_FOUND", "when": "订单号不存在"},
        ],
    },
    {
        "key": "order_action",
        "method": "POST", "path": "/api/orders/{order_no}/{action}", "group": "订单",
        "name": "状态流转",
        "summary": "pay 支付 / ship 发货 / complete 完成 / cancel 取消",
        "auth": "user", "query": [],
        "pathParams": [
            {"name": "order_no", "sample": "SO202609240001", "desc": "订单号", "type": "string", "required": True},
            {"name": "action", "sample": "pay", "desc": "pay / ship / complete / cancel", "type": "string", "required": True},
        ],
        "body": None,
        "sampleResponse": '{\n  "orderNo": "SO202609240001",\n  "status": "paid"\n}',
        "errors": [
            {"status": 409, "code": "INVALID_TRANSITION", "when": "跳步（待支付直接发货），或已发货了还来取消"},
            {"status": 404, "code": "ORDER_NOT_FOUND", "when": "订单号不存在"},
            {"status": 422, "code": "INVALID_PARAM", "when": "action 不是那四个词之一"},
        ],
    },
    {
        "key": "order_delete",
        "method": "DELETE", "path": "/api/orders/{order_no}", "group": "订单",
        "name": "删订单",
        "summary": "只有管理员能删；待支付 / 已支付的会把库存还回去",
        "auth": "admin", "query": [], "body": None,
        "pathParams": [{"name": "order_no", "sample": "SO202609240001", "desc": "订单号", "type": "string", "required": True}],
        "sampleResponse": '{\n  "deleted": true,\n  "orderNo": "SO202609240001",\n  "restockedItems": 1\n}',
        "errors": [
            {"status": 403, "code": "FORBIDDEN", "when": "店员账号来删 —— 注意是 403 不是 401"},
            {"status": 404, "code": "ORDER_NOT_FOUND", "when": "订单号不存在"},
        ],
    },
    {
        "key": "stats",
        "method": "GET", "path": "/api/stats", "group": "统计",
        "name": "订单统计",
        "summary": "总数、各状态条数、已收金额、缺货商品数",
        "auth": "user", "query": [], "pathParams": [], "body": None,
        "sampleResponse": '{\n  "orders": 1,\n  "ordersByStatus": {"pending": 1},\n  "revenue": "0.00",\n  "outOfStock": 1\n}',
        "errors": [{"status": 401, "code": "UNAUTHORIZED", "when": "没带 token"}],
    },
    {
        "key": "reset",
        "method": "POST", "path": "/api/admin/reset", "group": "系统",
        "name": "重置样例数据",
        "summary": "清空订单、商品库存回出厂值 —— 只有管理员",
        "auth": "admin", "query": [], "pathParams": [], "body": None,
        "sampleResponse": '{\n  "deletedOrders": 1,\n  "products": 9\n}',
        "errors": [{"status": 403, "code": "FORBIDDEN", "when": "店员账号来重置"}],
    },
]

# 不进清单的路由：/ 是说明页，/docs、/openapi.json 是 FastAPI 自带的。
EXCLUDED_PATHS = {"/", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}

GROUP_ORDER = ["登录", "商品", "订单", "统计", "系统"]
