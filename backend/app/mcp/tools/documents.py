"""MCP 工具 — 文档生成规范。

供外部项目在 Claude Code 中「按平台模板、实操被测系统、截图贴图」生成
操作/演示/验收文档。平台不代跑：把规范（操作流程 + 格式模板 + 写作规则）发给
外部 Claude Code，由它用自己的浏览器工具在本地实操并落盘到项目 docs/。
模板与平台内生成共用同一份 SKILL.md（经 _load_format_template 切片），保证规范一致。
"""
from __future__ import annotations

DOC_TYPE_LABELS = {"manual": "操作手册", "demo": "演示文档", "acceptance": "验收文档"}


def _build_playbook(doc_type: str, label: str, template: str) -> str:
    """组装外部 Claude Code 可直接执行的 playbook。"""
    return f"""# 在 Claude Code 中生成【{label}】

你现在要为**当前项目对应的系统**生成一份【{label}】。规范由 Lumiere 平台提供，
但**由你（Claude Code）在本地实操**：真实打开系统、逐页截图、按模板把截图贴进文档。
平台不代跑。

## 第 0 步 · 收集参数（缺什么就问用户）
- system_url：被测系统访问地址（必填）
- username / password：登录账号密码（必填）
- modules：文档范围，逗号分隔的模块名（可选，不填则覆盖主要模块）
- audience：目标读者（可选，默认「测试工程师」）
- title：文档标题（可选，默认「<系统名>{label}」）

## 第 1 步 · 实操系统并截图
优先用 **Playwright MCP 浏览器工具**（browser_navigate / browser_snapshot /
browser_take_screenshot / browser_click / browser_type）。若当前没有浏览器工具，
用 Bash 跑一段 Playwright 脚本代替（headless，视口 1400×900）。

截图统一存到项目的 `docs/screenshots/` 目录，命名 `NN_模块名.png`（NN 为两位序号）：
1. 打开 system_url → 截图**登录页**
2. 输入账号密码、点登录 → 等页面加载 → 截图**登录后首页**
3. 对每个目标模块：
   - 点击左侧/顶部导航进入该模块 → 截图**列表页**
   - 点击「新增/创建/添加」打开弹窗 → 截图**表单弹窗**（关键操作必须截到）
   - 若有「编辑/详情」等核心操作，同样截图
4. 只截文档范围内的模块，最多约 15 张，避免无关页面
5. 每次操作后等待页面/弹窗加载完成再截；弹窗要完整可见

## 第 2 步 · 按模板写文档
**严格套用下方「格式模板」的章节编号、层级、顺序**，把第 1 步的截图贴到对应位置：
- 每张截图用 `![](screenshots/NN_模块名.png)` 引用（**相对路径**），紧接一行 `*图：说明*`
- 登录页、首页、每个目标模块的截图都必须被引用，不要遗漏
- 操作步骤要能实操：具体到**按钮名称、输入内容、预期结果**（用界面上的真实文字）
- 概述 2-3 段；每个子功能都要有「适用场景 / 前置条件 / 操作步骤」三部分
- 禁止模糊词（操作成功 / 显示正常 / 无报错 / 符合预期）
- 禁止写死具体 URL（用「系统登录页」「平台首页」等代称，因不同环境地址不同）
- 术语表可保留英文注释；正文中文菜单名用界面真实文字，不要中英双语混排

## 第 3 步 · 落盘
保存为 `docs/{{title}}.md`（截图已在 `docs/screenshots/`，相对路径可离线打开）。
完成后告诉用户：文档路径、截图数量、覆盖的模块。

## 必须遵循的格式模板（{label}）
```markdown
{template}
```
"""


async def get_doc_spec(doc_type: str = "manual") -> dict:
    """获取文档生成规范：操作流程 + 指定类型的格式模板 + 写作规则。

    外部 Claude Code 调用本工具拿到规范后，在本地实操被测系统、截图、按模板生成文档。
    """
    from app.services.doc_generator import _load_format_template

    if doc_type not in DOC_TYPE_LABELS:
        doc_type = "manual"
    label = DOC_TYPE_LABELS[doc_type]
    template = _load_format_template(doc_type)
    playbook = _build_playbook(doc_type, label, template)

    return {
        "docType": doc_type,
        "docTypeLabel": label,
        "template": template,
        "playbook": playbook,
        "availableDocTypes": DOC_TYPE_LABELS,
    }
