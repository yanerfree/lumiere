"""testBench MCP Server — 暴露平台数据能力，供 Web 引擎和 Claude Code 使用"""
from __future__ import annotations

from fastmcp import FastMCP

from app.mcp.deps import get_mcp_session
from app.mcp.tools import test_cases, api_endpoints, environments, test_reports, api_tests, scenario_gen, projects, ui_scripts, documents, sync, skills, plans, analysis, project_notes, mocks

mcp = FastMCP(
    name="testBench",
    instructions="""testBench 测试管理平台 MCP Server。

═══════════════════════════════════════════════════════════
【先看这里·选对工具，别搞混】
═══════════════════════════════════════════════════════════

① 生成「步骤用例」（功能用例）只有一条路：
   **先在被测系统里活体验证过，再用 tb_create_case 一条条回写成果。**
   （平台侧「喂需求文档批量产用例」那条流水线已下线 —— 凭文档想象出来的用例
     跑不通，也没人认。）

①-0 【动手之前先查这个模块已经有什么】调 tb_list_cases 带上 module 筛选，
   看清楚已有哪些场景（返回里带 title / 预期结果，还有 owes 告诉你每条还欠
   manual/api/ui 哪几维）。**同一个场景已经存在就不要再生成一条**，该补的是它欠的
   那一维。想接着上次没干完的活，传 pending_only=true。
   —— 平台会硬拒同模块下标题完全相同的用例，但换个说法就绕过去了，
   真正防重复的是你动手之前那一眼。

①-1 【怎么挑场景】这是**功能验证**，不是接口参数遍历。
   · 先把这个模块**页面上用户能做的事**盘一遍列出来，从页面出发、别从接口列表
     出发。按接口字段/参数排列组合切出来的是碎片，不是场景 —— 实测被打回过。
     盘全了再挑核心的，别捡边角料。
   · 一条用例 = 一个**能独立验证的完整流程**：配下去 → 真生效 →
     在用户看得见的地方验出来。
   · **合还是拆，判据只有一条：合并的唯一代价是「一挂全挂」。**
     只在「前面挂了后面本来也测不了」的天然链条上合（建 → 发布 → 调用通 →
     下线 → 调用不通），那时合并不丢任何信息；两个互不依赖的功能合成一条，
     只是让它们互相绑架。**前置很重、步骤超过一屏、要换角色或换环境的一律拆开** ——
     链越长越容易半路挂掉，挂了之后后面那几个功能这次就等于没测。
   · 涉及状态的功能（草稿/发布/下线/禁用），必须覆盖**切换之后**：切过去能不能用、
     切回来对不对、页面回显对不对、切到不可用状态后是不是真的访问不通。
     只写「创建成功」是漏了大头。

①-2 【标题怎么写】一眼要能看出在测什么：**对象 + 做了什么 + 预期结果**。
   好：「API 类型服务发布后可被调用」「服务下线后调用返回 403」
   坏：「测试服务管理」「创建服务」「异常场景」「参数校验」
   —— 标题是列表页唯一露出来的东西。写得笼统，以后所有人都得点进详情才知道
   你在测什么，几百条之后没人受得了。

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

   ④-0 【一个值该放哪一层？按这个顺序问，命中就停】
   默认放最窄的一层，只有明确需要共享才往上提。**放宽一层的代价是污染别人**。

     Q1 这个值只在本次执行有意义吗？（登录 token、刚创建对象的 id、列表里查到的临时 id）
        → 是：**步骤提取物** variables_extract。绝大多数值都停在这里，这是默认答案。
             ⚠ 提取物**永远不要**写回环境变量或共享资源——那是跨次运行的污染源。

     Q2 本条用例多个地方要用、且每次跑该换一个新的吗？（本次要创建的服务名、订阅备注）
        → 是：**场景变量**。要唯一就用 kind=random / template（如 svc-{{$string:6}}）。

     Q3 这条链会**修改/消耗/删除**它吗？（禁用它、审批掉它、删掉它、把它改成别的状态）
        → 是：**必须自己造**（路线 A：开头建、末尾删）。
             哪怕多条用例都要"一个服务"，只要各自会改它的状态，就各造各的。
             共享一个会被改状态的资源 = 用例之间互相打架，还偶发。

     Q4 多条用例都要用、只读引用不改它、且反复重建代价大吗？
        （上游/负载、隔离上下文、长期存在的消费方应用）
        → 是：**项目级共享资源**（路线 B：查→没有就自己造且不清理→登记 exists_check）。

     Q5 它是"这个环境是什么"而不是"这次测什么"吗？（BASE_URL、登录路径、各角色账号密码）
        → 是：**环境变量**，人工在环境管理里维护，每个环境一套。
             你不要把它写进场景变量，也不要在测试里去改它。

   一句话判据：**会被改的别共享，只读的才配共享；能停在提取物就别往上提。**

   ④-1 【别把环境变量镜像成场景变量】环境变量（BASE_URL/LOGIN_URL/账号密码等）执行时
   由平台直接注入，步骤里写 ${BASE_URL} 就能用，**不需要**再建一个 kind=global_ref 的
   同名场景变量。多建一层只是噪音，还让人以为值存在用例里。场景变量只用来放
   「这条用例自己的数据」（如本次要创建的服务名 svcName）。

   ④-2 【前置数据你自己造，不许写死 UUID】编排链常依赖别的资源
   （上游/负载 upstreamId、隔离上下文 isolationId、被订阅的应用 appId……）。
   把这些 UUID 存成 kind=literal 场景变量是**错的**——换环境或该资源被删就全挂。
   **造数据是你的活**，别指望环境里"刚好有"。按资源性质二选一：

   ▸ 路线 A【场景自足】：这条用例自己的数据（本次要创建的服务、要发起的订阅……）
     场景开头加步骤**真的调接口创建** → variables_extract 提取 id → 末尾加步骤删掉。
     自建自删，跑一百遍都干净。优先走这条，能自足就别依赖外部。

   ▸ 路线 B【共享基础数据】：多条用例都要用、且反复重建代价大的底座
     （上游/负载、隔离上下文、长期存在的消费方应用……）。三步走：
       1. 先 tb_list_global_data 查项目里有没有登记过；
       2. **没有 → 你自己调接口把它造出来**（活体验证时就造，这是你的责任），
          造完**不要清理**，它要留给后续场景复用；
       3. 造好后（或本来就有）用 tb_upsert_automation_resource 登记 exists_check
          —— 写明"怎么按名字/条件找到它 + 从响应里抽哪个字段当 id"。
          之后每次跑，平台会在第一个步骤之前自动探一次并注入 ${资源名}，
          换环境也能自动找到那个环境里的对应资源。
     注意 exists_check 要用**稳定标识**（name/code 这类）去 match，不要用 id，
     否则换环境照样匹配不上，等于换个地方写死。

   判断标准：这条链换到一个**干净环境**还能不能跑通？
   跑不通说明前置数据没交代清楚——要么 A 里没造全，要么 B 里漏了第 2 步。

⑤ 【默认先活体验证，别凭文档编】只要能连上被测系统（有可访问的环境地址和账号），
   就**必须真的把接口调一遍**把流程跑通——拿到真实响应结构、真实字段名、真实状态码，
   再据此回推。不要读完接口文档就直接生成。
   · tb_generate_api_test 仅限**确实拿不到可访问环境**时使用，它是让平台 AI 凭文档造，
     质量明显更差。不能因为省事就走它。
   · 判断依据是"能不能连上"，不是"手上有没有文档"。有文档但环境也能连 → 仍然要活体验证。

⑥ 【一个用例 = 一条接口场景】tb_sync_orchestrated_scenario 按 source_case_id 幂等：
   同一条用例重推**永远覆盖那一条**（步骤整体替换、code 不变、标题以最新一次为准），
   不会新增。所以补完步骤尽管重推，标题也可以改。
   反过来说：**不要试图给同一条用例推多条场景**——用例详情里只呈现一条、只有一套编辑器，
   多推只会互相覆盖。一条用例要覆盖多个流程时，应该拆成多条用例。
   返回值里的 replacedExisting 告诉你这次是覆盖还是新建。

⑦ 【动库之前先报清单，等用户确认】调用任何写库工具（tb_create_case /
   tb_sync_orchestrated_scenario / tb_upsert_scenario_variables / tb_create_api_node）
   之前，先把清单列给用户看，**得到确认再执行**。清单一条一行，四列：

     场景名称 | 这条验什么（一句话） | 用户在哪儿看得到 | 库里已有吗（标出相似的那条编号）

   「验什么」那一列不能省 —— 光看场景名，用户判断不出你有没有理解跑偏，
   而这正是这道确认唯一拦得住的事故。
   「用户在哪儿看得到」是**你自己的筛子**：说不出落点的那几条，基本就是接口碎片，
   报清单之前自己先划掉。
   也要让用户能只改其中几条（"去掉 3、5"），只给"确认/取消"的话，
   他发现一条不对就只能整个推倒，几次之后就闭眼确认了。

⑧ 【回推必须带你亲手跑过的证据】接口场景带上真实请求/响应，UI 脚本带上本地
   跑通的结果。红线是「回推的是脚本不是结论」—— 没跑过就回推，等于把想象
   写进了事实库，而它长得和真验过的一模一样，事后分辨不出来。

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

第五步：UI 自动化脚本（可选，用户要求时执行）
- **平台不生成脚本**。你在自己机器上用 Playwright 写、本地真跑通，再调
  tb_sync_ui_script 回推；入库前会硬拦截写死的服务地址和凭据。
- 写之前先调 tb_get_sync_spec(kind='ui_script') 对齐写法（变量必须走
  os.getenv 顶格声明，平台执行时把所选环境的真值替换进默认值）。
- 回推后调 tb_run_ui_script(case_id, env_id) 让**平台**在标准环境上跑一遍确认——
  你本机跑通不算数（本机有 dev server、有残留数据、有 cookie）。

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

当用户要求把本项目的 skill 传到平台 / 从平台取 skill 时：

【推上去】
- 读本地 .claude/skills/<name>/SKILL.md（有 references/ 等附属文件一起读）
- 调 tb_push_skill(project_id, content=SKILL.md全文, files={相对路径:文本})
- name 不传就取 frontmatter 里的 name；同名会覆盖，覆盖前自动留档可回滚
- visibility 默认 public（其它项目可取用），只想自己用就传 project
- 一次推多个就循环调用，**推之前把清单报给用户确认**（同 ⑦ 条纪律）

【取下来】
- tb_list_skills(project_id) 看有哪些 → tb_pull_skill(skill_id=...) 拿全文
- 按返回的 writeTo / extraWriteTo 路径写进本地 .claude/skills/，写完告诉用户重启会话才生效

【边界·别搞混】本通道存的是**客户端侧执行**的 skill —— 跑在开发者机器的
Claude Code 里，用 Bash/Edit/Playwright 这些本地工具。平台只做存取，
永不把它们当 prompt 执行。内置的 tb-* 是另一类（平台侧执行、绑 AI 模型档位），
不在本通道里，也不要试图用 tb_push_skill 覆盖它们。
""",
)


