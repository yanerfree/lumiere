"""testBench MCP Server — 暴露平台数据能力，供 Web 引擎和 Claude Code 使用"""
from __future__ import annotations

from fastmcp import FastMCP

from app.mcp.deps import get_mcp_session
from app.mcp.tools import test_cases, api_endpoints, environments, test_reports, api_tests, scenario_gen, projects, ui_scripts, documents, sync

mcp = FastMCP(
    name="testBench",
    instructions="""testBench 测试管理平台 MCP Server。

═══════════════════════════════════════════════════════════
【先看这里·选对工具，别搞混】
═══════════════════════════════════════════════════════════

① 生成「步骤用例」（功能用例）走哪条路？
   · 手上只有需求文档，要批量产出        → tb_create_scenario_task + tb_confirm_and_generate（AI 流水线，多阶段质量管控）
   · 已在被测系统里活体验证过，要回写成果 → tb_create_case（一条条显式建）

② 「接口测试」有两种，不是一回事，别混：
   · 【接口测试模块·单接口】tb_generate_api_test
       —— 只有接口文档、无法活体验证时，给一个/少数接口，AI 造一组正向/参数/边界/安全场景。
          写入 api_test_scenarios，用 source_api_ids 关联接口，**没有 source_case_id**。
   · 【用例·编排的接口场景】tb_sync_orchestrated_scenario
       —— 你**亲手活体验证过**的多步 E2E 接口链（登录→造→断言→清理），显式写回，
          用 source_case_id 绑定某功能用例、**共享该用例的场景变量**。

③ 活体验证后「回推同步」= 步骤用例 + 编排接口场景 + 场景变量。通道：
   tb_get_sync_spec（先对齐口径）→ tb_list_global_data（看可引用项）→
   tb_create_case（步骤用例）+ tb_upsert_scenario_variables（场景变量）+ tb_sync_orchestrated_scenario（接口场景）
   → tb_run_api_test（执行验证）。

④ 变量纪律（回推时必须遵守，硬约束）：脚本里**不允许写死数据变量**。任何取值只能来自
   ①场景变量 ${名字}/${SV_名字}  ②项目级全局引用（${BASE_URL}/账号/token，见 tb_list_global_data）
   ③步骤间提取物（上一步 variables_extract）。tb_sync_orchestrated_scenario 会硬拦截悬空 ${x}。

═══════════════════════════════════════════════════════════

当用户要求生成测试用例时，必须按以下流程执行：

第一步：确定目标
- 调用 tb_list_projects 和 tb_list_branches 确定目标项目和分支
- 调用 tb_list_api_tree 获取 API 接口列表，了解有哪些功能模块

第二步：了解真实页面（最关键，不能跳过）
- 调用 tb_get_api_node 获取接口详细定义（字段名、类型、校验规则、枚举值、必填项）
- 在用户项目中用 Read 工具读取前端源码，提取真实 UI 信息：
  * 找页面组件：grep -r "创建|新建|编辑|删除" src/pages/ src/components/ src/views/ --include="*.vue" --include="*.jsx" --include="*.tsx" -l
  * 读组件文件，提取：按钮文字（<Button>保存</Button>、<el-button>创建</el-button>）
  * 提取表单字段标签（<Form.Item label="服务名称">、<el-form-item label="名称">）
  * 提取 Toast/消息文案（message.success('创建成功')、ElMessage.error('名称已存在')）
  * 提取弹窗标题（<Modal title="新建服务">、<el-dialog title="编辑">）
  * 提取路由路径（router 配置中的 path）
- 如果找不到前端代码，就从 API 定义推断页面结构，但必须在步骤中标注"待确认"

第三步：检查去重
- 调用 tb_list_cases 检查同模块已有用例，避免重复

第四步：生成用例
- 基于第二步获取的真实 UI 信息，生成用例步骤
- 每条用例调用 tb_create_case 入库

第五步：生成 UI 自动化脚本（可选，用户要求时执行）
- 对生成的用例，调用 tb_generate_ui_script 自动生成 Playwright 脚本
- 需要指定 env_id（环境ID，包含 BASE_URL 等配置）
- 脚本通过 Playwright MCP 逐步操作真实浏览器生成，基于页面真实元素
- 生成后自动执行验证，通过的脚本保存到用例

用例质量规范：
- 步骤必须是页面操作（点击按钮、填写输入框），禁止接口调用风格
- 按钮名称、字段标签、Toast文案必须来自第二步提取的真实代码
- 预期结果必须是 UI 可见的（Toast内容、页面跳转、列表变化）
- 禁止模糊词：操作成功/显示正常/无报错/符合预期
- 每条用例只验证一个测试点
- P0 占比不超过 15%
- case_type 用 e2e
- preconditions 必填，分为环境前置（登录/权限）和业务数据前置（已存在XX数据）
- steps 每项必须有 seq（从1开始）、action、expected
- 多角色用例步骤前必须加角色标记：[管理员] / [租户]

当用户要求生成操作文档 / 演示文档 / 验收文档时，按以下流程执行：

第一步：取规范
- 调用 tb_get_doc_spec(doc_type) 获取平台规范：doc_type 传 manual(操作手册)/demo(演示文档)/acceptance(验收文档)
- 返回的 playbook 是完整可执行的操作指南，template 是必须严格遵循的格式模板

第二步：收集参数（缺什么问用户）
- system_url(系统地址)、username/password(登录账号)、modules(文档范围)、audience(目标读者)、title(标题)

第三步：实操系统并截图（关键，不能编造）
- 优先用 Playwright MCP 浏览器工具(browser_navigate/browser_take_screenshot/browser_click/browser_type)真实操作系统
- 若无浏览器工具，用 Bash 跑 Playwright 脚本代替
- 截图存到当前项目 docs/screenshots/ 目录：登录页→首页→每个目标模块的列表页和新增弹窗

第四步：按模板写文档并落盘
- 严格套用 tb_get_doc_spec 返回的 template 的章节编号/层级/顺序
- 每张截图用相对路径 ![](screenshots/NN_xxx.png) 引用，紧接一行 *图：说明*
- 操作步骤具体到按钮名称、输入内容、预期结果；禁止模糊词；禁止写死具体 URL
- 保存为 docs/{title}.md
""",
)


