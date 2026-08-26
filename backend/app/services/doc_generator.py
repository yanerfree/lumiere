"""文档自动生成服务 — 通用 Playwright 截图 + AI 写文档"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from app.services.ai import llm_client
from app.services.ai_config_resolver import ResolvedAIConfig

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class DocGenEvent:
    type: str
    data: dict


async def generate_doc_with_screenshots(
    *,
    system_url: str,
    username: str,
    password: str,
    title: str,
    doc_type: str,
    modules: str | None,
    audience: str | None,
    business_context: str | None,
    feedback: str | None = None,
    ai_config: ResolvedAIConfig,
    project_id: uuid.UUID,
    language: str = "zh",
) -> AsyncIterator[DocGenEvent]:
    """执行 lum-doc-generate Skill：通用 Web 截图 + AI 写文档。"""

    session_id = uuid.uuid4().hex[:12]
    shot_dir = SCREENSHOT_DIR / str(project_id) / session_id
    shot_dir.mkdir(parents=True, exist_ok=True)

    yield DocGenEvent(type="skill_start", data={"skill": "lum-doc-generate", "totalSteps": 5})

    # Step 1: 启动浏览器并登录
    yield DocGenEvent(type="step_start", data={"step": 1, "title": "启动浏览器并登录"})

    screenshots = []
    try:
        loop = asyncio.get_event_loop()
        screenshots = await loop.run_in_executor(
            None, _take_screenshots,
            system_url, username, password, modules, str(shot_dir),
        )
        yield DocGenEvent(type="step_done", data={"step": 1, "summary": f"登录成功"})
    except Exception as e:
        logger.error("Playwright failed: %s", e)
        yield DocGenEvent(type="error", data={"message": f"浏览器操作失败: {str(e)[:200]}"})
        return

    # Step 2: 截图结果
    yield DocGenEvent(type="step_start", data={"step": 2, "title": "逐页截图"})
    for s in screenshots:
        s['url'] = f"/api/screenshots/files/{project_id}/{session_id}/{s['file']}"
        yield DocGenEvent(type="screenshot", data={
            "page": s["page"], "file": s["file"], "url": s["url"],
            "isTarget": s.get("isTarget", False),
        })
    yield DocGenEvent(type="step_done", data={
        "step": 2,
        "summary": f"截图 {len(screenshots)} 张（目标模块 {sum(1 for s in screenshots if s.get('isTarget'))} 张）",
    })

    # Step 3: AI 写文档
    yield DocGenEvent(type="step_start", data={"step": 3, "title": "AI 写文档"})

    screenshots_desc = "\n".join(
        f"### 截图 {i+1}: {s['page']}{' ⭐目标模块' if s.get('isTarget') else ''}\n![{s['page']}]({s['url']})\n页面地址: {s.get('pageUrl', '')}"
        for i, s in enumerate(screenshots)
    )

    doc_label = {"manual": "操作手册", "acceptance": "验收文档", "demo": "演示文档"}.get(doc_type, "演示文档")
    lang_instruction = ""
    if language == "en":
        doc_label_en = {"manual": "Operation Manual", "acceptance": "Acceptance Document", "demo": "Demo Document"}.get(doc_type, "Document")
        lang_instruction = f"\n\n## 语言要求：\n请使用**英文**撰写整篇文档。文档类型：{doc_label_en}。"
        doc_label = doc_label_en

    # 从 SKILL.md 读取格式模板
    format_template = _load_format_template(doc_type)

    messages = [
        {"role": "system", "content": f"""你是技术文档专家。根据系统截图信息生成{doc_label}。

## 严格按照以下格式输出（章节编号、层级、顺序必须一致）：

```
{format_template}
```

