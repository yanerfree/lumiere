"""文档管理 API — CRUD + AI 生成"""
from __future__ import annotations

import json
import logging
import re
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import BaseSchema
from app.core.exceptions import NotFoundError
from app.deps.auth import get_current_user, require_project_role
from app.deps.db import get_db
from app.models.user import User
from app.models.document import Document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}/documents", tags=["documents"])


class CreateDocRequest(BaseSchema):
    title: str = Field(..., min_length=1, max_length=200)
    doc_type: str = Field(default="manual", pattern="^(manual|acceptance|training)$")
    source_case_ids: list[str] | None = None


class GenerateDocRequest(BaseSchema):
    title: str = Field(..., min_length=1, max_length=200)
    doc_type: str = Field(default="manual")
    folder_id: str | None = None
    additional_info: str | None = None


def _doc_to_dict(d: Document) -> dict:
    return {
        "id": str(d.id),
        "title": d.title,
        "docType": d.doc_type,
        "language": d.language,
        "content": d.content,
        # 只报有没有上一版可退，不把整篇旧正文塞进列表响应
        "canRevert": bool(d.previous_content),
        "genConfig": d.gen_config,
        "sourceCaseIds": d.source_case_ids,
        "status": d.status,
        "createdAt": d.created_at.isoformat() if d.created_at else None,
        "updatedAt": d.updated_at.isoformat() if d.updated_at else None,
    }


@router.get("")
async def list_documents(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await session.execute(
        select(Document).where(Document.project_id == project_id).order_by(Document.created_at.desc())
    )
    docs = result.scalars().all()
    return {"data": [_doc_to_dict(d) for d in docs]}


@router.get("/{doc_id}")
async def get_document(
    project_id: uuid.UUID,
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = await session.get(Document, doc_id)
    if not doc or doc.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="文档不存在")
    return {"data": _doc_to_dict(doc)}


@router.delete("/{doc_id}")
async def delete_document(
    project_id: uuid.UUID,
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer")),
):
    doc = await session.get(Document, doc_id)
    if not doc or doc.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="文档不存在")
    await session.delete(doc)
    await session.commit()
    return {"data": {"deleted": True}}


@router.post("/generate")
async def generate_document(
    project_id: uuid.UUID,
    body: GenerateDocRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.services.ai_config_resolver import resolve_ai_config
    from app.services.ai import llm_client
    from app.mcp.tools import test_cases, api_endpoints
    from app.core.exceptions import AppError

    ai_config = await resolve_ai_config(project_id, session, capability="doc-generate")
    if not ai_config:
        raise AppError(code="AI_NOT_CONFIGURED", message="AI 服务未配置", status_code=503)

    # 获取项目默认分支
    from sqlalchemy import select as sa_select
    from app.models.project import Branch
    branch_result = await session.execute(
        sa_select(Branch).where(Branch.project_id == project_id, Branch.status == "active").limit(1)
    )
    branch = branch_result.scalar_one_or_none()
    branch_id = str(branch.id) if branch else None

    cases_text = "（无用例数据）"
    if branch_id:
        cases_result = await test_cases.list_cases(session, branch_id, page_size=100,
            folder_id=body.folder_id)
        cases = cases_result.get("cases", [])
        if cases:
            lines = []
            for c in cases[:40]:
                steps_text = " → ".join(s.get("action", "") for s in c.get("steps", [])[:5])
                lines.append(f"### {c['title']}\n前置: {c.get('preconditions','无')}\n步骤: {steps_text}\n预期: {c.get('expectedResult','')}")
            cases_text = "\n\n".join(lines)

    api_tree = await api_endpoints.list_api_tree(session, str(project_id))
    api_text = "\n".join(f"- {n.get('method','GET')} {n.get('url','')} ({n.get('name','')})" for n in api_tree if n.get("type") == "endpoint")[:15] or "（无 API 接口）"

    DOC_TYPE_LABELS = {"manual": "操作手册", "acceptance": "验收文档", "training": "培训教材"}
    doc_label = DOC_TYPE_LABELS.get(body.doc_type, "操作手册")

    business_context = body.additional_info or ""
    case_count = len(cases_result.get("cases", [])) if branch_id else 0

    messages = [
        {"role": "system", "content": f"你是技术文档专家。根据测试用例和业务背景生成{doc_label}。要求：\n- 基于用例步骤整理成操作说明，不要凭空编造功能\n- 每个操作章节对应一组相关用例\n- 步骤要具体到按钮名称、输入内容、预期结果\n- 输出完整 Markdown，结构清晰"},
        {"role": "user", "content": f"""请根据以下信息生成一份【{doc_label}】：

标题：{body.title}
用例数量：{case_count} 条

{f'## 业务背景（用户提供）{chr(10)}{business_context}' if business_context else ''}

## 测试用例（文档素材，共 {case_count} 条）
{cases_text}

## API 接口
{api_text}

请生成完整的 Markdown 文档。"""},
    ]

    doc = Document(
        project_id=project_id,
        title=body.title,
        doc_type=body.doc_type,
        status="draft",
        created_by=current_user.id,
    )
    session.add(doc)
    await session.flush()
    doc_id = doc.id

    async def event_stream():
        full = ""
        try:
            async for chunk in llm_client.stream(messages, config=ai_config):
                if chunk.delta:
                    full += chunk.delta
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk.delta}, ensure_ascii=False)}\n\n"

            doc.content = full
            doc.status = "published"
            from app.services.ai.usage import log_ai_call
            await log_ai_call(session, project_id=project_id, capability="doc-generate",
                              model=ai_config.model, est_chars=len(full))
            await session.commit()

            yield f"data: {json.dumps({'type': 'done', 'docId': str(doc_id), 'title': body.title}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