# 工具目录 —— 前端「MCP 工具中心」和 Key 级工具档位都从这里取，
# 避免再出现"前端硬编码 20 条、后端实际注册 32 条"的漂移。
TOOL_CATALOG: list[dict] = []

_current_category = "其它"


def _section(category: str):
    """标记后续 _register 调用所属分类（本模块自上而下线性执行一次）。"""
    global _current_category
    _current_category = category


def _register(func, name: str, description: str):
    """注册一个 MCP 工具，直接查真实 DB。同时登记进 TOOL_CATALOG。"""
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

    TOOL_CATALOG.append({
        "name": name,
        "description": description,
        "category": _current_category,
        "params": ", ".join(p.name for p in new_params) or "无",
    })
    mcp.tool(name=name)(wrapper)


# ── 测试用例工具 ─────────────────────────────────

_section("用例·手工步骤")

_register(
    test_cases.list_cases,
    name="tb_list_cases",
    description="列出分支下的用例（手工步骤那一层）。找已有用例、确认编号、看某模块测了哪些时用。**断点续跑靠它**：传 pending_only=true 只返回还欠着的那些 —— target_level 说这条要做到哪一步（spec 只要步骤 / spec_api 步骤+接口 / full 三件套），三个维度状态说已经做到哪一步，差集就是待办；返回里的 owes 直接列出还欠哪几维。中断之后重跑不用从头来，也不会把做完的又捡回来重做。参数: branch_id(分支UUID), page, page_size, keyword, folder_id, module(按模块名，省得先查folder_id), priority(P0/P1/P2/P3), case_type(api/e2e), target_level(spec/spec_api/full), ui_status/api_status/manual_status(not_started/draft/debugging/pending_review/executable/needs_fix), pending_only(默认false)",
)

