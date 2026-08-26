"""Lumiere MCP Server — 暴露平台数据能力，供 Web 引擎和 Claude Code 使用"""
from __future__ import annotations

from fastmcp import FastMCP

from app.mcp.deps import get_mcp_session
from app.mcp.tools import test_cases, api_endpoints, environments, test_reports, api_tests, scenario_gen, projects, ui_scripts, documents, sync, skills, plans, analysis, project_notes, mocks, deliverable, review, duty, branch_diff

mcp = FastMCP(
    name="Lumiere",
    instructions="""Lumiere 测试管理平台 MCP Server。

═══════════════════════════════════════════════════════════
【先看这里·选对工具，别搞混】
═══════════════════════════════════════════════════════════

⓿ 【每轮上来先问"该干什么"，别自己拍】tb_next_duty 按分支给出四个队列
   （归因待办 / 失败复跑 / 缺场景 / 自证不全），每条带「下一步该调哪个工具」。
   交付前自己过门禁：tb_check_deliverable 直接说卡在哪一步；只想看一个模块的
   欠账用 tb_module_checkup，想让评审先挑一遍毛病用 tb_review_case。
   新版本分支要复用上一版用例，先 tb_check_branch 对账 —— 别照着旧清单硬抄。
   **这些工具怎么判、判据是什么，看它们各自的描述和返回值，这里不复述。**

① 生成「步骤用例」（功能用例）只有一条路：
   **先在被测系统里活体验证过，再用 tb_create_case 一条条回写成果。**
   （平台侧「喂需求文档批量产用例」那条流水线已下线 —— 凭文档想象出来的用例
     跑不通，也没人认。）

①-A 【预期从哪来 —— 这一条最要紧，看漏了整批用例都白写】
   活体验证解决的是**「路径怎么走、跑不跑得通」**，
   它**不能**用来决定**「结果应该是什么」**。

   把实测到的行为直接写成预期，等于**把系统当前的实现当成正确答案**。
   系统要是有 bug，你就把 bug 固化成了「预期」，而且步骤、接口场景、UI 脚本
   三份产物同源，会互相一致地一起错 —— 全绿，全错，没人看得出来。
   **那不是测试，那只是把现状抄了一遍。**

   做法是三步，不是"挑一个权威来抄"：

     ① 读需求 —— 它**应该**怎么做。
        PRD、设计文档、README、docs/、接口契约（OpenAPI/proto）、
        代码里的业务规则注释和校验分支。你是 Claude Code，仓库就在手边，直接读。
     ② 读实现 + 实测 —— 它**实际**怎么做。
        看代码里那段逻辑，再真跑一遍拿到真实字段名、状态码、响应结构。
     ③ **自己比对，自己判断** —— 测试的价值全在这一步。

   比对出来只有三种结果，各走各的路：

     · **一致** → 预期就按它写，落款写依据（例：`docs/订阅.md §3.2` + 实测确认）。
       **这种不用问任何人。** 绝大多数都落在这里。

     · **不一致** → **这就是你要找的东西**。预期**按需求写**（不是按实测写），
       用例会红 —— 红得对。然后调 tb_submit_analysis 提 cause=product_defect，
       进待确认队列。**这时候才需要人**：人确认它确实是缺陷。
       确认之后交付门禁就不把这条红算作阻塞了（缺陷修好它自己转绿）。
       ⚠ 把预期改成实测值让它变绿 = 替被测系统把 bug 洗白，这是最严重的一种假绿。

     · **需求没覆盖 / 需求本身说不清** → 先自己判断：同类功能怎么做的、
       行业惯例是什么（越权该 403 不是 401、删除该幂等、分页该有上限）。
       判得出来就按判断写，并在 note 里写明「需求未覆盖，按 X 判断」。
       **判不出来才问用户，而且要带着判断去问**：
       「文档 §3.2 说 A，实测是 B，我倾向 A 因为…… 你确认哪个对」，
       不是「这 5 条你看对不对」—— 后者是把判断成本整个推给人，
       几次之后人就闭眼说"都对"，这道确认也就废了。

   ── 什么时候该找人 ──

   **默认自己判断，别事事找人确认。** 但下面这些**必须**找人，别自己扛：

     · 定需求：需求缺失或含糊，而你判不出该是什么（判得出就自己判，见上）
     · 确认 bug：实测和需求不一致，你认为是缺陷 —— 提归因让人拍板
     · **卡住了**：环境连不上、账号没权限、依赖的数据造不出来、
       同一个坎试了两三次还是过不去 —— **说出来，别硬试**。
       闷头试一小时不如问一句；这里没有"问了显得不行"这回事。
     · **发现了这条用例之外的问题**：别的模块的缺陷、平台工具本身不对劲、
       文档和代码互相矛盾 —— 顺手说一声，别因为"不在这次范围内"就咽下去。
     · **要做影响别人的动作**：改平台级开关/全局配置、删除或改动不是你造的数据、
       动别的用例 —— 先说再做。这类事回滚代价高，而且会让并跑的用例莫名其妙挂掉。

   问的方式同上 —— **带着你的判断和依据去问**，别甩清单，见 ①-A 那段。

   反过来也一样：**能自己判的别攒着等人**。攒一批"待确认"扔过去，
   人要一条条重新理解上下文，比你当场判贵得多。

①-0 【动手之前先查这个模块已经有什么】调 tb_list_cases 带上 module 筛选，
   看清楚已有哪些场景（返回里带 title / 预期结果，还有 owes 告诉你每条还欠
   manual/api/ui 哪几维）。**同一个场景已经存在就不要再生成一条**，该补的是它欠的
   那一维。想接着上次没干完的活，传 pending_only=true。
   —— 平台会硬拒同模块下标题完全相同的用例，但换个说法就绕过去了，
   真正防重复的是你动手之前那一眼。

   ⚠ **判重只看 tb_list_cases，别拿接口场景当依据**。tb_list_api_tests 返回里
   分了两组：`boundToCases`（一个用例一条）和 `standalone`（**无主场景**）。
   `standalone` 现在**恒为空** —— 2026-08-15 清了存量 47 条并把
   source_case_id 收成 NOT NULL + 外键 CASCADE，写不出无主场景了。
   它还留着是当哨兵：**里面一旦有东西，说明有人绕过了约束，那不是谁的产物，别算进判重**。
   实测跑偏过一次：CC 看到孤儿场景 AT-0009 全绿，就不写新用例、改去"补用例重绑"。

①-1 【怎么挑场景】这是**功能验证**，不是接口参数遍历。

   **场景清单和预期一样，来自「需求 + 实现」两边，不是只看页面。**
   只从页面盘，盘出来的是"这个系统做了什么"；需求里写了、实现漏做的那些功能，
   页面上根本没有入口，你永远盘不到 —— **漏测的恰恰是这一类**。

   · 先读需求：这个模块**应该**有哪些能力、哪些角色、哪些状态流转、
     哪些约束（限额、时效、越权边界）。列成清单。
   · 再盘**页面上用户能做的事** + 接口：**实际**能做到什么。别从接口列表出发 ——
     按接口字段/参数排列组合切出来的是碎片不是场景，实测被打回过。
   · 两边比对：
       两边都有   → 正常场景，挑核心的做
       需求有、实现没有 → **功能缺失，这是缺陷**。照需求写用例让它红，
                          提 product_defect 归因。别因为"页面上没这个按钮"就跳过
       实现有、需求没有 → 多出来的行为。先判断是不是合理扩展，
                          可疑的（尤其涉及权限、数据可见性）报出来
   · 连不上需求文档时才退回"只盘页面"，并说明这批场景的完整性没有需求背书。

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

①-1-B 【做的过程中发现场景不对，当场改，别攒着】
   场景清单是动手前拍的，**动手时一定会发现它不准** —— 这是正常的，不是返工。
   发现下面这些，**直接改/直接补，不用等谁批准**（改完在回复里说一句改了什么）：

     · 这条其实是两个互不依赖的功能 → 拆成两条
     · 探索时发现一条没盘到的关键流程（尤其是状态切换回来、并发、越权）→ 补一条
     · 标题/预期和实测下来的真实流程对不上 → 用 tb_update_case 改（你写错了自己改）
     · 这条根本验不出承诺的东西（比如承诺"配置生效"却只断了保存成功）→ 重写它

   **别做的**：为了让清单"跟一开始报的一致"而硬把发现塞进原来的框里，
   或者攒着等下一轮再说 —— 场景清单是工具不是承诺，它该跟着你的认知走。
   只有"要删掉已有用例"和"改动别人的用例"才需要先说一声。

①-2 【标题怎么写】一眼要能看出在测什么：**对象 + 做了什么 + 预期结果**。
   好：「API 类型服务发布后可被调用」「服务下线后调用返回 403」
   坏：「测试服务管理」「创建服务」「异常场景」「参数校验」
   —— 标题是列表页唯一露出来的东西。写得笼统，以后所有人都得点进详情才知道
   你在测什么，几百条之后没人受得了。

② 接口场景**只有一种**：你亲手活体验证过、绑在某条用例上的多步 E2E 链。
   回推走 tb_sync_orchestrated_scenario（登录→造→断言→清理），
   用 source_case_id 绑定某功能用例、**共享该用例的场景变量**。

   （2026-08-15 之前还有第二种「单接口·凭文档 AI 造」，连同「接口测试」页面
     一起下线了 —— 那种不绑用例，而场景变量只能挂在用例上，所以它结构上就跑不了。
     生成归你，平台只做呈现和回推通道。库里可能还留着几条那个年代的无主场景，
     **梳理场景、判重复时不要把它们算进来**，见下面 tb_list_api_tests 的 standalone 组。）

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
   · 平台**不再提供**"凭文档造"那条路（原 tb_generate_api_test 已下线）。连不上环境就
     先把环境弄通，或者只回推步骤用例（target_level=spec）把接口那一维明明白白欠着 ——
     欠着是事实，编一条跑不了的场景是假象。
   · 判断依据是"能不能连上"，不是"手上有没有文档"。有文档但环境也能连 → 仍然要活体验证。

⑥ 【一个用例 = 一条接口场景】tb_sync_orchestrated_scenario 按 source_case_id 幂等：
   同一条用例重推**永远覆盖那一条**（步骤整体替换、code 不变、标题以最新一次为准），
   不会新增。所以补完步骤尽管重推，标题也可以改。
   反过来说：**不要试图给同一条用例推多条场景**——用例详情里只呈现一条、只有一套编辑器，
   多推只会互相覆盖。一条用例要覆盖多个流程时，应该拆成多条用例。
   返回值里的 replacedExisting 告诉你这次是覆盖还是新建。

⑦ 【新增用例之前先报清单，等用户确认】

   **判据是「这个动作回滚贵不贵」，不是「是不是写库」**（2026-08-24 收窄）：

     要报清单等确认 —— 新建用例、删用例、改动**别人**的用例、
       往接口库里加节点（tb_create_case / tb_create_api_node / 删改类动作）
     不用报，直接做 —— 补一个还欠着的维度（回推脚本/接口场景/场景变量）、
       改**你自己**刚写错的字、改和实测不符的步骤（tb_update_case）。
       做完在回复里说一句改了什么就行。

   为什么收窄：原来写的是"调用任何写库工具之前都要报"，而 tb_update_case 的说明里
   写着「**你写错了自己改，别喊人**」—— 两处打架，照哪条都不对。更实际的后果是
   补一维、改个错字也要走一遍确认，人一天被问十几次，几次之后就闭眼说"都对"，
   **这道确认就废了**。确认要留给真正贵的那几个动作。

   报清单时一条一行，四列：

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

生成测试用例的流程就是上面 ①～⑧，这里只补三件上面没说的：

【真实文案从哪来】步骤里的按钮名、字段标签、Toast、弹窗标题，必须是被测系统里
真实存在的那几个字。你是 Claude Code，仓库就在手边，动手前先把它们抠出来：
  grep -rnE "创建|新建|编辑|删除" src/pages/ src/components/ src/views/ -l
  读命中的组件：<Button>保存</Button> / <Form.Item label="服务名称"> /
  message.success('创建成功') / <Modal title="新建服务"> / router 里的 path
读不到前端代码（不在同一个仓库、手上只有接口文档），**不是**"照接口定义把页面
推出来、步骤里标个待确认"的理由 —— 那种步骤没人跑得下去，标了待确认也没人回来
确认，它只会以"看着像写好了"的样子躺在库里。按 ⑤ 办：能连上环境就在页面上点一遍
把文案抄回来；连不上就说出来，只回推能确定的那部分（target_level=spec），
把欠的那一维明明白白欠着。

【UI 自动化脚本】平台不生成脚本。你在自己机器上用 Playwright 写、本地真跑通：
先 tb_get_sync_spec(kind='ui_script') 对齐写法（变量必须走 os.getenv 顶格声明，
平台执行时把所选环境的真值替换进默认值）→ tb_sync_ui_script 回推（入库前硬拦
写死的地址和凭据）→ tb_run_ui_script(case_id, env_id) 让**平台**在标准环境上
再跑一遍。你本机跑通不算数 —— 本机有 dev server、有残留数据、有 cookie。

【质量规范不用背，入库时会逐条告诉你】module 为空 / preconditions 太短 /
步骤写成接口调用风格 / 预期里有模糊词 / 同模块标题重复 —— tb_create_case
入库时点出哪一条哪个字不对，比在这里背规范准。seq 不用填，平台按顺序补；
case_type 默认就是 e2e。
机器判不了、只能你自己守的是这四条：
  · **一条用例只验一个测试点**
  · **预期必须是用户在界面上看得见的东西**（不是"库里有了"）
  · **多角色的步骤加 [管理员] / [租户] 标记**
  · **P0 占比压在 15% 以内** —— 这条现在真的没人拦你（原来的批量闸门
    随平台侧生成一起停了），全靠你自己分级。什么都 P0 等于没分级。

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
    description="列出分支下的用例（手工步骤那一层）。找已有用例、确认编号、看某模块测了哪些时用。**断点续跑靠它**：传 pending_only=true 只返回还欠着的那些 —— target_level 说这条要做到哪一步（spec 只要步骤 / spec_api 步骤+接口 / full 三件套），三个维度状态说已经做到哪一步，差集就是待办；返回里的 owes 直接列出还欠哪几维。中断之后重跑不用从头来，也不会把做完的又捡回来重做。参数: branch_id(分支UUID), page, page_size, keyword, folder_id, module(按模块名，省得先查folder_id), priority(P0/P1/P2/P3), case_type(e2e=场景 / api=单接口), target_level(spec/spec_api/full), **lifecycle_status**(draft/done/deprecated —— 不传时**自动排除已废弃的**；要找回废弃的用例就显式传 deprecated，否则撤销都撤不了), ui_status/api_status/manual_status(draft草稿/debugging调试中/completed完成), pending_only(默认false)，**bug_state**('blocked'=关联的 bug 还没验回来，跟 git 上已关闭 issue 取交集就是该回来调的那批，批量回归也会跳过它们；'fixed'=抓到过 bug 且已验回来的痕迹清单；'none'=从没关联过)",
)

_register(
    test_cases.get_case,
    name="tb_get_case",
    description="读一条用例的全部内容：手工步骤、前置条件、预期结果、模块归属。改它或给它挂接口场景之前先读一遍。参数: case_id(用例UUID)",
)

_register(
    duty.next_duty,
    name="tb_next_duty",
    description="【每轮上来先问这个】这一轮该干什么：一次给**七个**队列 —— ①待归因（红了还没分析的失败，带现象/红了几次/第几次复发）②待复跑（已确认修好的，跑绿了跟进单自动关）③**待处理接口变动**（版本升级分支对账命中的，一条条改预期——**按新版本的需求写，不是打开新版本跑一遍照着抄**，那是把实现抄了一遍、把新引入的 bug 固化成预期）④**待补用例**（新版本的新端点，谁都没覆盖它，不补就零覆盖且**永远不会报错**）⑤待补场景（审核反复提到的模块级缺口，被提到越多越该补）⑥待自证（回推四问没答的）⑦**等人拍板的废弃**（探不出来落到人手里的，别绕过它自己废）。每条都带「下一步该调哪个工具」，按堵得最死的排。**别自己关跟进单** —— 跑绿平台自动关；要强行放过得人工关闭并写原因。参数: branch_id(分支UUID), limit(每队列条数，默认10)",
)

_register(
    review.review_case,
    name="tb_review_case",
    description="【回推完自己先过一遍，别等人】按六维评审一条用例并落库审核结论：场景合理性 / 验证点到位 / 接口必要性 / UI 脚本正确性 / **本条覆盖完整性** / 可执行与纪律（不适用的维度自动摊掉权重）。⚠「本条覆盖完整性」只判**这一条自己承诺的东西验全了没有**（标题和预期里说到的每件事，断言里是不是都验了）——模块还缺哪些场景走 coverageGaps，是情报，**不扣这一条的分**，别为了提分去补邻居用例。**判定不由 AI 说，也不看分数**：有 blocker 一律不过、**两处以上 major 不过**、没真跑成功 → 第三种结论 `inconclusive`（无法审核：既不算通过也不算打回，环境弄好再审）。**分数只做体检和排序，不参与判定**（六维加权是模型给的，同一条两次拿到 86 和 78 是常事，拿抖动的数当闸门没法照着改）。blocker = 放进回归就是假绿或根本跑不了（断言恒真、只断控制面状态就当生效、预期照着实现抄、UI 脚本必挂）。返回 mustFix 逐条指到步骤名/断言/脚本位置。run_first=true 会先真跑一遍再评（UI 优先，debug 模式不进通过率）——断言咬不咬得住静态看不出来；**没带 run_first 就是静态审，结论强度差一个量级**（实测同一条：静态 84 分通过、真跑 56 分打回），reviewMode 会告诉你这次是哪种。另有两条岔路，返回形状不同：这条正申请废弃时它改审「**该不该废**」；版本升级里没被对账清单命中、内容与上一版逐字一致、上一版已审通过的「照抄堆」直接四条件自动过审（decidedBy=system），都不走六维。⚠ 这是一次不间断的同步调用，run_first=true 时可能跑到分钟级，中途没有心跳——如果这次调用**超时/无响应就中止了，不代表没跑完**：评审是跑完就落库，**别当场重新审一遍**，调 tb_review_check 看是还在跑还是已经出结论了。真的在跑的话这个工具也会挡下来（返回 status=in_progress，不会重复触发一轮真跑）。参数: case_id(用例UUID), run_first(可选), env_id(试跑用的环境UUID)",
)

_register(
    review.review_status,
    name="tb_review_check",
    description="【tb_review_case 超时了先调这个，别重审】只读地查一条用例的审核状态，**不触发评审、不占环境、不跑任何东西**，随时可以调。三种回答：①status=in_progress —— 有一次审核正在跑（含别人在页面上发起的模块批量），带已经跑了多久，等一会再来查，这期间调 tb_review_case 只会被挡回来；②status=reviewed —— 已经有结论，返回跟 tb_review_case 同一形状的摘要（verdict/mustFix/niceToFix/summary/runAttribution），另带 stale=true 表示这条结论出具之后场景或 UI 脚本又被改过、结论可能对不上现在的内容；③status=not_reviewed —— 从没审过，去调 tb_review_case。参数: case_id(用例UUID)",
)

_register(
    review.review_batch,
    name="tb_review_batch",
    description="【一批一起送审·别自己 for 循环】把一批用例送进平台的**审核队列**。⚠ 推一批时**不要自己循环调 tb_review_case** —— 那个是直调、一条也不排队，你并发送 20 条就是 20 次真跑同时打一个环境，而这条队列要防的两件事一件都吃不到：①**同环境串行**（两条脚本共用租户/账号，A 跑到一半 B 把 A 要用的数据删了 → A 莫名报错 → 审核判 A「脚本有问题」，**这是假打回**）②**熔断**（环境一挂，连续 3 条环境类失败就暂停整批；逐条调的话 20 条全标「无法审核」，看着像用例集体坏了）。**这批一定是真跑**，所以没有 run_first 参数。范围三选一：case_ids(逗号分隔，1 条=单条 / 多条=抽审) / module(模块名，连子模块一起，=模块全量) / 都不传=整个分支；scope='incremental' 只审没审过的和被打回的（**无法审核的也算没审过**）。单批上限 30 条，超了只排前 30。**人工发起的排在你前面**，入队后用 tb_review_batch_status 轮询，别重复入队。同一条已在这个环境的活跃批次里排着 → 自动合并不跑两遍。参数: branch_id(分支UUID), case_ids(可选), module(可选), env_id(可选，不传挑有 BASE_URL 的默认环境), scope(all/incremental，默认all), with_checkup(模块级审核时顺带做模块体检，默认true)",
)

_register(
    review.review_batch_status,
    name="tb_review_batch_status",
    description="【看审核批次跑到哪了】轮询一个批次的进度：每条的结论(approved/rejected/inconclusive)、当前在审哪条、通过/打回/无法审核各几条。**判完没完看 finished 字段**，别拿 done==total 猜（total 为 0 或中途熔断时那个猜法不成立）。**status=paused 就是熔断了** —— 连续 3 条环境类失败，那不是用例的问题，去把环境弄好再在页面上续跑，接着刷只会继续红。参数: batch_id(批次UUID，tb_review_batch 返回的)",
)

_register(
    review.module_checkup,
    name="tb_module_checkup",
    description="【写完一批自己问一句，别等人催】这个模块**还缺什么**：回 commonIssues（这个模块的用例反复犯的同一个错，改一处能修一片，纯汇总不问模型）+ coverageGaps（该测没测的场景，模型看内容）+ coverageSkew（P0 占比、创建/查询/修改/删除/异常/权限六类各几条、缺哪一类，代码数个数 —— 模型读 60 条标题时不会去算「18/22 都是创建类」，而这个比例往往比任何一条缺口都刺眼）。**observed_actions 值得多花一步去凑** —— 把你在页面上探到的可操作项（按钮/菜单项/状态流转）传进来，缺口就是拿它跟现有用例对账出来的：「页面上有这个操作、用例里一条都没覆盖」是最硬的缺口；不传就只能凭标题猜，出来的东西会泛。**缺口是建议清单不是门禁**，不参与任何一条用例过不过。不占队列、不用环境、不碰被测系统，随时可以问。参数: branch_id(分支UUID), module(模块名) 或 folder_id(模块UUID) 给一个, observed_actions(可选，页面上探到的可操作项列表)",
)

_register(
    deliverable.check_branch,
    name="tb_check_branch",
    description="【验收·一次看完整个分支】做完一批之后跑这个，别逐条查。回 summary（可交付/有阻塞/有脆弱点/待人审 各几条）+ 每条一行：卡在哪(firstBlocker)、有几处脆弱点(riskKinds)、审核标签。**阻塞和脆弱点是分开的**：「有一步真挂了」和「跑绿了但异步断言抢跑」在 owes 里长得一样，要做的事完全不同（一个改断言、一个加 retry_timeout_ms）。参数: branch_id(分支UUID), module(可选，按模块名筛)",
)

_register(
    deliverable.check_deliverable,
    name="tb_check_deliverable",
    description="【交付门禁·做完自己先跑】这条用例现在**能不能交付**，只读不改任何状态。回三类结论：blockers=交不了（有一条就是不可交付：欠维度/一步没跑过/有步骤挂着/断言把布尔写成字符串这种必然假红）、risks=交得了但脆（典型是异步断言没开 retry_timeout_ms —— 跑绿了也是侥幸跑赢时间窗，换台机器就红；还有只跑了勾选的一部分、流量被截断）、notes=要你自己判断的（疑似越界的测试点、只用 body_contains 兑付「应产生/应记入」这类承诺、请求体里的驼峰键）。**别再自己宣布「这条可以交付了」** —— 跑这个，把它的结论贴出来。参数: case_id(用例UUID)",
)

_section("版本升级·分支对账")

_register(
    branch_diff.list_branch_endpoints,
    name="tb_list_branch_endpoints",
    description="【版本升级·对账第一步】这个分支的用例依赖了哪些端点、哪些字段 —— 反查的**平台那一半**（另一半「新版本改了什么」在你本机 git 里，两半求交集才是影响清单，所以平台单独产不出清单）。回每个端点的归一化路径模板 + 用它的用例编号/场景/步骤名/期望状态码/断言字段路径。⚠ **必读返回里的「覆盖不到的」**：手工步骤和 UI 脚本里没有结构化 method/url，这套反查探不到它们 —— 纯 UI 改版在这份表上一个字都不会变，而「没命中」下一步会被当成「可以照抄」。参数: branch_id(分支UUID)",
)

_register(
    branch_diff.apply_endpoint_diff,
    name="tb_apply_endpoint_diff",
    description="【版本升级·对账第二步】把新版本的变更报上来求交集落清单，**一个用例都不改**。分堆：命中→要改（removed→该废候选）、未命中→照抄、kind=added→待补用例。changes 每条 {url, method, kind, detail}；kind: removed(端点没了) / field_changed(字段变了，detail必填) / new_state(新增状态值，detail必填) / renamed(改名挪位置 → **要改不是要废**) / added(新端点 → 待补用例，**不报就零覆盖且永远不报错**)。url 用路由声明的写法，平台归一化后匹配（剥 host/query、id 段和变量压成通配、容忍部署前缀差异），**故意偏向多命中** —— 多命中只多审一次，漏命中是假绿。可多次调补交：命中累积、重复不重落，**新命中的会撤回已自动过审的用例**。命中的用例预期落款自动打回「待重新确认」。参数: branch_id, changes(数组), from_ref(旧版本号), to_ref(新版本号)",
)

_register(
    branch_diff.request_deprecate,
    name="tb_request_deprecate",
    description="提请废弃一条用例（新版本上这个场景不存在了）。**平台硬校验证据，交不齐不受理** —— 假废弃比假绿更毒：误废一条，那块功能就再没人测了而且**永远不报错**（假绿至少还在回归池里刷红）。证据要正反两面：正面 apiProbe=[{url,method,status}] 打老端点拿 404/410，或 uiProbe=[{page,找了什么,结论,截图}]；反面 searchedElsewhere=[...] 排除改名/挪菜单/拆页面（这三种在 UI 上都长得像「没了」）。提请只挂「待废审」，**用例状态一个字不动**，要等批准才落 deprecated。批准走 tb_review_case（有待决请求时它改审「该不该废」，平台自己复核接口那半边）或人在页面上一条条确认；探不出来一律落人。参数: case_id, reason(一句话), evidence(证据对象)",
)

_section("用例·手工步骤")

_register(
    test_cases.create_case,
    name="tb_create_case",
    description="回写一条**活体验证过**的步骤用例。凭文档想象的用例跑不通也没人认。"
                "**标题写成「对象+动作-预期」两段**（短横、不留空格），前段 20 字内一眼看完（列表上只露标题）；"
                "**每个步骤名带角色前缀**：前置:/操作:/验证:/清理: —— 写了前缀平台就不用猜你的意图，判得准也不误报。"
                "case_type 看**测试对象**：api=某一个接口的参数/权限；e2e=场景（某功能是否按需实现）。"
                "**跟做不做 UI 无关**（做几维看 target_level: spec/spec_api/full），跟步骤多少也无关；造数用了几个接口不影响判断。"
                "门禁：模糊词硬拒、同模块同名硬拒、步骤粒度自动拆。"
                "参数: branch_id, title, module, case_type, priority(P0-P3), preconditions, "
                "steps([{seq,action,expected}]), expected_result, submodule, target_level, target_level_reason, "
                "expected_confirmed_by/expected_confirmed_note(跟用户确认过「这条要验什么」就带上，平台只记录不拦)"
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
    description="建/改一条 LLM Mock 路由，决定「上游怎么答」——status_code(429/500 测重试降级熔断)、delay_ms(测超时)、finish_reason(stop/length/content_filter 测透传)、prompt_tokens/completion_tokens(**测网关的计费/配额统计算得对不对**)、model(测模型映射)。按 path 幂等。**path 必须带你自己的前缀**（如 /mock/TC-XXX-00001/v1/chat/completions）：直接占用 /v1/chat/completions 会被拒，那是所有用例共用的，你配成 429 别人就跟着挂、还偶发。**smart=true 开智能应答**：行为改由请求正文里的指令决定（SAY:原样回显 / MODE:HIT 输出含 VIOLATION / MODE:PII 输出侧敏感信息(请求里没有，验护栏查的是输出) / MODE:EMPTY 零内容流 / MODE:FILTER 空回复+content_filter / MODE:DEFY 无视 stream:false 硬返流 / MODE:SLOW 每片 250ms / MODE:LOOP 先 tool_calls 再终局)，一条路由演完所有场景，上面那些静态参数不生效；smart_role='checker'(或路径带 /checker) 演网关护栏调的那个检查模型，它把「本次待检正文多长、开头是什么」回显出来——网关喂给护栏什么，这是唯一观测点。参数: name, path, status_code(默认200), delay_ms, response_body, finish_reason, prompt_tokens, completion_tokens, model, smart(默认false), smart_role(auto/upstream/checker)",
)

_register(
    mocks.llm_mock_requests,
    name="tb_llm_mock_requests",
    description="**网关到底往上游发了什么** —— 断言用的。鉴权头有没有正确注入、模型名有没有按映射改写、参数有没有被篡改，这些在网关下游根本看不见（客户端只能看到最终响应），这是唯一的观测点。智能应答路由还返回 smartMeta：mode(命中哪条指令)、**stream(网关实际发出的值，流式降级有没有真发生只能看它)**、hasStreamOptions、loopStage，checker 角色再带 checkedLen/envelopeLen/verdict(护栏到底拿到了多长的正文)。断言前先 tb_llm_mock_reset 清一次。参数: path(可选), limit(默认20)",
)

_register(
    mocks.llm_mock_reset,
    name="tb_llm_mock_reset",
    description="清掉上游请求记录。**断言「上游收到几次」之前必须先清** —— 不清的话上一轮的记录还在，「只应收到 1 次」会假过，而假过比假红更难发现。参数: path(可选，不传清全部)",
)

_register(
    mocks.proxy_capture,
    name="tb_proxy_capture",
    description="代理观测抓到的真实请求 —— 写接口场景的素材来源。活体验证最费劲的一步是「这个页面动作到底发了哪些请求、body 长什么样」，自己开 devtools 抄又慢又容易抄错，而平台的代理已经记下来了。**前端跑 Vite 时先滤一遍**：抓到的绝大多数是 .jsx?t= 热更新，实测 156 条里只有 9 条是 /api/，不滤就被噪声占满。参数: limit(默认50，上限200), url_contains(在全量记录上按 URL 子串筛，如 '/api/'), method(如 'POST')",
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
    description="改一条已有用例。只传要改的字段。**你写错了自己改，别喊人**（标题打错字、步骤和实测不符都用这个）。"
                "过和建用例同一套门禁，同名检查排除自己。"
                "**改不了状态**：ui/api/manual_status 一概不收 —— 状态由执行事实或人推进；要说「这条能跑了」就去跑一遍。"
                "改步骤或预期会清掉「预期已确认」；只是措辞润色传 reconfirm=true（沿用原落款、只重盖时间）。"
                "参数: case_id, title, priority, preconditions, steps([{seq,action,expected}]), expected_result, "
                "target_level(spec/spec_api/full), target_level_reason, expected_confirmed_by, expected_confirmed_note, reconfirm, "
                "**blocked_external**(卡在外部条件就写一句等什么；不是状态、不免检阻塞，只为分清「没人写」和「写不了」；条件到位传空串), "
                "**bug_refs**([{ref,url,status:open|fixed,note}]，整份覆盖)：跑出来红但原因不在用例＝产品 bug 就关联上。"
                "open=还没验回来（批量回归跳过它、不计通过率）；fixed=**你回来调通了**才标。"
                "**关联是永久痕迹，标完 fixed 别清** —— 清了就看不出这条曾抓到过 bug；[] 只用于关联错了。"
                "待办来源：tb_list_cases(bug_state='blocked') 跟 git 上已关闭 issue 取交集。, "
                "**tags**(自由分拣词，≤20 个/每个 ≤32 字；不表达状态和审核结论), "
                "**module/submodule**(放错目录自己搬，目录不存在自动建；用例编号不跟着变)"
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
    description="列出**本项目**的测试环境（id + 名称）。环境是项目级的，一个项目的环境在别的项目里看不到、也用不了。拿到 env_id 再去 tb_get_merged_variables 看它有哪些变量可引用。参数: project_id(项目UUID)",
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
    description="【失败归因】把你对某次失败的判断写回平台。先 tb_get_ui_script_result 拿证据包和 run_id，"
                "**看完截图和流量、必要时活体复现一遍、结合代码看过**再来。"
                "**不是所有归因都要等人**，按证据齐不齐分流："
                "①脚本自己错(test_defect)/用例过期(case_expired)/环境(env_issue)/数据(data_issue)/不稳定(flaky) "
                "→ **你自己改**，不用等人；天然闸门是「改完必须复跑跑绿，跟进单才会关」。"
                "②产品缺陷(product_defect) → 要 evidence 里三样齐全才放行："
                "liveVerified(活体怎么复现的)、codeRefs(文件:行 或需求出处)、issue(按 skill 规范提的单号/URL)。"
                "缺一样自动落回「等人确认」并告诉你缺什么。放行之后这条回归不再刷红，"
                "但**交付门禁照旧算「卡在产品缺陷」——不是通过**，所以甩锅没有收益。"
                "③需求问题(requirement_unclear)/拿不准(unknown) → 只有人能定，直接进待确认。"
                "参数: run_id, cause(见上), confidence(high/medium/low；低置信只能配 unknown), "
                "reasoning(判断依据), evidence(对象：liveVerified/codeRefs/issue 等), "
                "proposed_fix_target(script/product/data/case/env/none)"
)

_register(
    analysis.list_pending_confirm,
    name="tb_list_pending_confirm",
    description="【失败归因】列出**真正在等人**的归因 —— 你交上去、还得人拍板才能往下走的那些。默认**不含自证放行的**（此前列的是「所有还没确认的」，CC 明明被告知「自己改不用等」的那些也混在里面，人扫两眼发现没一条要自己做，之后就不看了）。默认留两种：`needs_human`（需求没写清/拿不准/产品缺陷自证缺样，只有人能定）和 `self_serve_sampled`（自证的抽检样本，每 10 条抽 1，**你照旧自己改别等**，人另外复核一次用来校准归因准不准）。要看全部传 include_self_serve=true。参数: project_id(可选), limit(默认20), include_self_serve(默认false)",
)

_section("执行报告")

_register(
    plans.list_plans,
    name="tb_list_plans",
    description="【执行报告】列出项目下的测试计划，拿 planId。拿到 planId 就能调 tb_get_report_summary / tb_get_failed_scenarios（只有 reportId 也行，它们会自己反查计划）。返回含用例数、最近一次 reportId。参数: project_id(项目UUID), status(可选: draft/executing/completed), limit(默认20)",
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
    description="一次执行的总览：通过 / 失败 / 跳过 / 通过率，以及按模块的分布。参数: plan_id 和 report_id **给一个就行**（只有 tb_run_plan 返回的 reportId 也能查，会自己反查计划）",
)

_register(
    test_reports.get_failed_scenarios,
    name="tb_get_failed_scenarios",
    description="【执行报告】拿这次报告里所有失败的用例，**每条带 runId** —— 用它调 tb_get_ui_script_result 看证据包（截图路径 / 流量 / 平台的现象初判），判完再调 tb_submit_analysis 回填归因。参数: plan_id 和 report_id **给一个就行**（不传 report_id 取该计划最近一次）",
)


# ── 接口测试工具 ──────────────────────────────────

_section("接口场景·可执行")

# tb_generate_api_test（凭接口文档让平台 AI 造单接口场景）2026-08-15 随
# 「接口测试」模块一起摘掉。它跟平台的定位反着来 —— 生成归外部 Claude Code，
# 平台只做呈现和回推通道。而且它的产物结构上就跑不了：场景变量只能挂在用例上
# （scenario_variables.case_id NOT NULL），不绑用例就拿不到凭据。

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
    api_tests.check_env_hygiene,
    name="tb_check_env_hygiene",
    description="【查测试残留】被测环境里有没有本项目跑出来的孤儿数据。两类：①这条链造了东西却**没有清理步骤** —— 每跑一次留一份，堆多了会让 data[0]、满页分页那类断言时红时绿（看着像被测系统的问题，其实是自己攒的垃圾）②最后一次运行没跑到清理步骤，那次造的 id 已从创建步骤的响应里抽出来，删它的请求就是那条清理步骤本身。⚠ 只看接口场景、只看得见**最后一次运行**：更早的残留、UI 脚本造的、手工造的平台都没记录，**报 0 条不等于环境干净**。参数: project_id(项目UUID), branch_id(可选，只看某分支)",
)

_register(
    api_tests.check_assertion_bite,
    name="tb_check_assertion_bite",
    description="【验断言到底有没有用】把**改状态的那个动作步**跳掉跑一遍：后面的断言该红就是有效，照样绿就是恒真（动作前后都成立，动作坏了也不会红）。绿的用例≠有效的用例——方向写反的断言也是绿的，断言条数和强度指纹都判不了这件事，只有「删掉原因、看结果是否消失」能判。跳的必须是动作步（审批通过/禁用服务/驳回/删除），别跳产出 id 的创建步（后面全卡在变量未解析，什么都证明不了）。只读：不写步骤状态、不建报告、不动用例维度；但**请求是真发的**（没被跳掉的步骤照跑）。⚠ 跳的就是清理步时，那一趟造的数据不会被删 —— 残留归你自己收（tb_check_env_hygiene 看不见它，变异运行不留痕）。参数: case_id(用例UUID), skip_steps(要跳掉的步骤名，逗号分隔，必须和场景里的名字完全一致), env_id(强烈建议，不传没有 BASE_URL/账号链子跑不起来)",
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
    ui_scripts.render_ui_script,
    name="tb_render_ui_script",
    description="【本地要跑就用它】把用例的 UI 脚本渲染成**一个能直接 pytest 跑的文件**：库里存的是带占位的原文（文案 ${键|中文}、取值 os.getenv），平台执行时才补齐，本地拿原文跑不通。这里一次烧进三样：①文案占位→当前语种那句话 ②os.getenv 默认值→该环境真值 ③被测系统自己的语种开关（在同一个文件里加 context fixture 种 localStorage——少这条最坑：脚本渲染成英文了、系统还说中文，必红）。凭据默认不烧（同族工具对凭证一律脱敏，不开后门），返回里给 exportEnv 让你 export 那两三个；要完全自包含就传 include_credentials=true（凭据会出现在返回内容里，仅本机用）。textUnresolved 是没换掉的键——先登记词条或补 |中文。参数: case_id(用例UUID), lang(zh|en，默认 zh), env_id(强烈建议), include_credentials(默认 false)",
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
    description="【失败证据包】拿这条用例最近一次执行的**完整证据**，用来判断为什么挂：截图（返回**文件路径**，和平台同机，直接 Read 打开看图）、网络流量摘要（按状态码分桶 + 展开非 2xx 和写操作那几条，其余页面自身的 GET 已折叠）、stdout 尾部、run_id，以及平台按确定性规则给的**现象**初判 failure_phenomenon（timeout / element_not_found / assertion_mismatch / http_5xx / script_error / dependency_unresolved / unknown）。**UI 和接口场景都收**（返回里的 script_type 说明是哪一维）——接口执行没有截图和浏览器流量，但 error_summary / stdout 轨迹 / 现象都在，够写 evidence。⚠ **要归因就把 run_id 传上**（tb_get_failed_scenarios 给的那个）：不传只取「最近一次」，而这条用例可能已经被复跑过 —— 活体撞到过 TC-DYGL-00013 六次接口执行、最近一次是 passed，于是证据包里什么指针都写不出来，而 tb_submit_analysis 又拒收 passed 的执行。**报告指着失败那次、这里给最新那次**，不传 run_id 就会踩这个错位。⚠ 现象不是归因 —— 平台判「是什么」，「为什么」由你判断。参数: case_id(用例UUID), run_id(可选但归因时强烈建议), script_type(可选 ui/api，不传取最近一次)",
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
    description="【回推第一步·先调】获取回推规范。**先看 kind=order 那一节**：动手顺序错了后面全歪 ——先在页面上走一遍 → tb_proxy_capture 取页面真发的请求 → **先写 UI 脚本** → 照真实流量写接口场景 → 最后判哪些断言必须留 UI。（上一轮就是反着做的：22 条接口场景全用 /subscriptions/provider，而页面真调的是 /provider-unified —— 端点存在、返回 200，用例一直绿，但验的是页面根本不用的接口。）还包括：命名规范（标题两段式、步骤前缀）、变量三层、断言形状、前置清理走 api fixture 别在页面上点、UI 脚本写不写的判据。参数: kind(order/naming/case/api_scenario/scenario_shape/ui_script/variables/timing/all，默认all)",
)

_register(
    sync.sync_orchestrated_scenario,
    name="tb_sync_orchestrated_scenario",
    description="【用例·编排的接口场景】把你**活体验证过**的多步接口链显式写回，绑定 source_case_id 并共享该用例场景变量。入库前硬拦截悬空 ${x}、软警告疑似写死和「和前面某步逐字相同的断言」（同一请求上动作前后断同一件事＝没验动作）。参数: project_id, branch_id, title, steps([{name,method,url,headers,body,assertions:[{type,operator,expected/value,field}],variables_extract:{name:jsonpath},group_name,enabled,**retry_timeout_ms**,retry_interval_ms,wait_ms}])——异步下发导致的抢跑用 retry_timeout_ms（断言没过就整步重发直到过或超时），别再插假步骤占时间窗；详见 tb_get_sync_spec(kind='timing'), source_case_id(必填), folder_name(可选), priority(默认P1), description(可选), **mode**(replace=整条覆盖，默认；patch=按 step name 只改点名的那几步、其余原样保留——改几个断言不用重发全链。name 必须和现有步骤完全一致，找不到就拒绝；加步骤/改名用 replace)",
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
    description="【共享基础数据·登记怎么找到它】路线B专用：多条用例共用、反复重建代价大的底座(上游/负载、隔离上下文、长期存在的应用)。用法=先 tb_list_global_data 查有没有 → 没有就**你自己调接口造出来且不清理** → 再用本工具登记 exists_check。之后每次跑，平台在第一步之前自动探测并注入 ${资源名}，换环境也能找到对应资源。注意 match 要用 name/code 这类稳定标识，不能用 id(等于换个地方写死)。**create_def 别省**：探到「确实没有」（探测请求成功但没匹配上）时平台会照它自动补建，补了会在运行结论里明说；401/5xx/超时算「没查成」，一律不动（一次 token 过期就照着建会造出一堆重复底座）。没登记 create_def 就只能报「变量未解析」等你自己造。只属于本条用例的数据别用这个，那种该在场景开头自建、末尾清理。参数: project_id, name(引用名), exists_check(必填,{method,url,match,extract,**role**(可选,默认ADMIN,探测与补建用哪个角色的token——读得到不等于建得了，实测建上游要租户管理员能力)), create_def(可选,登记备查当初怎么造的), description, keep(默认true)",
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
    sync.upsert_i18n_terms,
    name="tb_upsert_i18n_terms",
    description="【登记国际化词典】脚本里要用 t() 的文案在这里登记（按 key upsert）。有语言中立键就用键（services.form.name）——多义词只能这么区分；只有中文就用中文当键。带 i18next 命名空间的键两种拼法互认（`ns:a.b` = `ns.a.b`），查词时同一条，登记一次就够。⚠ 用键**必须先登记**：t() 查不到会原样返回那串键，选择器拿它匹配必然红；中文当键则退回中文不会挂。没 en 译文的词条注入后在英文环境仍退回中文。参数: project_id, items([{key(必填), zh(中文当键时可省), en, module, category(button/placeholder/label/text), description}])",
)

_register(
    sync.list_global_data,
    name="tb_list_global_data",
    description="【回推前查】汇总项目级**可引用**全局数据（全局变量+各环境变量键+自动化共享资源，凭证脱敏），帮你判断哪些走 global_ref、哪些别写死。**传 probe=true + env_id 会在该环境上真探测一遍共享资源**，每条给出 state：exists=探到了(附 extract 抽出的 values) / missing=确实没有，照它的 createDef 你自己调接口造出来（造完不用改配置，existsCheck 下次自然探得到）/ unknown=平台没查成(401、5xx、超时)，**别动它**——一次 token 过期就照 createDef 补建，会在被测环境造出一堆重复底座且 keep=true 没人清理。**补建按入口分，别记成一句全局结论**：这个工具（以及页面、预检）**只探不建**，只告诉你缺了什么、当初怎么造的；而**接口场景/UI 脚本真正执行之前**，探到 missing 且登记过 createDef 的，平台会**照它补建再复探一次**（见 api_test_runner 的 _auto_create_resource，补了会在运行结论里明说）。所以这里报 missing 不等于跑的时候还缺。参数: project_id(项目UUID), env_id(可选，probe=true 时必填), probe(默认false)",
)


# ── Skill 共享 ───────────────────────────────────

_section("Skill 共享")

_register(
    skills.push_skill,
    name="tb_push_skill",
    description="【把本项目的 skill 推上平台】读你本地 .claude/skills/<name>/ 的内容，推到 Lumiere 存起来，默认 visibility=public 即其它项目可取用。存的是**客户端侧执行**的 skill（跑在开发者机器的 Claude Code 里，用 Bash/Edit/Playwright），平台只存取、永不当 prompt 执行 —— 跟内置 tb-* 不是一类。同名会覆盖，覆盖前自动留档可回滚。参数: project_id(项目UUID), content(SKILL.md全文,必填), name(可选,不传则取 frontmatter 里的 name), files(可选,附属文件{相对路径:文本内容},如 references/api.md), description(可选,不传取 frontmatter), visibility(public全平台可取/project仅本项目,默认public), overwrite(默认true)",
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
