"""把 IntegrityError 翻译成说得清原因的业务异常。

原来各 service 一律 `except IntegrityError: raise ConflictError("XX 已存在")`。
分支名放开点号那次就栽在这上面：CHECK 约束 ck_branch_name_format 拒了 "v2.2.0"，
接口却回 409「分支配置名称已存在」，而列表里根本没有重名 —— 报错指向了一个
不存在的原因，真正的约束名一个字都没露出来。

asyncpg 里这些信息本来就是现成的（实测 SQLAlchemy 2.x + asyncpg）：

    e.orig.sqlstate                   → '23505'（重名）/ '23514'（CHECK）
    e.orig.__cause__.constraint_name  → 'uq_branch_project_name' / 'ck_branch_name_format'

注意 e.orig 是 SQLAlchemy 的包装类，它有 sqlstate 但 constraint_name 恒为 None，
约束名只在 __cause__（真正的 asyncpg 异常）上，所以两层都要取。
"""
from __future__ import annotations

from typing import NoReturn

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, ValidationError

UNIQUE_VIOLATION = "23505"
CHECK_VIOLATION = "23514"
FK_VIOLATION = "23503"


def _pg_detail(exc: IntegrityError) -> tuple[str | None, str | None]:
    """从 IntegrityError 里挖出 (sqlstate, 约束名)，挖不到返回 (None, None)。"""
    orig = exc.orig
    cause = getattr(orig, "__cause__", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(cause, "sqlstate", None)
    constraint = getattr(cause, "constraint_name", None) or getattr(orig, "constraint_name", None)
    return sqlstate, constraint


def reraise_integrity_error(
    exc: IntegrityError,
    *,
    conflict_code: str,
    conflict_message: str,
    check_messages: dict[str, tuple[str, str]] | None = None,
) -> NoReturn:
    """按 sqlstate 分流后抛出对应业务异常；认不出来的原样抛回。

    conflict_*    唯一约束冲突（23505）时用的 code/message。
    check_messages CHECK 约束（23514）→ {约束名: (code, message)}。

    认不出的完整性错误**不编原因**，原样抛回 —— 宁可 500 带着真堆栈，
    也别再返回一句听起来合理的假原因，那比报错本身更难查。
    """
    sqlstate, constraint = _pg_detail(exc)

    if sqlstate == UNIQUE_VIOLATION:
        raise ConflictError(code=conflict_code, message=conflict_message)

    if sqlstate == CHECK_VIOLATION:
        code, message = (check_messages or {}).get(
            constraint, ("CONSTRAINT_VIOLATION", f"数据不满足约束 {constraint or '(未知)'}")
        )
        raise ValidationError(code=code, message=message)

    if sqlstate == FK_VIOLATION:
        raise ValidationError(
            code="FK_VIOLATION",
            message=f"关联的资源不存在或已被删除（约束 {constraint or '(未知)'}）",
        )

    raise exc