_register(
    test_cases.get_case,
    name="tb_get_case",
    description="读一条用例的全部内容：手工步骤、前置条件、预期结果、模块归属。改它或给它挂接口场景之前先读一遍。参数: case_id(用例UUID)",
)

_register(
    test_cases.create_case,
    name="tb_create_case",
    description="新建一条用例（手工步骤）。用例是「测什么」的载体——接口场景和 UI 脚本都挂在它下面，所以先有用例再有脚本。编号和目录自动生成。**入库要过门禁**：标题完全同名硬拒、标题含模糊词（操作成功/显示正常/无报错/符合预期）硬拒。标题相似只提醒不拦。**P0 三件套不拦你**：同源生成的三份产物容易互相一致而不正确（典型是把「创建成功」做成「返回 200」），所以建议先在对话里跟用户确认这个场景到底要验什么，再把确认内容用 expected_confirmed_by / expected_confirmed_note 带上来 —— 平台只记录、不拦截，没带只回一句提醒。参数: branch_id, title, module(中文如'服务管理'), case_type(e2e/api), priority(P0-P3), preconditions(前置条件), steps([{seq,action,expected}]), expected_result, target_level(这条要做到什么程度: spec只要步骤/spec_api步骤+接口/full三件套，默认spec), expected_confirmed_by(跟谁确认的), expected_confirmed_note(确认了什么，把对话里那句原话带上来)",
)