## 写作质量要求：
1. **概述要详细**：模块概述至少 2-3 段话，介绍核心能力、支持的功能、应用场景
2. **功能介绍要具体**：每个功能用 2-3 句话描述，不要只写一句
3. **术语表要完整**：术语名称后面加英文括号注释（如有），解释要详细
4. **操作步骤格式统一**：每个子功能必须有「适用场景」「前置条件」「操作步骤」三部分
5. **截图引用**：用 `![](url)` 引用，下一行用 `*图：说明*` 格式
6. **截图引用** — 登录页、首页、目标模块的截图必须全部引用，不要遗漏；其他无关模块的截图可以不引用
7. **只写文档范围内的功能**：范围是「{modules or '全部功能'}」
8. **⭐标记的截图要详细展开**，其他截图作为辅助说明配图
9. **禁止出现具体系统地址/URL** — 不要写 http://xxx 等具体地址，用「系统登录页」「平台首页」等通用描述代替，因为不同用户的环境地址不同
10. **语种一致** — 中文文档的操作步骤中只写中文菜单名（如「用户管理」），不要写「用户管理」或「User Management」这种双语；术语表中可以保留英文注释
{lang_instruction}"""},
        {"role": "user", "content": f"""请生成【{doc_label}】：

标题：{title}
系统地址：{system_url}
文档范围：{modules or '全部功能'}（只详细写这个范围，不要展开其他功能）
目标读者：{audience or '测试工程师'}
{f'业务背景：{business_context}' if business_context else ''}
{f'修改意见（请在生成时重点关注）：{feedback}' if feedback else ''}

以下是系统截图（⭐标记的是目标模块，要详细写操作步骤）：

{screenshots_desc}