class GenerateWithScreenshotsRequest(BaseSchema):
    system_url: str = Field(..., min_length=1)
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1, max_length=200)
    doc_type: str = Field(default="manual")
    languages: list[str] = Field(default=["zh"])
    modules: str | None = None
    audience: str | None = None
    output_dir: str | None = None
    business_context: str | None = None
    feedback: str | None = None
    doc_id: str | None = None


@router.post("/generate-with-screenshots")
async def generate_with_screenshots(
    project_id: uuid.UUID,
    body: GenerateWithScreenshotsRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    from app.services.ai_config_resolver import resolve_ai_config
    from app.core.exceptions import AppError

    ai_config = await resolve_ai_config(project_id, session, capability="doc-generate-screenshots")
    if not ai_config:
        raise AppError(code="AI_NOT_CONFIGURED", message="AI 服务未配置", status_code=503)

    languages = body.languages or ["zh"]
    LANG_NAMES = {"zh": "中文", "en": "English"}

    from app.services.doc_generator import generate_doc_with_screenshots, generate_doc_content

    saved_config = {
        "systemUrl": body.system_url,
        "username": body.username,
        "password": body.password,
        "modules": body.modules,
        "audience": body.audience,
        "businessContext": body.business_context,
    }

    async def event_stream():
        screenshots = None
        created_doc_ids = []

        try:
            # Step 1: 截图只做一次
            async for event in generate_doc_with_screenshots(
                system_url=body.system_url,
                username=body.username,
                password=body.password,
                title=body.title,
                doc_type=body.doc_type,
                modules=body.modules,
                audience=body.audience,
                business_context=body.business_context,
                feedback=body.feedback,
                ai_config=ai_config,
                project_id=project_id,
                language=languages[0],
            ):
                if event.type == "done":
                    screenshots = event.data.get("screenshots", [])
                    first_content = event.data.get("content", "")

                    # 保存第一份文档
                    lang = languages[0]
                    title_suffix = f" ({lang.upper()})" if len(languages) > 1 else ""
                    if body.doc_id and len(languages) == 1:
                        doc = await session.get(Document, uuid.UUID(body.doc_id))
                        if doc and doc.project_id == project_id:
                            doc.title = body.title
                            doc.doc_type = body.doc_type
                            doc.language = lang
                            doc.content = first_content
                            doc.gen_config = saved_config
                            doc.status = "published"
                            created_doc_ids.append(str(doc.id))
                    else:
                        doc = Document(
                            project_id=project_id,
                            title=body.title + title_suffix,
                            doc_type=body.doc_type,
                            language=lang,
                            status="published",
                            content=first_content,
                            gen_config=saved_config,
                            created_by=current_user.id,
                        )
                        session.add(doc)
                        await session.flush()
                        created_doc_ids.append(str(doc.id))
                    from app.services.ai.usage import log_ai_call
                    await log_ai_call(session, project_id=project_id,
                                      capability="doc-generate-screenshots",
                                      model=ai_config.model, est_chars=len(first_content or ""))
                    await session.commit()
                else:
                    if event.type == "chunk":
                        pass
                    yield f"data: {json.dumps({'type': event.type, **event.data}, ensure_ascii=False)}\n\n"

            # Step 2: 其他语种 — 复用截图，只调 AI 写文档
            for lang in languages[1:]:
                lang_name = LANG_NAMES.get(lang, lang)
                yield f"data: {json.dumps({'type': 'step_start', 'step': 'lang', 'title': f'生成{lang_name}版本'}, ensure_ascii=False)}\n\n"

                title_suffix = f" ({lang.upper()})"
                lang_content = ""
                async for event in generate_doc_content(
                    title=body.title + title_suffix,
                    doc_type=body.doc_type,
                    modules=body.modules,
                    audience=body.audience,
                    business_context=body.business_context,
                    ai_config=ai_config,
                    screenshots=screenshots,
                    language=lang,
                ):
                    if event.type == "chunk":
                        lang_content += event.data.get("content", "")
                    yield f"data: {json.dumps({'type': event.type, **event.data}, ensure_ascii=False)}\n\n"

                if lang_content:
                    doc2 = Document(
                        project_id=project_id,
                        title=body.title + title_suffix,
                        doc_type=body.doc_type,
                        language=lang,
                        status="published",
                        content=lang_content,
                        gen_config=saved_config,
                        created_by=current_user.id,
                    )
                    session.add(doc2)
                    await session.flush()
                    created_doc_ids.append(str(doc2.id))
                    await session.commit()

                yield f"data: {json.dumps({'type': 'step_done', 'step': 'lang', 'summary': f'{lang_name}版本已生成'}, ensure_ascii=False)}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'docId': created_doc_ids[0] if created_doc_ids else None, 'docIds': created_doc_ids}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("generate_with_screenshots failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")


def _count_images(md: str | None) -> int:
    """数 Markdown 里的图片引用条数。用来核对「优化后截图有没有被 AI 弄丢」。"""
    return len(_IMG_RE.findall(md or ""))


class OptimizeDocRequest(BaseSchema):
    feedback: str = Field(default="", max_length=2000)


@router.post("/{doc_id}/optimize")
async def optimize_document(
    project_id: uuid.UUID,
    doc_id: uuid.UUID,
    body: OptimizeDocRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """按修改意见优化已有文档 —— 保留截图，只重写文字。

    和「重新生成」的区别（这是这条路存在的全部理由）：重新生成会拿账号真登录
    被测系统、重新截一遍图、整篇重做；这里一个字节都不碰被测系统，截图沿用原来的。

    覆盖前会把旧正文存进 previous_content，配套 `/revert-optimize` 可一键退回 ——
    一篇文档是真跑一遍截图跑出来的，AI 改砸了不能没有回头路。
    """
    from app.services.ai_config_resolver import resolve_ai_config
    from app.services.ai import llm_client
    from app.core.exceptions import AppError, ValidationError

    doc = await session.get(Document, doc_id)
    if not doc or doc.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="文档不存在")

    original_content = doc.content or ""
    if not original_content.strip():
        # 空文档没什么可优化的，让 AI 凭空写反而会编出一篇没有截图的假手册
        raise ValidationError(code="DOC_EMPTY", message="这篇文档还没有正文，请先生成再优化")

    ai_config = await resolve_ai_config(project_id, session, capability="doc-optimize")
    if not ai_config:
        raise AppError(code="AI_NOT_CONFIGURED", message="AI 服务未配置", status_code=503)

    from app.services.doc_generator import _load_format_template
    format_template = _load_format_template(doc.doc_type or "manual")

    messages = [
        {"role": "system", "content": f"""你是技术文档专家。用户对已生成的文档提出了修改意见，请根据修改意见优化文档。

## 重要规则：
1. **保留所有截图引用** — 不要删除或修改 `![](...)` 图片链接
2. **按照修改意见修改** — 只修改用户指出的问题部分
3. **保持整体结构** — 不要大幅改变章节结构，除非用户明确要求
4. **保持格式一致** — 输出完整的 Markdown 文档

## 格式模板参考：
```
{format_template}
```"""},
        {"role": "user", "content": f"""## 原始文档内容
{original_content}

---

## 用户的修改意见
{body.feedback or '请根据格式模板优化文档质量，确保每张截图都被引用，操作步骤详细具体。'}

---

请根据修改意见优化上面的文档，输出完整的优化后 Markdown 文档。"""},
    ]

    before = _count_images(original_content)

    async def event_stream():
        full_content = ""
        try:
            async for chunk in llm_client.stream(messages, config=ai_config):
                if chunk.delta:
                    full_content += chunk.delta
                    yield f"data: {json.dumps({'type': 'chunk', 'content': chunk.delta}, ensure_ascii=False)}\n\n"

            if not full_content.strip():
                # 一个字都没回来就别去动原文 —— 覆盖成空等于把文档删了
                yield f"data: {json.dumps({'type': 'error', 'message': 'AI 没有返回内容，原文档未改动'}, ensure_ascii=False)}\n\n"
                return

            doc.previous_content = original_content
            doc.content = full_content
            doc.status = "published"
            from app.services.ai.usage import log_ai_call
            await log_ai_call(session, project_id=project_id, capability="doc-optimize",
                              model=ai_config.model, est_chars=len(full_content))
            await session.commit()

            after = _count_images(full_content)
            yield "data: " + json.dumps({
                "type": "done",
                "docId": str(doc_id),
                # 「保留截图」是这条路唯一的承诺，兑没兑现要报数，别让人自己去数
                "imagesBefore": before,
                "imagesAfter": after,
                "imagesLost": max(0, before - after),
                "canRevert": True,
            }, ensure_ascii=False) + "\n\n"
        except Exception as e:
            logger.exception("optimize_document failed")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/{doc_id}/revert-optimize")
async def revert_optimize(
    project_id: uuid.UUID,
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """撤销上一次「优化文字」，退回优化前的正文。

    两版对调而不是丢弃 —— 撤销完还能再撤销回去，人不会因为点错一下就卡死。
    """
    from app.core.exceptions import ValidationError

    doc = await session.get(Document, doc_id)
    if not doc or doc.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="文档不存在")
    if not doc.previous_content:
        raise ValidationError(code="NO_PREVIOUS", message="这篇文档没有可撤销的优化记录")

    doc.content, doc.previous_content = doc.previous_content, doc.content
    await session.commit()
    return {"data": {"docId": str(doc_id), "canRevert": True}}


@router.get("/{doc_id}/export-html")
async def export_html(
    project_id: uuid.UUID,
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """导出 HTML — 图片转 base64 内嵌，单文件可离线查看"""
    import re, base64
    from pathlib import Path
    from fastapi.responses import Response

    doc = await session.get(Document, doc_id)
    if not doc or doc.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="文档不存在")

    content = doc.content or ""
    SCREENSHOT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "screenshots"

    def replace_img(match):
        img_path = match.group(2)
        relative = img_path.replace("/api/screenshots/files/", "")
        full_path = SCREENSHOT_DIR / relative
        if full_path.exists():
            b64 = base64.b64encode(full_path.read_bytes()).decode()
            ext = full_path.suffix.lstrip(".")
            return f"data:image/{ext};base64,{b64}"
        return img_path

    # 先逐个替换图片为 base64 img 标签
    html_body = content
    for m in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', content):
        alt = m.group(1)
        img_path = m.group(2)
        relative = img_path.replace("/api/screenshots/files/", "")
        full_path = SCREENSHOT_DIR / relative
        if full_path.exists():
            b64 = base64.b64encode(full_path.read_bytes()).decode()
            ext = full_path.suffix.lstrip(".")
            src = f"data:image/{ext};base64,{b64}"
        else:
            src = img_path
        img_tag = f'<img src="{src}" alt="{alt}" style="max-width:100%;border:1px solid #eee;border-radius:6px;margin:8px 0">'
        html_body = html_body.replace(m.group(0), img_tag, 1)

    # 文本格式转换
    html_body = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html_body, flags=re.MULTILINE)
    html_body = re.sub(r'^## (.+)$', r'<h2 style="border-bottom:1px solid #eee;padding-bottom:6px;margin-top:30px">\1</h2>', html_body, flags=re.MULTILINE)
    html_body = re.sub(r'^# (.+)$', r'<h1 style="border-bottom:2px solid #eee;padding-bottom:10px">\1</h1>', html_body, flags=re.MULTILINE)
    html_body = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html_body)
    html_body = re.sub(r'^- (.+)$', r'<div style="padding-left:16px">• \1</div>', html_body, flags=re.MULTILINE)
    html_body = html_body.replace("\n\n", "<br/><br/>").replace("\n", "<br/>")

    full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>{doc.title}</title>