_section("Mock 与观测")

_register(
    mocks.llm_mock_status,
    name="tb_llm_mock_status",
    description="LLM Mock 服务在不在、有哪些路由、被测网关的上游地址该填什么。测 AI 网关绕不开它：用真上游测网关又慢又费钱又不确定，挂了还分不清是网关的锅还是模型的锅。",
)

_register(
    mocks.upsert_llm_mock_route,
    name="tb_upsert_llm_mock_route",
    description="建/改一条 LLM Mock 路由，决定「上游怎么答」——status_code(429/500 测重试降级熔断)、delay_ms(测超时)、finish_reason(stop/length/content_filter 测透传)、prompt_tokens/completion_tokens(**测网关的计费/配额统计算得对不对**)、model(测模型映射)。按 path 幂等。**path 必须带你自己的前缀**（如 /mock/TC-XXX-00001/v1/chat/completions）：直接占用 /v1/chat/completions 会被拒，那是所有用例共用的，你配成 429 别人就跟着挂、还偶发。参数: name, path, status_code(默认200), delay_ms, response_body, finish_reason, prompt_tokens, completion_tokens, model",
)

_register(
    mocks.llm_mock_requests,
    name="tb_llm_mock_requests",
    description="**网关到底往上游发了什么** —— 断言用的。鉴权头有没有正确注入、模型名有没有按映射改写、参数有没有被篡改，这些在网关下游根本看不见（客户端只能看到最终响应），这是唯一的观测点。断言前先 tb_llm_mock_reset 清一次。参数: path(可选), limit(默认20)",
)

_register(
    mocks.llm_mock_reset,
    name="tb_llm_mock_reset",
    description="清掉上游请求记录。**断言「上游收到几次」之前必须先清** —— 不清的话上一轮的记录还在，「只应收到 1 次」会假过，而假过比假红更难发现。参数: path(可选，不传清全部)",
)

_register(
    mocks.proxy_capture,
    name="tb_proxy_capture",
    description="代理观测抓到的真实请求 —— 写接口场景的素材来源。活体验证最费劲的一步是「这个页面动作到底发了哪些请求、body 长什么样」，自己开 devtools 抄又慢又容易抄错，而平台的代理已经记下来了。参数: limit(默认50)",
)


_section("项目须知")

_register(
    project_notes.list_project_notes,
    name="tb_list_project_notes",
    description="列出项目须知 —— **动手写用例之前先读一遍**。里面是前人（和你自己上几轮）踩出来的坑：接口哪个行为反直觉、哪个状态会连带改别的字段、哪个角色走的是另一条路径。不知道这些就会写出错的断言，然后把「被测系统本来就这样」当成 bug 报上去。参数: project_id(项目UUID), category(可选: api_note接口/系统行为 / bug_pattern踩过的坑 / custom其它)",
)

