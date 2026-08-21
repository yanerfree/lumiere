"""新建项目时铺的默认环境和默认全局变量。

为什么要有默认：环境和全局变量 2026-08-21 从全平台改成项目级
（迁移 zzo0envproj / zzp0gvarproj）之后，新建的项目是**空的** ——
以前那 4 个环境和 5 个全局变量是全平台共用的，新项目一进来就有。
不铺默认的话，新项目第一件事是手工建 4 个环境，而且 `TEST_LANGUAGE`
不存在会让 UI 脚本的 `t()` 和接口断言的 `${T:...}` 少一层兜底。

**默认环境不带任何变量。** 老库里那 4 条种子环境带着
`BASE_URL=https://api.example.com`、`ADMIN_PASSWORD=123456` 这类演示值 ——
照抄过来等于给每个新项目预埋一份假凭证，而假凭证比没凭证更坏：
它让「忘了填」看起来像「填过了」。环境页上本来就有「常用变量参考」提示该填哪些。
"""
from __future__ import annotations

# (名字, 说明)
DEFAULT_ENVIRONMENTS: list[tuple[str, str]] = [
    ("development", "本地开发环境"),
    ("testing", "测试环境"),
    ("staging", "预发布环境"),
    ("production", "生产环境"),
]

# (key, value, 说明) —— 跟老库里那 5 条的键和默认值保持一致，
# 免得同一个 key 在不同项目里默认值不一样。
DEFAULT_GLOBAL_VARIABLES: list[tuple[str, str, str]] = [
    ("API_TIMEOUT", "30", "API 请求超时（秒）"),
    ("BASE_WAIT", "1000", "基础等待时间（毫秒）"),
    ("LOG_LEVEL", "INFO", "日志级别"),
    ("RETRY_COUNT", "3", "失败重试次数"),
    ("TEST_LANGUAGE", "zh",
     "测试跑哪种语言：zh=中文（默认，不填也是中文）/ en=英文。"
     "UI 脚本里 t(\"更多\")、接口断言里 ${T:服务名已存在} 都按这个值取译文，"
     "译文来自「国际化词典」；词典里查不到就原样返回中文，不会报错，值填错也退回中文。"
     "改这里对本项目所有环境生效；只想让某个环境跑英文，就在该环境变量里加同名 key 覆盖。"),
]


def build_defaults(project_id):
    """返回 (环境对象列表, 全局变量对象列表)，调用方 session.add_all 即可。

    放在一个函数里而不是让 project_service 自己拼：新增一条默认值时只改这里，
    不用担心漏了某条创建路径。
    """
    from app.models.environment import Environment, GlobalVariable

    envs = [
        Environment(project_id=project_id, name=name, description=desc, sort_order=i)
        for i, (name, desc) in enumerate(DEFAULT_ENVIRONMENTS)
    ]
    gvars = [
        GlobalVariable(project_id=project_id, key=key, value=value,
                       description=desc, sort_order=i)
        for i, (key, value, desc) in enumerate(DEFAULT_GLOBAL_VARIABLES)
    ]
    return envs, gvars