def _register(func, name: str, description: str):
    """注册一个 MCP 工具，直接查真实 DB。"""
    import functools
    import inspect

    sig = inspect.signature(func)
    params = list(sig.parameters.keys())
    has_session = "session" in params

    @functools.wraps(func)
    async def wrapper(**kwargs):
        if has_session:
            async with get_mcp_session() as session:
                return await func(session=session, **kwargs)
        return await func(**kwargs)

    wrapper.__doc__ = description
    new_params = [p for p in sig.parameters.values() if p.name != "session"]
    wrapper.__signature__ = sig.replace(parameters=new_params)

    mcp.tool(name=name)(wrapper)


# ── 测试用例工具 ─────────────────────────────────

_register(
    test_cases.list_cases,
    name="tb_list_cases",
    description="列出分支下的测试用例，支持分页和筛选。参数: branch_id(分支UUID), page, page_size, keyword, folder_id, priority(P0/P1/P2/P3), case_type(api/e2e)",
)

_register(
    test_cases.get_case,
    name="tb_get_case",
    description="获取单条测试用例的完整详情。参数: case_id(用例UUID)",
)

_register(
    test_cases.create_case,
    name="tb_create_case",
    description="创建一条功能测试用例，自动生成编号和目录。参数: branch_id, title, module(中文如'服务管理'), case_type(e2e/api), priority(P0-P3), preconditions(前置条件), steps([{seq,action,expected}]), expected_result",
)

_register(
    test_cases.get_folder_tree,
    name="tb_get_folder_tree",
    description="获取用例文件夹树形结构，含每层用例数量。参数: branch_id(分支UUID)",
)


# ── API 接口工具 ──────────────────────────────────

_register(
    api_endpoints.list_api_tree,
    name="tb_list_api_tree",
    description="获取项目下所有 API 接口的树形结构（文件夹和端点）。参数: project_id(项目UUID)",
)

_register(
    api_endpoints.get_api_node,
    name="tb_get_api_node",
    description="获取单个 API 节点详情（含 method, url, headers, body, auth 等）。参数: node_id(节点UUID)",
)