_register(
    project_notes.add_project_note,
    name="tb_add_project_note",
    description="把这一轮撞出来的坑写回项目须知，别让下一轮再踩一遍。**一条只说一件事，正文 200 字以内**（超了直接拒，不截断），写成「现象 + 别踩的坑」。只记你亲手撞到的**事实**（「这个接口 404 有两种：上游的 404 和网关无路由的 404，只断状态码会误判」），不记判断结论（结论会过期，事实不会）。同标题覆盖。参数: project_id, title, content, category(api_note/bug_pattern/custom，默认 api_note)",
)


_register(
    test_cases.update_case,
    name="tb_update_case",
    description="改一条已有用例的内容（只传要改的字段，没传的原样不动）。**你写错了自己改，别喊人** —— 标题打错字、步骤和实测不符（比如写「跳转回列表」、实际跳的是详情页），都用这个修。过的是和建用例同一套门禁（模糊词硬拒、同模块同名硬拒、步骤粒度自动拆），同名检查会排除自己。**改不了状态**：ui_status/api_status/manual_status 一概不收 —— 状态由平台按执行事实推进或由人拍板；你要说「这条能跑了」，就去跑一遍让结果说话。改了步骤或预期结果会自动清掉「预期已确认」标记（返回里会提醒），要重新跟用户对一遍。参数: case_id(用例UUID), title, priority, preconditions, steps([{seq,action,expected}]), expected_result, target_level(spec/spec_api/full), expected_confirmed_by, expected_confirmed_note",
)

_register(
    test_cases.get_folder_tree,
    name="tb_get_folder_tree",
    description="用例目录树 + 每层用例数。决定新用例该放哪个模块时用。参数: branch_id(分支UUID)",
)


# ── API 接口工具 ──────────────────────────────────

_section("接口库·只记怎么调")

_register(
    api_endpoints.list_api_tree,
    name="tb_list_api_tree",
    description="列出项目的**接口库**目录树。接口库只记「系统有哪些接口、怎么调」，没有断言、不能执行——编排接口场景之前来这里查接口长什么样。参数: project_id(项目UUID)",
)

_register(
    api_endpoints.get_api_node,
    name="tb_get_api_node",
    description="读单个接口的调用方式：method / url / params / headers / body / auth。拿它去拼接口场景的步骤。参数: node_id(节点UUID)",
)

_register(
    api_endpoints.create_api_node,
    name="tb_create_api_node",
    description="往**接口库**里加一个接口或文件夹。这是在维护接口文档，不产生可执行的测试——要可执行的用 tb_sync_orchestrated_scenario。参数: project_id(项目UUID), name(名称), node_type(endpoint/folder,默认endpoint), method(GET/POST/PUT/DELETE等), url(接口路径), parent_id(可选,父文件夹UUID), params(可选,查询参数[{key,value,desc}]), headers(可选,[{key,value,desc}]), body(可选,请求体), body_type(可选,json/form/raw/none), auth(可选,{type,token}), description(可选), sort_order(排序,默认0)",
)


# ── 环境变量工具 ──────────────────────────────────

_section("环境与变量")

_register(
    environments.list_environments,
    name="tb_list_environments",
    description="列出所有测试环境（环境名 + envId）。跑任何场景前都得先选一个，拿到的 envId 传给 tb_run_api_test。",
)

_register(
    environments.get_merged_variables,
    name="tb_get_merged_variables",
    description="看某个环境执行时实际会注入哪些变量（全局变量 + 该环境变量，同名以环境为准）。排查「变量未解析」先查这里。参数: env_id(环境UUID)",
)


# ── 测试报告工具 ──────────────────────────────────

_section("失败归因")

