"""P0 封样：截图服务免鉴权（<img> 发不出 Authorization 头），所以必须自防路径穿越。

这条守的是 `GET /api/screenshots/files/{path:path}`：曾经 `UPLOAD_DIR / path` 直接拼，
`../../` 无 token 就能读出仓库任意文件（实测 curl 读到过 pyproject.toml）。
免鉴权是设计（见 test_endpoint_auth.py 的白名单），所以穿越防护只能落在这里——
直接调函数验证，不起 app、不触发 lifespan、不撞 MCP 端口。
"""
import pytest

from app.api.screenshots import (
    UPLOAD_DIR,
    _safe_segment,
    _within_upload_dir,
    serve_screenshot,
)
from app.core.exceptions import NotFoundError


def test_safe_segment_strips_traversal_material():
    # 用户可控的 project_id/session_id 里的 '/'、'..' 必须被清干净
    assert _safe_segment("../../etc") == "etc"
    assert _safe_segment("a/b/c") == "abc"
    assert _safe_segment("..") == ""
    assert _safe_segment("....//") == ""
    # 合法段原样保留
    assert _safe_segment("proj-123_abc") == "proj-123_abc"
    # 超长截断
    assert len(_safe_segment("x" * 200)) == 64


def test_within_upload_dir_containment():
    assert _within_upload_dir(UPLOAD_DIR)
    assert _within_upload_dir(UPLOAD_DIR / "a.png")
    assert _within_upload_dir(UPLOAD_DIR / "sub" / "deep" / "a.png")
    # 跳出 UPLOAD_DIR 的一律不认
    assert not _within_upload_dir(UPLOAD_DIR / ".." / ".." / "pyproject.toml")
    assert not _within_upload_dir(UPLOAD_DIR / ".." / "screenshots_evil")


@pytest.mark.asyncio
async def test_serve_rejects_traversal():
    # 经典穿越串：解析后越界 → 当作不存在（404），而不是把仓库文件吐出来
    with pytest.raises(NotFoundError):
        await serve_screenshot("../../pyproject.toml")
    with pytest.raises(NotFoundError):
        await serve_screenshot("../../../../etc/passwd")


@pytest.mark.asyncio
async def test_serve_rejects_absolute_escape():
    # path:path 也可能塞进多级 ../，逐级解析后仍必须落在 UPLOAD_DIR 内
    with pytest.raises(NotFoundError):
        await serve_screenshot("sub/../../../app/main.py")