_register(
    api_endpoints.create_api_node,
    name="tb_create_api_node",
    description="创建 API 接口节点（endpoint 或 folder）。参数: project_id(项目UUID), name(名称), node_type(endpoint/folder,默认endpoint), method(GET/POST/PUT/DELETE等), url(接口路径), parent_id(可选,父文件夹UUID), params(可选,查询参数[{key,value,desc}]), headers(可选,[{key,value,desc}]), body(可选,请求体), body_type(可选,json/form/raw/none), auth(可选,{type,token}), description(可选), sort_order(排序,默认0)",
)


# ── 环境变量工具 ──────────────────────────────────

_register(
    environments.list_environments,
    name="tb_list_environments",
    description="列出所有测试环境。",
)

_register(
    environments.get_merged_variables,
    name="tb_get_merged_variables",
    description="获取合并后的变量（全局变量 + 环境变量，环境优先）。参数: env_id(环境UUID)",
)


# ── 测试报告工具 ──────────────────────────────────

_register(
    test_reports.get_report_summary,
    name="tb_get_report_summary",
    description="获取测试报告摘要（通过/失败/跳过/通过率 + 模块级分布）。参数: plan_id, report_id(可选)",
)

_register(
    test_reports.get_failed_scenarios,
    name="tb_get_failed_scenarios",
    description="获取报告中失败的用例（含步骤、错误信息）。参数: plan_id, report_id(可选)",
)


# ── 接口测试工具 ──────────────────────────────────

_register(
    api_tests.generate_api_test,
    name="tb_generate_api_test",
    description="【接口测试模块·单接口】给一个/少数接口定义，AI 生成一组测试场景（正向/参数校验/边界/安全），写入 api_test_scenarios（source_api_ids 关联接口，无 source_case_id）。用于只有接口文档、无法活体验证时。⚠ 与『用例·编排的接口场景』(tb_sync_orchestrated_scenario) 不是一回事。参数: branch_id(分支UUID), api_info(接口定义文本，含method/url/参数/响应), folder_name(可选，目标文件夹名)",
)

_register(
    api_tests.list_api_test_scenarios,
    name="tb_list_api_tests",
    description="列出接口测试场景。参数: branch_id(分支UUID), folder_id(可选), status(可选: draft/published/deprecated)",
)

_register(
    api_tests.get_api_test_scenario,
    name="tb_get_api_test",
    description="获取接口测试场景详情（含所有步骤、断言、变量提取）。参数: scenario_id(场景UUID)",
)

_register(
    api_tests.run_api_test,
    name="tb_run_api_test",
    description="执行接口测试场景并返回结果汇总。参数: scenario_ids(逗号分隔的场景UUID列表), env_id(可选但强烈建议：传了才注入该环境的 BASE_URL/账号/token，${BASE_URL} 这类引用才能解析)",
)


# ── 功能场景测试工具 ──────────────────────────────

_register(
    scenario_gen.create_scenario_task,
    name="tb_create_scenario_task",
    description="""创建功能测试用例生成任务（推荐方式，质量最高）。AI 自动提取需求点→生成场景模型→批量展开用例，有多阶段质量管控。
创建后需调用 tb_confirm_and_generate 推进流程。
参数: project_id(项目UUID), branch_id(分支UUID), title(任务名称), content_markdown(需求文档Markdown内容)""",
)

_register(
    scenario_gen.get_scenario_task,
    name="tb_get_scenario_task",
    description="查询功能场景测试生成任务的状态与进度。参数: task_id(任务UUID)",
)

_register(
    scenario_gen.confirm_and_generate,
    name="tb_confirm_and_generate",
    description="确认需求点和场景模型，自动推进到用例展开。在 tb_create_scenario_task 创建任务后调用。可多次调用查看进度。参数: task_id(任务UUID)",
)

_register(
    scenario_gen.query_coverage_matrix,
    name="tb_query_coverage_matrix",
    description="查询覆盖矩阵：需求点 × 测试维度的覆盖状态。参数: task_id(任务UUID), branch_id(分支UUID)",
)


# ── 项目与分支查询工具 ──────────────────────────────