_register(
    analysis.submit_analysis,
    name="tb_submit_analysis",
    description=(
        "【失败归因】把你对某次失败的**原因**判断写回平台。先调 tb_get_ui_script_result 拿证据包和 run_id，"
        "看完截图、流量再来判。"
        "⚠ 这条**不会改任何状态**：不动用例状态、不进通过率、不改报告结论 —— 它进「待确认」队列，人拍板才算数。"
        "⚠ evidence 必须**指向平台侧证据的具体位置**（哪条请求 / 哪句 error_summary / 第几张截图），"
        "只有你自己的推理会被直接拒收；引用的东西这次执行里必须真有，否则也拒。"
        "⚠ 拿不准就 cause=unknown + confidence=low。低置信配一个具体 cause 会被拒 —— "
        "一个看起来很有道理的错答案，比一句「我不知道」有害得多。"
        "参数: run_id(执行记录UUID), "
        "cause(product_defect被测系统缺陷/test_defect脚本自己写错/case_expired需求变了用例过期/"
        "env_issue环境依赖问题/data_issue数据问题/flaky不稳定/unknown看不出来), "
        "confidence(high/medium/low), reasoning(为什么是这个原因而不是别的，写不出因果的归因基本是瞎猜), "
        "evidence([{type:error_summary|request|screenshot|stdout|phenomenon, ref:具体位置}]), "
        "proposed_fix_target(script/product/data/case/env/none)"
    ),
)

_register(
    analysis.list_pending_confirm,
    name="tb_list_pending_confirm",
    description="【失败归因】列出「已归因、还没人确认」的失败 —— 你交上去还没被拍板的那些。参数: project_id(可选), limit(默认20)",
)

_section("执行报告")

_section("执行报告")

_register(
    plans.list_plans,
    name="tb_list_plans",
    description="【执行报告】列出项目下的测试计划，拿 planId。**这是入口** —— tb_get_report_summary / tb_get_failed_scenarios 都要 planId，没有它那两个工具根本用不了。返回含用例数、最近一次 reportId。参数: project_id(项目UUID), status(可选: draft/executing/completed), limit(默认20)",
)

_register(
    plans.create_plan,
    name="tb_create_plan",
    description="【执行报告】新建一个自动化测试计划（只建不跑，触发要另调 tb_run_plan）。参数: project_id, branch_id, name(计划名), case_ids(逗号分隔的用例UUID), test_type(e2e跑UI脚本/api跑接口脚本，默认e2e), environment_id(强烈建议传，不传执行时拿不到 BASE_URL 和账号), retry_count(默认0)",
)

_register(
    plans.run_plan,
    name="tb_run_plan",
    description="【执行报告】触发计划在**平台执行器**上跑（这一跑算回归、进通过率口径）。立刻返回 taskId 和 reportId，执行是异步的 —— 拿 reportId 轮询 tb_get_report_summary。注意：你只是按了按钮，结果由平台执行器写；你不能写执行结果、也不能改用例通过状态。参数: plan_id(计划UUID)",
)

_register(
    plans.list_reports,
    name="tb_list_reports",
    description="【执行报告】列出测试报告，拿 reportId + 通过率。参数: project_id(项目UUID), plan_id(可选，按计划过滤), limit(默认20)",
)

_register(
    test_reports.get_report_summary,
    name="tb_get_report_summary",
    description="一次执行的总览：通过 / 失败 / 跳过 / 通过率，以及按模块的分布。参数: plan_id, report_id(可选)",
)

_register(
    test_reports.get_failed_scenarios,
    name="tb_get_failed_scenarios",
    description="【执行报告】拿这次报告里所有失败的用例，**每条带 runId** —— 用它调 tb_get_ui_script_result 看证据包（截图路径 / 流量 / 平台的现象初判），判完再调 tb_submit_analysis 回填归因。参数: plan_id(计划UUID), report_id(可选，不传取最近一次)",
)


# ── 接口测试工具 ──────────────────────────────────

_section("接口场景·可执行")

_register(
    api_tests.generate_api_test,
    name="tb_generate_api_test",
    description="【接口测试模块·单接口】给一个/少数接口定义，AI 生成一组测试场景（正向/参数校验/边界/安全），写入 api_test_scenarios（source_api_ids 关联接口，无 source_case_id）。用于只有接口文档、无法活体验证时。⚠ 与『用例·编排的接口场景』(tb_sync_orchestrated_scenario) 不是一回事。参数: branch_id(分支UUID), api_info(接口定义文本，含method/url/参数/响应), folder_name(可选，目标文件夹名)",
)

