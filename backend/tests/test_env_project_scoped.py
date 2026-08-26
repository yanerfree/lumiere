"""环境从全局改成项目级的封样。

改动前：`environments` 表没有 project_id、`name` 全局 unique，于是大家在用
「名字里塞项目前缀」手动模拟隔离（实测库里 `uag` / `stoa` / `测试平台self`
各自成一摊变量），而且两个项目都想有个 `staging` 就撞。

环境里存着 BASE_URL、账号、密码 —— 这一层漏了比用例漏了更贵。
背景和决定：docs/data-scoping-and-isolation.md §4，迁移 zzo0envproj。
"""
import inspect
import uuid

import pytest
from fastapi.routing import APIRoute

from app.main import app


def _env_routes():
    return [r for r in app.routes
            if isinstance(r, APIRoute) and "/environments" in r.path]


def _dep_names(route: APIRoute) -> set[str]:
    out, seen = set(), set()

    def walk(dep):
        for sub in dep.dependencies:
            call = getattr(sub, "call", None)
            if call is not None and id(call) not in seen:
                seen.add(id(call))
                out.add(getattr(call, "__name__", ""))
            walk(sub)
    walk(route.dependant)
    return out


# ── 路由形状 ──────────────────────────────────────────────────────

def test_扫到了环境路由():
    """防选择器坏掉 —— 一条都没匹配到时，下面几条会安静地全绿。"""
    assert len(_env_routes()) >= 10, [r.path for r in _env_routes()]


def test_没有环境路由还留在项目外():
    """`/api/environments/...` 这种形状里没有项目，既没法验"你是这个项目的人"，
    也没法验"这个 env 属于这个项目" —— 留一条就等于留一个后门。
    """
    naked = sorted({f"{sorted(r.methods)[0]} {r.path}"
                    for r in _env_routes()
                    if "{project_id}" not in r.path})
    assert not naked, "以下环境路由不在项目路径下：\n" + "\n".join("  " + x for x in naked)


def test_每条环境路由都验了项目成员身份():
    naked = sorted({f"{sorted(r.methods)[0]} {r.path}"
                    for r in _env_routes()
                    if "_check" not in _dep_names(r)})
    assert not naked, "以下环境路由少了 require_project_role：\n" + "\n".join("  " + x for x in naked)


def test_带env_id的路由还要验归属链():
    """★ 只验成员身份不够：路径写自己的项目、env_id 填别人的，
    照样能读到别人的 BASE_URL 和账号 —— 就是 deps/scope.py 开头记的那类越权。
    """
    naked = sorted({f"{sorted(r.methods)[0]} {r.path}"
                    for r in _env_routes()
                    if "{env_id}" in r.path and "verify_path_scope" not in _dep_names(r)})
    assert not naked, "以下路由没验 env 归属：\n" + "\n".join("  " + x for x in naked)


# ── 服务层签名 ────────────────────────────────────────────────────

@pytest.mark.parametrize("fn_name", ["list_environments", "create_environment",
                                     "list_environments_with_base_url",
                                     "reorder_environments"])
def test_服务层的project_id是必填(fn_name):
    """★ 给 project_id 一个 `None` 默认值是这里最自然也最错的写法：
    漏改的调用点会**安静地跑通并返回全库**，而不是报错。
    """
    from app.services import environment_service

    sig = inspect.signature(getattr(environment_service, fn_name))
    p = sig.parameters.get("project_id")
    assert p is not None, f"{fn_name} 没有 project_id 参数"
    assert p.default is inspect.Parameter.empty, \
        f"{fn_name} 的 project_id 有默认值 —— 漏改的调用点会静默返回全库"


def test_MCP列环境的工具也要项目():
    """lum_list_environments 原来返回全库环境，等于把别的项目的被测地址一并露出来。"""
    from app.mcp.tools import environments

    sig = inspect.signature(environments.list_environments)
    assert "project_id" in sig.parameters
    assert sig.parameters["project_id"].default is inspect.Parameter.empty


def test_工具描述里说清了是本项目():
    """描述是 CC 唯一的说明书。还写着"所有环境"的话它会以为看得见全部。"""
    from app.mcp import TOOL_CATALOG

    d = next(t["description"] for t in TOOL_CATALOG if t["name"] == "lum_list_environments")
    assert "本项目" in d and "project_id" in d
    assert "所有测试环境" not in d


# ── 模型约束 ──────────────────────────────────────────────────────

