"""
审计日志基础设施 — contextvars 上下文 + @audit_log 装饰器。

使用方式:
  1. API 层通过依赖注入设置审计上下文:
     inject_audit_context(user, request, project_id)

  2. Service 层用装饰器自动记录:
     @audit_log(action="create", target_type="user")
     async def create_user(session, data): ...

  3. 手动记录（特殊场景）:
     await write_audit_log(session, action="import", target_type="case", ...)
"""
import functools
import logging
import uuid
from contextvars import ContextVar
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 审计上下文（通过 contextvars 在 API → Service 之间传递）
# ---------------------------------------------------------------------------

_audit_ctx: ContextVar[dict] = ContextVar("audit_ctx", default={})


def set_audit_context(
    user_id: uuid.UUID | None = None,
    trace_id: str | None = None,
    project_id: uuid.UUID | None = None,
    actor_type: str | None = None,
    actor_label: str | None = None,
) -> None:
    """设置当前请求的审计上下文（在 API 层调用）。

    actor_type/actor_label 是**来源**，跟 user_id（是谁）是两件事：
    CC 的 Key 全由同一个人建，光看 user_id 所有 CC 长得一样。
    label 传 Key 的名字，页面上「admin · via uag-cc使用」才分得出哪台机器。
    """
    _audit_ctx.set({
        "user_id": user_id,
        "trace_id": trace_id,
        "project_id": project_id,
        "actor_type": actor_type,
        "actor_label": actor_label,
    })


def get_audit_context() -> dict:
    """获取当前审计上下文。"""
    return _audit_ctx.get()


# ---------------------------------------------------------------------------
# 写入审计日志（底层函数）
# ---------------------------------------------------------------------------

async def write_audit_log(
    session: AsyncSession,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None = None,
    target_name: str | None = None,
    changes: dict | None = None,
    user_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    trace_id: str | None = None,
    actor_type: str | None = None,
    actor_label: str | None = None,
) -> None:
    """直接写入一条审计日志。失败不抛异常，仅打日志。"""
    from app.models.audit_log import AuditLog

    try:
        ctx = get_audit_context()
        log = AuditLog(
            user_id=user_id or ctx.get("user_id"),
            project_id=project_id or ctx.get("project_id"),
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            changes=changes,
            trace_id=trace_id or ctx.get("trace_id"),
            actor_type=actor_type or ctx.get("actor_type"),
            actor_label=actor_label or ctx.get("actor_label"),
        )
        session.add(log)
        await session.flush()
    except Exception:
        logger.exception("Failed to write audit log: action=%s target_type=%s", action, target_type)


# ---------------------------------------------------------------------------
# @audit_log 装饰器
# ---------------------------------------------------------------------------

async def _resolve_project_id(session: AsyncSession, result: Any) -> uuid.UUID | None:
    """从被操作的对象推出它属于哪个项目。推不出就返回 None（不猜）。

    顺序：对象自己有 project_id → 直接用；有 branch_id → 查 branch。
    只多一次主键查询，而且只在上下文没给项目时才走。
    """
    if result is None:
        return None
    pid = getattr(result, "project_id", None)
    if pid:
        return pid
    bid = getattr(result, "branch_id", None)
    if not bid:
        return None
    try:
        from app.models.project import Branch   # Branch 定义在 project.py 里，不是 branch.py
        branch = await session.get(Branch, bid)
        return getattr(branch, "project_id", None)
    except Exception:  # noqa: BLE001
        return None


def _extract_target_info(result: Any) -> tuple[uuid.UUID | None, str | None]:
    """从 service 函数返回值中提取 target_id 和 target_name。"""
    if result is None:
        return None, None

    target_id = getattr(result, "id", None)

    # 按优先级尝试提取 name
    for attr in ("name", "username", "title"):
        target_name = getattr(result, attr, None)
        if target_name is not None:
            return target_id, str(target_name)

    return target_id, None


def _extract_changes(action: str, args: tuple, kwargs: dict, result: Any) -> dict | None:
    """自动从函数参数中提取变更摘要。"""
    changes = {}

    # 从 args/kwargs 中找 Pydantic BaseModel（即请求 schema）
    from pydantic import BaseModel
    for arg in list(args[1:]) + list(kwargs.values()):
        if isinstance(arg, BaseModel):
            # 只保留非 None 的字段，排除密码
            for k, v in arg.model_dump(exclude_none=True).items():
                if "password" in k.lower():
                    changes[k] = "***"
                elif isinstance(v, list) and len(v) > 5:
                    changes[k] = f"[{len(v)} items]"
                else:
                    changes[k] = str(v) if not isinstance(v, (str, int, float, bool)) else v
            break

    if not changes and action == "create" and result is not None:
        # create 但没有 schema 参数时，记录创建的对象关键字段
        for attr in ("name", "username", "title", "role", "status"):
            val = getattr(result, attr, None)
            if val is not None:
                changes[attr] = val

    return changes if changes else None


def audit_log(
    action: str,
    target_type: str,
    changes_extractor: Any | None = None,
):
    """
    审计日志装饰器 — 加在 async service 函数上。

    用法:
        @audit_log(action="create", target_type="user")
        async def create_user(session: AsyncSession, data: CreateUserRequest) -> User:
            ...

    约定:
        - 被装饰函数第一个参数是 session: AsyncSession
        - 函数返回 ORM 对象（自动提取 .id 和 .name/.username/.title）
        - 审计上下文通过 contextvars 获取（user_id, trace_id, project_id）
        - 装饰器失败不影响主业务
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 执行原函数
            result = await func(*args, **kwargs)

            # 提取 session（第一个参数）
            session = args[0] if args else kwargs.get("session")
            if session is None:
                logger.warning("audit_log: cannot find session for %s", func.__name__)
                return result

            # 提取目标信息
            target_id, target_name = _extract_target_info(result)

            # 所属项目：上下文里没有就从对象自己推。
            # HTTP 那边只有**项目级路由**才会设 project_id（deps/auth.py:72），
            # 用例的增删改走的不是那种路由，MCP 更是整条没设 —— 于是「操作日志」
            # 的「所属项目」列一片「-」，几百条日志没法按项目筛。
            # 用例身上有 branch_id，branch 身上有 project_id，推一次就有了。
            project_id = get_audit_context().get("project_id")
            if project_id is None:
                project_id = await _resolve_project_id(session, result)

            # 提取变更摘要
            changes = None
            if changes_extractor is not None:
                try:
                    changes = changes_extractor(result, *args[1:], **kwargs)
                except Exception:
                    pass
            else:
                try:
                    changes = _extract_changes(action, args, kwargs, result)
                except Exception:
                    pass

            # 写入审计日志（不影响主业务）
            await write_audit_log(
                session=session,
                action=action,
                target_type=target_type,
                target_id=target_id,
                target_name=target_name,
                changes=changes,
                project_id=project_id,
            )

            return result
        return wrapper
    return decorator