_register(
    api_tests.list_api_test_scenarios,
    name="tb_list_api_tests",
    description="列出分支下的**接口场景**（可执行的那种：多步 + 断言 + 变量提取）。参数: branch_id(分支UUID), folder_id(可选), status(可选: draft/published/deprecated)",
)

_register(
    api_tests.get_api_test_scenario,
    name="tb_get_api_test",
    description="读一条接口场景的全部内容：每一步的请求、断言、提取了什么变量。想知道它到底怎么测的就读这个。参数: scenario_id(场景UUID)",
)

_register(
    api_tests.run_api_test,
    name="tb_run_api_test",
    description="**真的跑一遍**接口场景，返回每步的状态码和断言结果。参数: scenario_ids(逗号分隔的场景UUID列表), env_id(可选但强烈建议：传了才注入该环境的 BASE_URL/账号/token，${BASE_URL} 这类引用才能解析)",
)


# ── 需求→用例流水线：已下线 ────────────────────────
#
# `tb_create_scenario_task` / `tb_confirm_and_generate` / `tb_get_scenario_task` /
# `tb_query_coverage_matrix` / `tb_get_generation_stats` 五个工具已摘除，
# 平台侧「AI 生成用例」的页面入口同时下线。
#
# 原因是实测数据：8 个批次里 3 个卡在 model_ready 半路、2 个 failed，最近一次
# 07-13。**一半批次卡在中间态，一个月无人问津** —— 这条路的形态（先喂文档、
# 先建任务、再确认、再等平台跑）对着一个手上就有 Claude Code 的用户，仪式太重，
# 用户用脚投票了。留着它的代价不只是死代码：CC 走「全量」档时还能开出一个
# 没有页面可以看的任务，跑到一半没人知道。
#
# **实现和数据一概不动**：`app/services/scenario_gen/`、`generation_tasks` 等 7 张表、
# `/api/scenario-gen/*` 接口全部保留。49 条老用例还挂着 `generation_task_id`，
# 删表会伤到它们。这里下线的只是**入口**。


# ── 项目与分支查询工具 ──────────────────────────────

_section("定位项目/分支")

_register(
    projects.list_projects,
    name="tb_list_projects",
    description="列出所有项目。几乎每个工具都要 project_id，一般从这里起步。",
)

_register(
    projects.list_branches,
    name="tb_list_branches",
    description="列出项目下的活跃分支。用例和接口场景都挂在分支上，branch_id 从这里拿。参数: project_id(项目UUID)",
)


# ── UI 脚本工具 ──────────────────────────────────

_section("UI 脚本")

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
    description="【失败证据包】拿这条用例最近一次 UI 执行的**完整证据**，用来判断为什么挂：截图（返回**文件路径**，和平台同机，直接 Read 打开看图）、网络流量摘要（按状态码分桶 + 展开非 2xx 和写操作那几条，其余页面自身的 GET 已折叠）、stdout 尾部、以及平台按确定性规则给的**现象**初判 failure_phenomenon（timeout / element_not_found / assertion_mismatch / http_5xx / script_error / dependency_unresolved / unknown）。⚠ 现象不是归因 —— 平台判「是什么」，「为什么」由你判断。参数: case_id(用例UUID)",
)


# ── 文档生成规范工具 ──────────────────────────────

_section("文档规范")

_register(
    documents.get_doc_spec,
    name="tb_get_doc_spec",
    description="获取文档生成规范：操作流程 + 格式模板 + 写作规则。外部 Claude Code 用它按平台模板、实操被测系统、截图贴图生成操作/演示/验收文档。参数: doc_type(manual操作手册/demo演示文档/acceptance验收文档，默认manual)",
)


# ── 回推同步工具（活体验证成果写回）──────────────────

_section("回推入库")


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
    sync.upsert_automation_resource,
    name="tb_upsert_automation_resource",
    description="【共享基础数据·登记怎么找到它】路线B专用：多条用例共用、反复重建代价大的底座(上游/负载、隔离上下文、长期存在的应用)。用法=先 tb_list_global_data 查有没有 → 没有就**你自己调接口造出来且不清理** → 再用本工具登记 exists_check。之后每次跑，平台在第一步之前自动探测并注入 ${资源名}，换环境也能找到对应资源。注意 match 要用 name/code 这类稳定标识，不能用 id(等于换个地方写死)；探不到时平台**不会**替你补建（create_def 只登记备查、不执行），只会报「变量未解析」——要补就调 tb_list_global_data(probe=true, env_id=...) 看哪条 state=missing，然后你自己按它的 createDef 造。只属于本条用例的数据别用这个，那种该在场景开头自建、末尾清理。参数: project_id, name(引用名), exists_check(必填,{method,url,match,extract}), create_def(可选,登记备查当初怎么造的), description, keep(默认true)",
)