请严格按照格式模板和质量要求输出完整文档。"""},
    ]

    full_content = ""
    try:
        async for chunk in llm_client.stream(messages, config=ai_config):
            if chunk.delta:
                full_content += chunk.delta
                yield DocGenEvent(type="chunk", data={"content": chunk.delta})
    except Exception as e:
        yield DocGenEvent(type="error", data={"message": f"AI 生成失败: {str(e)[:200]}"})
        return

    yield DocGenEvent(type="step_done", data={"step": 3, "summary": f"文档 {len(full_content)} 字符"})
    yield DocGenEvent(type="step_start", data={"step": 4, "title": "保存文档"})
    yield DocGenEvent(type="done", data={
        "content": full_content,
        "screenshots": [{"page": s["page"], "url": s["url"], "file": s["file"]} for s in screenshots],
        "screenshotCount": len(screenshots),
    })


def _load_format_template(doc_type: str) -> str:
    """从 SKILL.md 中提取对应文档类型的格式模板。

    SKILL.md 里模板小节形如：
        #### 操作手册（manual）
        ```markdown
        ...模板正文...
        ```
    按 doc_type 匹配 `（{doc_type}）` 标记，切出其后的第一个 ```markdown 代码块。
    """
    import re

    fallback = "按标准 Markdown 格式输出"
    skill_path = Path(__file__).resolve().parent.parent / "skills" / "preset" / "lum-doc-generate" / "SKILL.md"
    if not skill_path.exists():
        return fallback
    try:
        text = skill_path.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 SKILL.md 失败: %s", e)
        return fallback

    # 兼容全角/半角括号；training 类型复用 manual 模板
    dt = "manual" if doc_type == "training" else doc_type
    pattern = rf"####[^\n]*[（(]{re.escape(dt)}[）)]\s*```markdown\s*\n(.*?)\n```"
    m = re.search(pattern, text, re.DOTALL)
    if m:
        return m.group(1).strip()
    logger.warning("SKILL.md 中未找到 doc_type=%s 的模板，使用兜底", doc_type)
    return fallback


async def generate_doc_content(
    *,
    title: str,
    doc_type: str,
    modules: str | None,
    audience: str | None,
    business_context: str | None,
    ai_config: ResolvedAIConfig,
    screenshots: list[dict],
    language: str = "zh",
) -> AsyncIterator[DocGenEvent]:
    """复用已有截图，只调 AI 生成指定语种的文档内容。"""

    screenshots_desc = "\n".join(
        f"### Screenshot {i+1}: {s['page']}{' ⭐TARGET' if s.get('isTarget') else ''}\n![{s['page']}]({s['url']})"
        for i, s in enumerate(screenshots)
    )

    doc_label = {"manual": "操作手册", "acceptance": "验收文档", "demo": "演示文档"}.get(doc_type, "文档")
    format_template = _load_format_template(doc_type)

    lang_instruction = ""
    if language == "en":
        doc_label_en = {"manual": "Operation Manual", "acceptance": "Acceptance Document", "demo": "Demo Document"}.get(doc_type, "Document")
        lang_instruction = f"\n\n## Language requirement:\nWrite the entire document in **English**. Document type: {doc_label_en}."
        doc_label = doc_label_en

    messages = [
        {"role": "system", "content": f"""You are a technical documentation expert. Generate a {doc_label} based on system screenshots.

## Follow this format:

```
{format_template}
```

## Quality requirements:
1. Detailed overview (2-3 paragraphs)
2. Specific feature descriptions (2-3 sentences each)
3. Each sub-function must have: Applicable Scenario, Prerequisites, Steps
4. Reference screenshots with `![](url)`, followed by `*Figure: description*`
5. Only write about: {modules or 'all features'}
6. ⭐ marked screenshots should be expanded in detail
{lang_instruction}"""},
        {"role": "user", "content": f"""Generate a [{doc_label}]:

Title: {title}
Scope: {modules or 'all features'}
Target audience: {audience or 'test engineers'}
{f'Business context: {business_context}' if business_context else ''}

Screenshots (⭐ = target module, write detailed steps):

{screenshots_desc}

Output the complete document."""},
    ]

    try:
        async for chunk in llm_client.stream(messages, config=ai_config):
            if chunk.delta:
                yield DocGenEvent(type="chunk", data={"content": chunk.delta})
    except Exception as e:
        yield DocGenEvent(type="error", data={"message": f"AI generation failed: {str(e)[:200]}"})


def _take_screenshots(system_url: str, username: str, password: str, modules: str | None, shot_dir: str) -> list[dict]:
    """通用 Web 截图：不依赖特定 UI 框架。"""
    from playwright.sync_api import sync_playwright

    screenshots = []
    shot_path = Path(shot_dir)
    module_keywords = [m.strip() for m in modules.split('、')] if modules else []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})

        page.goto(system_url, timeout=15000)
        page.wait_for_timeout(2000)
        _save_shot(page, shot_path, screenshots, "登录页面", system_url)

        _try_login(page, username, password)
        page.wait_for_timeout(3000)
        _save_shot(page, shot_path, screenshots, "登录后首页", page.url)

        visited_urls = {page.url}
        nav_texts = _collect_nav_texts(page)
        logger.info("Found %d nav texts: %s", len(nav_texts), nav_texts)

        for nav_text in nav_texts:
            if len(screenshots) >= 20:
                break

            is_target = not module_keywords or any(kw in nav_text for kw in module_keywords)

            try:
                clicked = _click_nav_by_text(page, nav_text)
                if not clicked:
                    continue
                page.wait_for_timeout(2000)
            except Exception:
                continue

            if page.url in visited_urls:
                continue
            visited_urls.add(page.url)

            page_title = _get_page_title(page) or nav_text
            _save_shot(page, shot_path, screenshots, page_title, page.url, is_target=is_target)

            if is_target:
                _deep_screenshots(page, shot_path, screenshots, page_title)

        browser.close()

    return screenshots


def _collect_nav_texts(page) -> list[str]:
    """收集所有可见导航菜单的文本（只收文本，不持有元素引用）。"""
    selectors = ['.ant-menu-item', '[role="menuitem"]', 'nav a', 'aside a',
                 'li[class*="menu"] > a', '[class*="sidebar"] a']
    visited = set()
    texts = []
    for sel in selectors:
        try:
            for el in page.locator(sel).all():
                try:
                    t = el.text_content().strip()
                    if t and t not in visited and 2 <= len(t) <= 30:
                        # 跳过子菜单父项（有展开箭头的）
                        parent_cls = el.evaluate("e => e.closest('[class*=\"submenu\"]')?.className || ''")
                        if 'submenu-title' in parent_cls:
                            continue
                        visited.add(t)
                        texts.append(t)
                except Exception:
                    continue
            if len(texts) >= 5:
                break
        except Exception:
            continue
    return texts


def _click_nav_by_text(page, text: str) -> bool:
    """按文本重新定位并点击导航项（避免持有过期元素引用）。"""
    selectors = ['.ant-menu-item', '[role="menuitem"]', 'nav a', 'aside a',
                 'li[class*="menu"] > a', '[class*="sidebar"] a']
    for sel in selectors:
        try:
            items = page.locator(sel).all()
            for el in items:
                try:
                    if el.text_content().strip() == text and el.is_visible():
                        el.click(timeout=3000)
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def _get_page_title(page) -> str | None:
    """从页面主内容区读取当前页面标题。"""
    for sel in ['main h1', 'main h2', '[class*="content"] h1', '[class*="content"] h2',
                '[class*="main"] h2', '#root h2']:
        try:
            el = page.locator(sel).first
            if el.count() > 0:
                t = el.text_content().strip()
                if t and 2 <= len(t) <= 30:
                    return t
        except Exception:
            continue
    return None


def _try_login(page, username: str, password: str):
    """通用登录：找用户名/密码输入框 + 提交按钮。"""
    try:
        # 找用户名输入框：按 type、id、name、placeholder 匹配
        user_selectors = [
            'input[id*="user" i]', 'input[name*="user" i]',
            'input[placeholder*="用户" i]', 'input[placeholder*="user" i]',
            'input[placeholder*="account" i]', 'input[placeholder*="email" i]',
            'input[type="text"]:not([type="password"])',
        ]
        for sel in user_selectors:
            el = page.locator(sel).first
            if el.count() > 0:
                el.fill(username)
                break

        # 找密码输入框
        pwd = page.locator('input[type="password"]').first
        if pwd.count() > 0:
            pwd.fill(password)

        # 找提交按钮
        btn_selectors = [
            'button[type="submit"]',
            'button:has-text("登录")', 'button:has-text("Login")',
            'button:has-text("Sign in")', 'button:has-text("Log in")',
            'input[type="submit"]',
        ]
        for sel in btn_selectors:
            btn = page.locator(sel).first
            if btn.count() > 0:
                btn.click()
                return
    except Exception as e:
        logger.warning("Login failed: %s", e)


def _deep_screenshots(page, shot_path, screenshots, parent_name):
    """通用深度截图：只在主内容区域找操作按钮。"""
    action_keywords = [
        '新增', '创建', '添加', '新建',
        'Add', 'Create', 'New',
    ]

    # 限定在主内容区域找按钮，排除侧边栏/导航
    content_selectors = [
        'main button', '[class*="content"] button',
        '[class*="main"] button', '[class*="body"] button',
        '#root > div > div:last-child button',  # 通常最后一个 div 是内容区
    ]

    buttons = []
    for sel in content_selectors:
        found = page.locator(sel).all()
        if found:
            buttons = found
            break
    if not buttons:
        buttons = page.locator('button').all()

    deep_count = 0
    clicked_texts = set()
    for btn in buttons:
        if deep_count >= 3:
            break
        try:
            text = btn.text_content().strip()
            if not text or not any(kw in text for kw in action_keywords):
                continue
            if not btn.is_visible():
                continue
            # 排除明显不属于当前模块的按钮（按钮文字包含其他模块名）
            skip_keywords = ['AI', 'Mock', 'MCP', 'LLM', 'API', 'Skill']
            if any(sk in text for sk in skip_keywords) and not any(sk in parent_name for sk in skip_keywords):
                continue
            if text in clicked_texts:
                continue
            clicked_texts.add(text)

            btn.click(timeout=2000)
            page.wait_for_timeout(1500)

            # 检查是否出现了弹窗/对话框/抽屉
            dialog_selectors = [
                '[role="dialog"]', '[class*="modal"]', '[class*="drawer"]',
                '[class*="dialog"]', '[class*="popup"]',
            ]
            has_dialog = False
            for sel in dialog_selectors:
                if page.locator(sel).count() > 0:
                    has_dialog = True
                    break

            if has_dialog:
                _save_shot(page, shot_path, screenshots, f"{parent_name} - {text}", page.url, is_target=True)
                deep_count += 1

                # 关闭弹窗
                close_selectors = [
                    '[class*="close"]', 'button:has-text("取消")', 'button:has-text("Cancel")',
                    'button:has-text("关闭")', 'button:has-text("Close")',
                ]
                for sel in close_selectors:
                    close_btn = page.locator(sel).first
                    if close_btn.count() > 0:
                        try:
                            close_btn.click(timeout=1000)
                            page.wait_for_timeout(500)
                            break
                        except Exception:
                            continue

                # 按 Escape 兜底关闭
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)

        except Exception:
            continue


def _save_shot(page, shot_path, screenshots, name, url, is_target=False):
    """保存截图并标注关键操作区域（红框）。"""
    if not name or name == 'None':
        return
    idx = len(screenshots) + 1
    safe_name = name.replace('/', '_').replace(' ', '_')[:20]
    fname = f"{idx:02d}_{safe_name}.png"
    filepath = str(shot_path / fname)
    page.screenshot(path=filepath)

    annotations = _detect_annotations(page)
    if annotations:
        _draw_annotations(filepath, annotations)

    screenshots.append({"page": name, "file": fname, "pageUrl": url, "isTarget": is_target})


def _detect_annotations(page) -> list[dict]:
    """检测页面上需要标注的关键元素：活跃菜单、主要按钮、弹窗输入框。"""
    rects = []
    try:
        # 当前高亮的菜单项
        for sel in ['.ant-menu-item-selected', '.ant-menu-item-active', '[class*="menu"][class*="active"]']:
            el = page.locator(sel).first
            if el.count() > 0 and el.is_visible():
                box = el.bounding_box()
                if box:
                    rects.append({**box, 'color': (24, 144, 255), 'width': 2})
                break

        # 主要操作按钮（Primary 按钮）
        for btn in page.locator('.ant-btn-primary:visible').all()[:2]:
            box = btn.bounding_box()
            if box:
                rects.append({**box, 'color': (255, 77, 79), 'width': 3})

        # 弹窗中的输入框（如果有弹窗打开）
        dialog = page.locator('[role="dialog"]:visible, [class*="modal"]:visible, [class*="drawer"]:visible').first
        if dialog.count() > 0:
            for inp in page.locator('[role="dialog"] input:visible, [class*="modal"] input:visible').all()[:3]:
                box = inp.bounding_box()
                if box and box['width'] > 50:
                    rects.append({**box, 'color': (82, 196, 26), 'width': 2})
    except Exception:
        pass
    return rects


def _draw_annotations(filepath: str, annotations: list[dict]):
    """在截图上画标注框。"""
    try:
        from PIL import Image, ImageDraw
        img = Image.open(filepath)
        draw = ImageDraw.Draw(img)
        pad = 3
        for a in annotations:
            x1, y1 = int(a['x']) - pad, int(a['y']) - pad
            x2, y2 = int(a['x'] + a['width']) + pad, int(a['y'] + a['height']) + pad
            color = a.get('color', (255, 77, 79))
            width = a.get('width', 2)
            for i in range(width):
                draw.rectangle([x1 - i, y1 - i, x2 + i, y2 + i], outline=color)
        img.save(filepath)
    except Exception as e:
        logger.warning("Failed to draw annotations: %s", e)