def test_环境名是项目内唯一而不是全局唯一():
    """★ 全局唯一意味着两个项目都想有个 staging 就撞 —— 这正是本次要解决的。"""
    from app.models.environment import Environment

    col = Environment.__table__.c
    assert col.project_id.nullable is False
    assert not col.name.unique, "name 不该再是全局 unique"
    uniques = {tuple(sorted(c.name for c in cons.columns))
               for cons in Environment.__table__.constraints
               if cons.__class__.__name__ == "UniqueConstraint"}
    assert ("name", "project_id") in uniques, uniques


def test_全局变量也是项目级的():
    """「全局」= **项目内跨环境**，不是跨项目。

    这条最初写反了：当时把 global_variables 判成"该留全局"，理由是它是
    「所有环境都注入」的兜底层。看数据就知道错了 —— 5 条全是按项目调的旋钮
    （API_TIMEOUT / RETRY_COUNT / BASE_WAIT / LOG_LEVEL / TEST_LANGUAGE），
    其中 TEST_LANGUAGE「被测系统跑哪种语言」一个平台一个值根本不够用。
    迁移 zzp0gvarproj 改成项目级，覆盖语义（环境变量盖全局）一个字没动。
    """
    from app.models.environment import GlobalVariable

    col = GlobalVariable.__table__.c
    assert col.project_id.nullable is False
    assert not col.key.unique, "key 不该再是全平台 unique"
    uniques = {tuple(sorted(c.name for c in cons.columns))
               for cons in GlobalVariable.__table__.constraints
               if cons.__class__.__name__ == "UniqueConstraint"}
    assert ("key", "project_id") in uniques, uniques


def test_通知渠道仍然是全局的():
    """这个才是真该留全局的：通知渠道是平台设施，不是项目资产。
    有人"顺手补全"它的话这条会红。"""
    from app.models.environment import NotificationChannel

    assert "project_id" not in NotificationChannel.__table__.c


def test_全量替换全局变量只删本项目的():
    """★ 项目化之前是无条件 `delete(GlobalVariable)`。照原样留着的话，
    任何一个项目点一次「保存」就会清空**全平台所有项目**的全局变量。
    """
    from app.services import variable_service

    # 先剥掉注释行 —— 这个函数的注释里就引用了那句危险写法
    # `delete(GlobalVariable)`，不剥的话会先匹配到注释，测出来是假红。
    code = "\n".join(l for l in inspect.getsource(variable_service.put_variables).splitlines()
                     if not l.lstrip().startswith("#"))
    stmt = next(l.strip() for l in code.splitlines() if "delete(GlobalVariable)" in l)
    assert ".where(" in stmt, (
        f"delete 后面必须紧跟 where，漏了就是全平台数据清零。实际：{stmt}")
    assert "GlobalVariable.project_id == project_id" in stmt


def test_注入执行环境时只取本项目的全局变量():
    """不按项目过滤的话，A 项目的执行会被 B 项目的 TEST_LANGUAGE 覆盖，
    而且是静默的 —— 脚本跑出英文界面，没人知道为什么。"""
    from app.services import variable_service

    src = inspect.getsource(variable_service.build_run_env)
    assert "GlobalVariable.project_id == proj" in src
    assert "Environment.project_id" in src, "得先从 env 反查项目"


@pytest.mark.parametrize("path_frag", ["/global-variables", "/environments"])
def test_变量类路由都在项目路径下(path_frag):
    naked = sorted({f"{sorted(r.methods)[0]} {r.path}"
                    for r in app.routes
                    if isinstance(r, APIRoute) and path_frag in r.path
                    and "{project_id}" not in r.path})
    assert not naked, "以下路由不在项目路径下：\n" + "\n".join("  " + x for x in naked)


# ── 新项目的默认数据 ──────────────────────────────────────────────

def test_新项目铺了默认环境和默认全局变量():
    """★ 环境/全局变量项目化之后，新建的项目是**空的** —— 以前那 4 个环境和
    5 个全局变量是全平台共用的，新项目一进来就有。不铺默认的话，新项目第一件事
    是手工建 4 个环境，而且 TEST_LANGUAGE 不存在会让 t() 少一层兜底。
    """
    from app.services import project_service

    src = inspect.getsource(project_service.create_project)
    assert "build_defaults" in src
    assert "session.add_all" in src


def test_默认环境不带任何变量():
    """★ 老库那 4 条种子环境带着 BASE_URL=https://api.example.com、
    ADMIN_PASSWORD=123456 这类演示值。照抄给每个新项目等于预埋假凭证 ——
    而假凭证比没凭证更坏：它让「忘了填」看起来像「填过了」。
    """
    import uuid as _uuid

    from app.services.project_defaults import build_defaults

    envs, gvars = build_defaults(_uuid.uuid4())
    assert [e.name for e in envs] == ["development", "testing", "staging", "production"]
    for e in envs:
        # Environment 对象上不该挂任何 EnvironmentVariable
        assert not getattr(e, "variables", None)
    assert {g.key for g in gvars} == {
        "API_TIMEOUT", "BASE_WAIT", "LOG_LEVEL", "RETRY_COUNT", "TEST_LANGUAGE"}
    assert next(g.value for g in gvars if g.key == "TEST_LANGUAGE") == "zh"