_register(
    sync.sync_ui_script,
    name="tb_sync_ui_script",
    description=(
        "【用例·UI 脚本】把你在本地写好并**跑通过**的 Playwright 脚本回推到某条用例的「UI 测试」页签。"
        "入库前硬拦截写死的服务地址和凭据（换环境必挂），并检查有没有 pytest/playwright 认得的测试函数。"
        "写之前先调 tb_get_sync_spec(kind='ui_script') 看变量怎么取、模板长什么样。"
        "回推后用 tb_run_ui_script(case_id, env_id) 在目标环境上真跑一遍确认。"
        "参数: case_id(用例UUID), content(脚本正文，不是路径), "
        "language(可选 python/typescript，不传自动判), file_name(可选)"
    ),
)

_register(
    sync.list_global_data,
    name="tb_list_global_data",
    description="【回推前查】汇总项目级**可引用**全局数据（全局变量+各环境变量键+自动化共享资源，凭证脱敏），帮你判断哪些走 global_ref、哪些别写死。**传 probe=true + env_id 会在该环境上真探测一遍共享资源**，每条给出 state：exists=探到了(附 extract 抽出的 values) / missing=确实没有，照它的 createDef 你自己调接口造出来（造完不用改配置，existsCheck 下次自然探得到）/ unknown=平台没查成(401、5xx、超时)，**别动它**——一次 token 过期就照 createDef 补建，会在被测环境造出一堆重复底座且 keep=true 没人清理。平台**不执行** createDef，只告诉你缺了什么、当初怎么造的。参数: project_id(项目UUID), env_id(可选，probe=true 时必填), probe(默认false)",
)


# ── Skill 共享 ───────────────────────────────────

_section("Skill 共享")

_register(
    skills.push_skill,
    name="tb_push_skill",
    description="【把本项目的 skill 推上平台】读你本地 .claude/skills/<name>/ 的内容，推到 testBench 存起来，默认 visibility=public 即其它项目可取用。存的是**客户端侧执行**的 skill（跑在开发者机器的 Claude Code 里，用 Bash/Edit/Playwright），平台只存取、永不当 prompt 执行 —— 跟内置 tb-* 不是一类。同名会覆盖，覆盖前自动留档可回滚。参数: project_id(项目UUID), content(SKILL.md全文,必填), name(可选,不传则取 frontmatter 里的 name), files(可选,附属文件{相对路径:文本内容},如 references/api.md), description(可选,不传取 frontmatter), visibility(public全平台可取/project仅本项目,默认public), overwrite(默认true)",
)

_register(
    skills.list_skills,
    name="tb_list_skills",
    description="【看平台上有哪些 skill 可取用】列出可用的项目 skill(客户端侧执行那类)。传 project_id = 本项目的 + 全平台共享的；不传 = 只看全平台共享的。返回里的 skillId 拿去喂 tb_pull_skill。参数: project_id(可选,项目UUID), include_shared(默认true)",
)

_register(
    skills.pull_skill,
    name="tb_pull_skill",
    description="【把平台上的 skill 取到本地】拿一个 skill 的全文和附属文件，返回里带 writeTo 落盘路径(.claude/skills/<name>/SKILL.md)，照着写文件即可。定位二选一：skill_id(推荐,跨项目取用用它,要求该 skill 是 public)，或 project_id + name(取自己项目的,不受 public 限制)。参数: skill_id(可选), project_id(可选), name(可选)",
)


# ── 工具范围硬约束 ────────────────────────────────
# 按 API Key 过滤 tools/list 并拦截越权 tools/call。必须在全部 _register 之后注册。
from app.mcp.middleware import ToolScopeMiddleware  # noqa: E402

mcp.add_middleware(ToolScopeMiddleware())
