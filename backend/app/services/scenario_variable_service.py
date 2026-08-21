"""场景变量执行期解析 —— 把用例的场景变量解析成注入执行环境的 SV_* 键值。

kind:
  - literal    → 直接用 value_template
  - random     → value_template + -{runId}-{rand}（每次执行唯一、可追溯本脚本造的数据；
                 用连字符分隔而非下划线——服务名/slug/DNS 名等多数只允许 [a-z0-9-]，下划线常被拒）
  - global_ref → 从全局数据查（Epic1 提供项目级全局数据；此前先从传入 global_lookup 例如环境变量取）
  - template   → 部分固定+部分随机：value_template 里字面字符原样保留，内嵌 {{$fn}} 生成器
                 token 执行期展开（见 data_generators.expand_template），对标 apifox 数据生成器

UI(process.env.SV_x) 与接口(os.environ['SV_x']) 执行器读同一份，做到共用。
"""
from __future__ import annotations

import logging
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scenario_variable import ScenarioVariable

logger = logging.getLogger(__name__)


async def resolve_scenario_variables(
    session: AsyncSession,
    case_id,
    global_lookup: dict[str, str] | None = None,
    run_id: str | None = None,
) -> dict[str, str]:
    """返回 {'SV_RUN_ID': runId, 'SV_<name>': value, ...}，供注入执行环境。"""
    import uuid as _uuid
    cid = case_id if isinstance(case_id, _uuid.UUID) else _uuid.UUID(str(case_id))
    rid = run_id or uuid.uuid4().hex[:8]
    rows = (
        await session.execute(select(ScenarioVariable).where(ScenarioVariable.case_id == cid))
    ).scalars().all()
    out: dict[str, str] = {"SV_RUN_ID": rid}
    gl = global_lookup or {}
    for v in rows:
        if v.kind == "random":
            val = f"{v.value_template}-{rid}-{secrets.token_hex(2)}"
        elif v.kind == "global_ref":
            val = gl.get(v.value_template, "")
        elif v.kind == "template":
            from app.services.data_generators import expand_template
            val = expand_template(v.value_template or "", rid)
        else:  # literal
            val = v.value_template
        out[f"SV_{v.name}"] = str(val)
    return out


def add_bare_names(env: dict, resolved: dict) -> dict:
    """把 `SV_名字` 同时以裸名 `名字` 注册一份。

    两条执行路径此前各写各的：接口场景那边做了这一步，所以 `${PROJ_NAME}` 能用；
    UI 脚本那边只注 `SV_PROJ_NAME`。而工具说明和抽屉里都写着「UI 和接口共用同一份」——
    外部 CC 照着写 `os.getenv("PROJ_NAME")`，拿到的是空串，**还不报错**，
    表现成"填了个空名字"这种莫名其妙的失败。实测踩到了。

    裸名不覆盖已有的环境变量：环境里同名的键是"这个环境是什么"，优先级更高。
    """
    for k, val in (resolved or {}).items():
        env[k] = val
        if k.startswith("SV_") and k != "SV_RUN_ID" and k[3:] not in env:
            env[k[3:]] = val
    return env


async def inject_project_resources(session: AsyncSession, case_id, env: dict,
                                   content: str) -> list[str]:
    """把**项目级共享资源**（自动化数据）也注进 UI 脚本的执行环境。

    **补的是规范里答不上来的那一问：UI 脚本里的 projectId 该放哪层。**
    变量分层说"多条用例只读引用的底座 → 项目级共享资源，跑前平台探一次并注入
    `${资源名}`"，但那句只对**接口场景**成立（api_test_runner 那条路做了）；
    UI 脚本这边只注环境变量 + 场景变量，共享资源一个都进不来。于是唯一能跑的写法
    要么是 `kind=literal` + 真实 UUID（规范明令禁止），要么自己在脚本里按名字反查一遍
    —— 后者是可行的，但规范里没写，等于让人自己发明一条没人保证的路。

    **只在脚本真的引用了才探。** 判据是脚本里 `os.getenv("X")` 的键跟资源名
    （裸名或 `SV_` 前缀）对得上。对不上就一次 HTTP 都不发 —— 探测是要打被测环境的，
    给不需要它的脚本白加几百毫秒和几条无谓请求，这类"顺手加上"的成本最后都会回来。

    返回真的注进去的键名（调用方要能说出"这次注了什么"，不然又是一次静默行为）。
    """
    from app.models.automation_resource import AutomationResource
    from app.models.case import Case
    from app.models.project import Branch
    from app.services.ui_text_render import _GETENV_RE

    cid = case_id if isinstance(case_id, uuid.UUID) else uuid.UUID(str(case_id))
    case = await session.get(Case, cid)
    if case is None:
        return []
    branch = await session.get(Branch, case.branch_id)
    if branch is None:
        return []

    wanted = {m.group("key") for m in _GETENV_RE.finditer(content or "")}
    if not wanted:
        return []
    resources = (await session.execute(
        select(AutomationResource).where(AutomationResource.project_id == branch.project_id)
    )).scalars().all()
    # 资源的 extract 声明了哪些键 —— 注进来的是那些键，不是资源名本身。
    names: set[str] = set()
    for res in resources:
        names.add(str(res.name))
        names.update(str(k) for k in ((res.exists_check or {}).get("extract") or {}))
    if not any(n in wanted or f"SV_{n}" in wanted for n in names):
        return []

    from types import SimpleNamespace

    from app.services.api_test_runner import TokenCache, _resolve_automation_resources
    probe_env = dict(env)
    # 探测要带鉴权。**必须给 TokenCache**：不给的话请求裸发、401，而 _check_one 把
    # 401 当"没查到"，于是每次都注不进任何东西 —— 而且是静默的（返回空列表，
    # 跟"这脚本不需要资源"长得一样）。接口场景那条路一直是传 TokenCache 的。
    if not probe_env.get("AUTH_TOKEN") and env.get("TEST_TOKEN"):
        probe_env["AUTH_TOKEN"] = env["TEST_TOKEN"]
    try:
        resolved, report = await _resolve_automation_resources(
            session, SimpleNamespace(project_id=branch.project_id), probe_env,
            token_cache=TokenCache(probe_env))
    except Exception:      # 探不到不该把整次执行打死 —— 脚本会红在取不到值上，看得见
        logger.warning("项目级共享资源探测异常，这次不注入", exc_info=True)
        return []
    if not resolved:
        # 脚本明明引用了、却一个都没注进来 —— 这条**必须留痕**，
        # 否则脚本红在"取到空串"上，而人会去查前端。
        logger.warning("脚本引用了项目级共享资源但一个都没探到：%s", report)
    added: list[str] = []
    for k, v in (resolved or {}).items():
        for key in (k, f"SV_{k}"):
            if key in wanted and not env.get(key):
                env[key] = str(v)
                added.append(key)
    return added
