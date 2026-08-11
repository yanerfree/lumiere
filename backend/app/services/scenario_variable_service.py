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

import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scenario_variable import ScenarioVariable


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
