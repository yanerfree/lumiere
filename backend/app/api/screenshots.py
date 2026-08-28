"""截图上传 API — 供探索测试和文档生成使用"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Depends

from app.deps.auth import get_current_user
from app.models.user import User

router = APIRouter(prefix="/api/screenshots", tags=["screenshots"])

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "screenshots"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 用户可控的路径段（project_id/session_id）只允许这些字符，杜绝 '/'、'..'、空段等穿越素材
_UNSAFE_SEG = re.compile(r"[^A-Za-z0-9_-]")


def _safe_segment(seg: str) -> str:
    return _UNSAFE_SEG.sub("", seg)[:64]


def _within_upload_dir(p: Path) -> bool:
    """解析软链/相对段后，p 必须仍落在 UPLOAD_DIR 内。"""
    base = UPLOAD_DIR.resolve()
    target = p.resolve()
    return target == base or base in target.parents


@router.post("/upload")
async def upload_screenshot(
    file: UploadFile = File(...),
    project_id: str | None = None,
    session_id: str | None = None,
    current_user: User = Depends(get_current_user),
):
    ext = Path(file.filename or "img.png").suffix or ".png"
    filename = f"{uuid.uuid4().hex}{ext}"

    sub_dir = UPLOAD_DIR
    if project_id:
        seg = _safe_segment(project_id)
        if seg:
            sub_dir = sub_dir / seg
    if session_id:
        seg = _safe_segment(session_id)
        if seg:
            sub_dir = sub_dir / seg
    # 双保险：清洗后仍越界则回落到根目录
    if not _within_upload_dir(sub_dir):
        sub_dir = UPLOAD_DIR
    sub_dir.mkdir(parents=True, exist_ok=True)

    file_path = sub_dir / filename
    content = await file.read()
    file_path.write_bytes(content)

    relative_path = str(file_path.relative_to(UPLOAD_DIR))
    url = f"/api/screenshots/files/{relative_path}"

    return {
        "data": {
            "url": url,
            "filename": filename,
            "size": len(content),
        }
    }


@router.get("/files/{path:path}")
async def serve_screenshot(path: str):
    # 免鉴权是有意的（<img> 标签发不出 Authorization 头），所以这里必须自己防路径穿越：
    # `../../` 解析后会跳出 UPLOAD_DIR，一律当作不存在处理。
    from fastapi.responses import FileResponse
    from app.core.exceptions import NotFoundError
    file_path = UPLOAD_DIR / path
    if not _within_upload_dir(file_path):
        raise NotFoundError(code="NOT_FOUND", message="截图不存在")
    if not file_path.exists() or not file_path.is_file():
        raise NotFoundError(code="NOT_FOUND", message="截图不存在")
    return FileResponse(file_path)