<style>
body {{ max-width:900px; margin:40px auto; padding:0 20px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; color:#333; line-height:1.8; }}
img {{ max-width:100%; border:1px solid #eee; border-radius:6px; margin:8px 0; }}
</style></head>
<body>{html_body}
<hr style="margin-top:40px;border:none;border-top:1px solid #eee">
<p style="font-size:12px;color:#999">由 testBench 测试管理平台生成</p>
</body></html>"""

    from urllib.parse import quote
    return Response(
        content=full_html.encode("utf-8"),
        media_type="text/html",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(doc.title)}.html"},
    )


@router.get("/{doc_id}/export-zip")
async def export_zip(
    project_id: uuid.UUID,
    doc_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """打包下载 — Markdown + 截图文件夹"""
    import re, zipfile, io
    from pathlib import Path
    from fastapi.responses import StreamingResponse as ZipResponse

    doc = await session.get(Document, doc_id)
    if not doc or doc.project_id != project_id:
        raise NotFoundError(code="NOT_FOUND", message="文档不存在")

    content = doc.content or ""
    SCREENSHOT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "screenshots"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 替换图片路径为相对路径，同时收集文件
        def fix_path(match):
            alt = match.group(1)
            img_path = match.group(2)
            relative = img_path.replace("/api/screenshots/files/", "")
            full_path = SCREENSHOT_DIR / relative
            filename = Path(relative).name
            local_path = f"images/{filename}"
            if full_path.exists():
                zf.writestr(local_path, full_path.read_bytes())
            return f"![{alt}]({local_path})"

        fixed_content = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', fix_path, content)
        zf.writestr(f"{doc.title}.md", fixed_content.encode("utf-8"))

    buf.seek(0)
    from urllib.parse import quote
    return ZipResponse(
        content=buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(doc.title)}.zip"},
    )


# ── 任务配置保存 + 获取（供 Claude Code 使用）──

@router.post("/tasks")
async def create_doc_task(
    project_id: uuid.UUID,
    body: dict,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_project_role("project_admin", "developer", "tester")),
):
    """保存文档生成任务配置，返回任务 ID 和一行命令"""
    import uuid as uuid_mod
    task_id = uuid_mod.uuid4().hex[:12]

    # 保存到内存（生产环境应存 DB 或 Redis）
    if not hasattr(create_doc_task, '_tasks'):
        create_doc_task._tasks = {}
    import time

    create_doc_task._tasks[task_id] = {
        "projectId": str(project_id),
        "config": body,
        "createdBy": str(current_user.id),
        "createdAt": time.time(),      # 配 TTL 用，见 get_doc_task
    }

    host = body.get("_host", "localhost:8756")
    task_url = f"http://{host}/api/projects/{project_id}/documents/tasks/{task_id}"

    return {"data": {
        "taskId": task_id,
        "taskUrl": task_url,
        "command": f'请根据以下任务配置生成操作文档：{task_url}',
        "instructions": [
            "1. 在被测系统的项目目录下打开终端",
            "2. 运行 claude 启动 Claude Code",
            f"3. 粘贴这一行：请根据以下任务配置生成操作文档：{task_url}",
            "4. AI 会自动读取配置 → 打开系统 → 截图 → 写文档",
            f"5. 文档保存在 {body.get('outputDir', 'docs/')} 目录",
        ],
    }}


_DOC_TASK_TTL_SECONDS = 30 * 60


@router.get("/tasks/{task_id}")
async def get_doc_task(
    project_id: uuid.UUID,
    task_id: str,
):
    """获取任务配置（Claude Code 通过 URL 读取）。

    **这条必须不带鉴权**：平台给的是一行"粘到 Claude Code 里"的命令，
    对面那个 CC 没有平台 token，加鉴权等于把这条路堵死。
    安全性靠的是 task_id 是 uuid4（capability URL）。

    但配置里带着被测系统的**账号密码**，所以两条收口，缺一不可：
      · **一次性**：读完即删。CC 只需要读一次；URL 万一落进日志/聊天记录里也失效了。
      · **30 分钟过期**：没人来读也不会永远留在内存里。
    以前两条都没有 —— 任务建出来就一直在，谁拿到 URL 都能反复读出密码。
    """
    import time

    tasks = getattr(create_doc_task, "_tasks", {})
    task = tasks.get(task_id)
    if task and time.time() - task.get("createdAt", 0) > _DOC_TASK_TTL_SECONDS:
        tasks.pop(task_id, None)
        task = None
    if not task:
        raise NotFoundError(
            code="NOT_FOUND",
            message="任务不存在、已被读取过或已过期（任务链接是一次性的，30 分钟内有效）。回页面重新生成一条。",
        )
    tasks.pop(task_id, None)      # 一次性：读完即删
    return {"data": task["config"]}