_register(
    projects.list_projects,
    name="tb_list_projects",
    description="列出所有项目（名称、ID、描述）。用于确定要操作的目标项目。",
)

_register(
    projects.list_branches,
    name="tb_list_branches",
    description="列出项目下所有活跃分支。参数: project_id(项目UUID)",
)

_register(
    scenario_gen.get_generation_stats,
    name="tb_get_generation_stats",
    description="查询 AI 生成质量统计：通过率/拒绝率/总数。参数: branch_id(分支UUID)",
)


# ── UI 脚本工具 ──────────────────────────────────

_register(
    ui_scripts.generate_ui_script,
    name="tb_generate_ui_script",
    description="AI 生成 Playwright UI 测试脚本。读取用例步骤，调用 LLM 生成可执行的 Playwright Python 脚本并保存。参数: case_id(用例UUID), env_id(可选，环境UUID，用于获取 BASE_URL)",
)

_register(
    ui_scripts.run_ui_script,
    name="tb_run_ui_script",
    description="执行**单条**用例的 Playwright UI 脚本（聚焦调试用），返回通过/失败，失败自动截图。参数: case_id(用例UUID), env_id(环境UUID，必须包含 BASE_URL)",
)

_register(
    ui_scripts.run_ui_scripts_batch,
    name="tb_run_ui_scripts_batch",
    description="**批量**执行多条用例的 UI 脚本（回归用，AI-free 逐个跑真实 Playwright），返回聚合通过率。参数: case_ids(逗号分隔的用例UUID列表), env_id(环境UUID，含 BASE_URL)",
)

_register(
    ui_scripts.get_ui_script_result,
    name="tb_get_ui_script_result",
    description="获取用例最近一次 UI 脚本执行结果（状态、耗时、错误摘要、截图数）。参数: case_id(用例UUID)",
)


# ── 文档生成规范工具 ──────────────────────────────

_register(
    documents.get_doc_spec,
    name="tb_get_doc_spec",
    description="获取文档生成规范：操作流程 + 格式模板 + 写作规则。外部 Claude Code 用它按平台模板、实操被测系统、截图贴图生成操作/演示/验收文档。参数: doc_type(manual操作手册/demo演示文档/acceptance验收文档，默认manual)",
)


# ── 回推同步工具（活体验证成果写回）──────────────────

_register(
    sync.get_sync_spec,
    name="tb_get_sync_spec",
    description="【回推第一步·先调】获取回推规范：变量三层怎么选、步骤/断言/提取物 JSON 形状、禁止写死的正反例。参数: kind(case/api_scenario/variables/all，默认all)",
)

_register(
    sync.sync_orchestrated_scenario,
    name="tb_sync_orchestrated_scenario",
    description="【用例·编排的接口场景】把你**活体验证过**的多步接口链显式写回，绑定 source_case_id 并共享该用例场景变量。入库前硬拦截悬空 ${x}、软警告疑似写死。⚠ 与 tb_generate_api_test（单接口AI造）不是一回事。参数: project_id, branch_id, title, steps([{name,method,url,headers,body,assertions:[{type,operator,expected/value,field}],variables_extract:{name:jsonpath},group_name,enabled}]), source_case_id(强烈建议), folder_name(可选), priority(默认P1), description(可选)",
)

_register(
    sync.upsert_scenario_variables,
    name="tb_upsert_scenario_variables",
    description="回写/更新用例的场景变量（按 name upsert，UI+接口共用）。参数: case_id, variables([{name, kind(literal整段固定/random前缀随机/global_ref引用全局键/template部分固定部分随机如svc-{{$string:6}}), value_template, var_type, description}]), project_id(可选), branch_id(可选)",
)

_register(
    sync.list_scenario_variables,
    name="tb_list_scenario_variables",
    description="读取用例的所有场景变量（name/kind/value_template + 如何引用）。参数: case_id(用例UUID)",
)

_register(
    sync.list_global_data,
    name="tb_list_global_data",
    description="【回推前查】汇总项目级**可引用**全局数据（全局变量+各环境变量键+自动化共享资源，凭证脱敏），帮你判断哪些走 global_ref、哪些别写死。参数: project_id(项目UUID)",
)