def test_默认全局变量都带说明():
    """页面上光有 key 和值，没人知道 BASE_WAIT 是干什么的。"""
    from app.services.project_defaults import DEFAULT_GLOBAL_VARIABLES

    for key, value, desc in DEFAULT_GLOBAL_VARIABLES:
        assert value, f"{key} 没有默认值"
        assert desc and len(desc) >= 4, f"{key} 没有说明"


# ── 归属校验的行为 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_env不属于该项目时报不存在():
    """故意 404 不 403：403 等于承认"这个 id 存在，只是你没权限"。"""
    from app.core.exceptions import NotFoundError
    from app.deps.scope import verify_path_scope

    class _Req:
        path_params = {"project_id": str(uuid.uuid4()), "env_id": str(uuid.uuid4())}

    class _Result:
        @staticmethod
        def scalar_one_or_none():
            return uuid.uuid4()      # 环境存在，但属于别的项目

    class _Session:
        async def execute(self, *a, **k):
            return _Result()

    with pytest.raises(NotFoundError) as e:
        await verify_path_scope(_Req(), _Session())
    assert "不存在" in str(e.value.message)


@pytest.mark.asyncio
async def test_env归属对得上就放行():
    from app.deps.scope import verify_path_scope
    same = uuid.uuid4()

    class _Req:
        path_params = {"project_id": str(same), "env_id": str(uuid.uuid4())}

    class _Result:
        @staticmethod
        def scalar_one_or_none():
            return same

    class _Session:
        async def execute(self, *a, **k):
            return _Result()

    assert await verify_path_scope(_Req(), _Session()) is None


def test_克隆出来的副本留在源项目():
    """跨项目克隆等于把别人的凭证搬过来，不能是一次点击的副作用。"""
    from app.services import environment_service

    src = inspect.getsource(environment_service.clone_environment)
    assert "source.project_id" in src


# ── body 里的 env_id ──────────────────────────────────────────────

def test_body里的env_id都验了归属():
    """★ 路径上的两道校验（require_project_role / verify_path_scope）都**管不到请求体**。

    环境项目化之后，body 里塞一个别的项目的 env_id 就等于把别人的 BASE_URL、
    账号、密码注进本次执行。实测漏过三处：接口场景批量执行、AI 生成接口场景、
    建计划，另加「批量执行用例」。

    新加一个「从 body 收 env_id」的路由而忘了验，这条会红。
    """
    import re

    naked = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        try:
            src = inspect.getsource(r.endpoint)
        except (OSError, TypeError):
            continue
        if not re.search(r"body\.(env_id|environment_id)\b", src):
            continue
        if "assert_env_in_project" not in src:
            naked.append(f"{sorted(r.methods)[0]} {r.path}  ({r.endpoint.__name__})")
    assert not naked, (
        "以下路由从 body 收 env_id 但没验它属不属于本项目：\n"
        + "\n".join("  " + x for x in sorted(set(naked)))
    )


def test_扫到了body收env的路由():
    """防选择器坏掉 —— 一条都没匹配到时上面那条会安静全绿。"""
    import re

    n = 0
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        try:
            src = inspect.getsource(r.endpoint)
        except (OSError, TypeError):
            continue
        if re.search(r"body\.(env_id|environment_id)\b", src):
            n += 1
    assert n >= 4, f"只扫到 {n} 条，选择器可能坏了"


def test_空env_id是合法的():
    """不指定环境就跑是正常用法，别把它也拦掉。"""
    from app.services.environment_service import assert_env_in_project
    import asyncio

    class _S:
        async def execute(self, *a, **k):
            raise AssertionError("env_id 为空不该去查库")

    for empty in (None, "", 0):
        asyncio.run(assert_env_in_project(_S(), empty, uuid.uuid4()))


def test_乱填的env_id按不存在处理():
    """不是 UUID 的值不该抛 ValueError 冒到 500 —— 那会把参数错误变成服务器错误。"""
    import asyncio

    from app.core.exceptions import NotFoundError
    from app.services.environment_service import assert_env_in_project

    class _S:
        async def execute(self, *a, **k):
            raise AssertionError("解析不出 UUID 就不该查库")

    with pytest.raises(NotFoundError):
        asyncio.run(assert_env_in_project(_S(), "not-a-uuid", uuid.uuid4()))
