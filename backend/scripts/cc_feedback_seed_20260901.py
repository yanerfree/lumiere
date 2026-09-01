"""2026-09-01 那份汇总的 31 条 —— 通道开通**之前**攒下的存量，一次性搬进来。

## 为什么单独放一个文件，而不是塞进 import 命令里

这是**数据**，不是逻辑。它有明确的保质期：搬完就再也不会有第二份
（以后 CC 直接走 `lum_report_feedback`）。把它和 CLI 混在一起，
下次有人改 CLI 时会顺手"维护"这份数据 —— 而它**不该被维护**，
它是一份历史快照，改动它等于篡改当时的现场。

## 正文为什么是摘要而不是原文照抄

原文 648 行，单条最长的（1.1）有 40 多行带三级小标题。全量搬进来会让这张表
变成"另一份没人读的文档"—— 而这条通道的全部意义就是让它比文档更容易被处理。
所以每条压到「现象 / 为什么要紧 / 建议」三段，**原文的定位信息（章节号）写在
evidence.refs 里** —— 要看全貌去读原文，这里只留够判断和分派的量。

判类的口径（和 `lum_report_feedback` 的描述一致）：
  · bug         —— 说了会做 A 实际做了 B（含静默失败、描述≠实现）
  · improvement —— 行为没错，但代价不合理 / 容易把人带错路
  · requirement —— 平台今天没有这个能力

第五章（做得好的地方）**不导入**：它没有可处理的东西，导进来只会稀释这张表。
3.6 同理（那是一处正面对照）。
"""
from __future__ import annotations

SOURCE = "import"
SOURCE_DOC = "~/ai-admin/Lumiere-平台反馈-汇总版-2026-09-01.md"
REPORTER = "外部 CC（2026-09-01 汇总文档）"

# (章节号, 标题, 类别, 工具/模块, 正文, expected, actual)
ITEMS: list[dict] = [
    # ── 一、功能缺陷 ──────────────────────────────────────────────
    dict(
        ref="1.1", category="bug", tool="接口场景执行器",
        title="执行器给未写 Authorization 的步骤自动注入有效凭据",
        body="""步骤没有显式写 Authorization 头时，执行器会替它注入一份有效凭据再发出去。

这一条同时踩坏三件事：
① 整类负例写不出来 —— 「无凭据访问应返回 401」是最基础的一类安全断言，
   在这里你以为在测无凭据，实际发出去的是带凭据的请求。
② 它会真的执行破坏性操作。实测「不带凭据删除服务，期望 401」返回 204，
   服务真的被删了 —— 一条本意是验证系统会拒绝的步骤，变成了一次成功的删除。
③ 失败形态精准伪装成产品缺陷。看到 204 的第一反应是「被测系统鉴权有洞」，
   差一点就去提 P0 了；反过来顺着平台结果把预期从 401 改成 204，
   就等于把一条鉴权断言洗成了恒真。

现在的绕法是显式传一个无效 token —— 但那测的是「坏 token」不是「无 token」，
很多实现里走的是不同分支。

建议（按优先级）：① 给步骤加显式的「不注入」开关（auth: none）；
② 或把注入规则倒过来，只在步骤显式声明时才注入；
③ 无论选哪条，**先把当前这个注入行为写进工具描述** —— 现在一个字都没提，
   而它改变了请求的实际内容。这是「描述≠实现」里后果最重的一处。""",
        expected="步骤里没写 Authorization，就该按没有凭据发出去（所见即所发）",
        actual="执行器替它注入了一份有效凭据；实测「无凭据删除服务期望 401」返回 204，资源真被删了",
    ),
    dict(
        ref="1.2", category="bug", tool="断言执行（type=status / operator=in）",
        title="in 算子在 type=status 上恒为 false",
        body="""{"type":"status","operator":"in","value":"200,204"}，接口实际返回 204，平台判 fail：
failedAssertions 里 expected 是字符串 "200,204"、actual 是数字 204（无引号）。
in 的实现大概率是把 expected 按逗号切成 ["200","204"] 后直接判 actual in [...]，
字符串元素 vs 数字永远不相等。status 的 actual 来自 HTTP 状态码天然是 int，
所以这不是偶发，是恒 false。

佐证：同一 type=status 上的 != 算子正确报红，且 actual 取到真值 418 而不是 null ——
说明 == / != 那条路径做了类型归一，只有 in 漏了。类型缝是局部的。

危害方向是「把人推向减少断言」：遇到这种红最省事的处置是改成 == 写死一个实测到的
状态码。但换一个确实允许多种返回的场景（删除不存在的资源 204 或 404 都合理），
被迫 == 只能二选一，另一半合法行为会被判红，再退一步就是干脆删掉断言。

一个恒为 false 的算子比一个不存在的算子更糟：不存在会立刻报错，作者知道要换写法；
恒 false 会给出一段看起来在认真比较的失败信息，让作者以为是自己的用例有问题。

建议：in 比较前把两边归一到同一类型；顺带检查 not_in;
最好在断言执行层统一归一一次，而不是每个算子各归各的 —— 现在这个缝就是各写各的产物。""",
        expected="status in 200,204 且实际返回 204 → 判 pass",
        actual="判 fail，failedAssertions 显示 expected='200,204'(str) vs actual=204(int)",
    ),
    dict(
        ref="1.3", category="bug", tool="lum_sync_orchestrated_scenario",
        title="type=jsonpath 断言静默入库，且该类断言全部判假",
        body="""断言 type 实际只支持 status / body_contains / body_field，field 路径不带 $. 前缀。
但写成 type:"jsonpath" + field:"$.data.id" 时：入库成功、无任何报错或警告；
执行时该类断言全部判假，actual 恒为 null —— 真值也判假。

实测一次跑出 7 条假红。因为 actual:null 看起来非常像「接口没返回这个字段」，
第一反应是去查被测系统的响应结构。

更糟的变体：把这类恒假断言配在一个期望它失败的负例上，就会得到假绿。

建议：入口对 assertions[].type 做白名单校验，不在名单内直接拒绝并列出合法值 ——
这是零成本的，平台完全知道自己支持哪三种。同理 field 里带 $. 前缀时应该报错或
自动剥离，而不是拿一个永远取不到的路径去查。
顺带：operator 也有同类问题 —— 按直觉写 equals，平台只认 ==，同样是静默的。""",
        expected="不支持的 assertion type 应在入库时被拒绝并列出合法值",
        actual="静默入库，执行时该类断言 actual 恒 null、全部判假，一次产生 7 条假红",
    ),
    dict(
        ref="1.4", category="bug", tool="lum_sync_orchestrated_scenario",
        title="步骤名超 varchar(200) 无前置校验，抛原始 SQL 并回显全部绑定参数",
        body="""任一步的 name 超过 200 字符时整次 sync 事务性失败，返回给调用方的不是业务错误，
而是 SQLAlchemy 的原始异常 —— 带着完整 INSERT 语句和这一批所有步骤的绑定参数。

三个问题叠在一起：
① 没有前置校验。上限是 api_test_steps.name 的列定义，平台完全知道它是多少，
   却让请求走到数据库才失败，而且失败是整批的 —— 42 步里 1 步超限，
   另外 41 步一起回滚，调用方要重新组装整份 payload 再发一次。
② 错误信息泄露内部结构。回显的 SQL 里有完整表名列名和全部绑定参数。
   我这条的参数里没有明文凭据，只是因为我遵守了「口令走 ${...} 变量引用」那条规范 ——
   平台自己没有任何机制保证这一点。换一个把 token 写进 header 字面量的调用方，
   这条异常就会把它原样吐回来，并且大概率被粘进聊天记录、issue、日志。
③ 上限没写在任何工具描述里，而它很容易撞到：评审规则一直在要求「步骤名里写明设计
   意图和理由」，照做就会把步骤名写长。**一条规则在推你往上写，另一条在 200 字符处
   静默截断你 —— 两条规则没对过账。** 本轮最长的步骤名 196 字符，是贴着上限过的。

建议：① 入口做长度校验，返回业务错误并指名是哪一步、超了多少；
② 无论是否修上面那条，都要把数据库异常包一层再返回 —— 回显绑定参数是通用的凭据
   泄露面，不限于这一个工具；③ 把 200 这个数字写进工具描述。""",
        expected="步骤名超限应在入口返回业务错误，指名哪一步、超了多少",
        actual="走到数据库才失败，抛 SQLAlchemy 原始异常并回显完整 INSERT 与全部绑定参数；整批 42 步一起回滚",
    ),
    dict(
        ref="1.5", category="bug", tool="lum_upsert_scenario_variables",
        title="kind=global_ref 场景变量解析成空串，且不报「变量未解析」",
        body="""建一个 kind=global_ref 的变量指向共享资源，在步骤里引用它 —— 解析结果是空串，
且平台不报「变量未解析」，请求照常发出。被测系统收到一个空 UUID，
回 422 invalid UUID length: 0。

难查在于：错误落在被测系统那一侧，长得完全像一个业务参数错误，
花了不少时间在被测系统里找「谁把 id 传空了」。而且同名变量会伪装成能用 ——
如果恰好有一个同名的其它 kind 的变量存在，读起来一切正常。

绕法：不用 global_ref，直接在步骤里写共享资源的原名（如 ${echoUpstreamId}）。

建议：① 解析不到时按平台自己已有的「变量未解析」路径报错并中止该步，
而不是替换成空串继续发 —— 平台已经有这个错误态了，这里没走到；
② 若 global_ref 当前就是不支持在步骤里引用，则在落库时就拒绝该 kind。""",
        expected="global_ref 解析不到时按「变量未解析」报错并中止该步",
        actual="解析成空串、不报错、请求照发；被测系统回 422 invalid UUID length: 0",
    ),
    dict(
        ref="1.6", category="bug", tool="lum_update_case",
        title="changed 回执会漏报，而它是唯一的写入回执",
        body="""实测四类字段：preconditions 和 bug_refs 进 changed 且落库；
blocked_external 和 expected_confirmed_by/_note 不进 changed（changed: []）但**确实落库了**，
只有 lum_check_deliverable 的 notes 能看出来。

后果分两层，第二层才要命：
第一层是「不知道写没写进去」。changed:[] 的字面意思是「什么都没改」，描述里没有任何
地方说它会漏报，使用者的自然反应是重发一遍 —— 对一个整份覆盖语义的接口，
盲目重发不是没有代价的。
第二层是它会诱导出错误的结论。我曾白纸黑字写下「判定为未落库」，理由是
「changed 和 lum_list_cases 两侧都看不到」—— 推理过程没毛病，错在这两个入口
恰好都是漏报它的入口。这条错误结论在我的记录里躺了一整轮。
**一个会漏报的回执，会让认真核对的人比不核对的人错得更笃定。**

已复现 3 次。

建议：① changed 补全所有实际落库的字段（根治）；② 若某些字段有意不进 changed，
在返回里单独回显其当前值，或在描述里明说；③ 返回体已经回显了 preconditions/steps/
expectedResult/bugRefs 一大片（单次上万字），偏偏不回显这两个刚写进去的 ——
回显得越多，缺的那两个越像「没写成功」。""",
        expected="changed 列出本次实际落库的全部字段",
        actual="blocked_external / expected_confirmed_by / _note 落库了但 changed 为 []（已复现 3 次）",
    ),
    dict(
        ref="1.7", category="bug", tool="lum_get_case",
        title="没有任何只读入口能读回 bug_refs / expected_confirmed_by / blocked_external / tags / target_level",
        body="""这是 1.6 的加强版：问题比「回执漏报」更靠前 —— 这些字段压根没有读回路径。

lum_get_case 的描述逐字写着「读一条用例的全部内容」。实际返回只有
id / caseCode / title / type / priority / folderId / preconditions / steps /
expectedResult / automationStatus / source。没有 bugRefs、没有 expectedConfirmedBy、
没有 blockedExternal、没有 tags、没有 targetLevel。

完整生命周期：写入时 changed 部分漏报（1.6）→ 返回体只回显部分且可能截断（1.8）
→ lum_get_case 完全看不到 → lum_list_cases 只有派生布尔没有原文
→ lum_check_deliverable 只在触发某条 note 时顺带引用一小段。

也就是说，要确认一个字段写没写进去，唯一途径是去触发一条恰好会引用它的 note。
**这不是读取路径，这是副作用观测。**

建议：lum_get_case 把这些字段带上。纯增量、零风险，而且能同时消掉 1.6 的第二层后果。""",
        expected="工具描述写的是「读一条用例的全部内容」，那就该能读回全部已落库字段",
        actual="bugRefs / expectedConfirmedBy / blockedExternal / tags / targetLevel 五个字段任何只读入口都读不到",
    ),
    dict(
        ref="1.8", category="bug", tool="lum_update_case",
        title="bug_refs[].note 被静默截断，无任何提示",
        body="""传入的 note 结尾被砍掉，返回体里就是断的，没有任何 warning 或字段说明这里发生了截断。

和「展示截断」（2.4）不是一回事：那个是显示层截断、库里是全的；
这个是返回体里存的就是断的，再读回来也是断的。

建议：① 工具描述里给出长度上限（现在一个字都没提）；
② 超限时报错而不是静默截断 —— 让调用方自己决定砍哪一段，比平台从尾巴上一刀切好
（项目须知 200 字那条就是拒收不截断，那个处理方式是对的，正好可以对照）。""",
        expected="超长要么报错要么明确提示发生了截断",
        actual="静默截断，返回体和后续读回都是断的，无 warning",
    ),
    dict(
        ref="1.9", category="improvement", tool="lum_update_case",
        title="改 steps 会清掉落款，而 reconfirm 在落款已被清掉之后无东西可沿用",
        body="""实测三次调用形成一个隐藏约束：
① 传 steps + preconditions → 成功，警告「步骤/预期改动了，之前的『预期已确认』标记已失效」
② 传 expected_result + reconfirm=true → 成功，警告「reconfirm=True 但这条本来就没有落款，
   没东西可沿用」
③ 传 expected_confirmed_by + _note → 成功，警告消失

② 说明 reconfirm=true 只能在落款还在时沿用它；一旦 ① 把落款清掉，reconfirm 就失去了
作用对象。也就是说，**要保住落款必须在改 steps 的那一次调用里同时带上落款字段** ——
分两次做就一定会掉。这个约束在描述里没有，只能靠撞。

建议（任选其一）：reconfirm=true 在无落款可沿用时退化为「按本次传入的字段新建落款」；
或者在 ① 那条「标记已失效」的警告里直接把出路写出来。""",
        expected="",
        actual="",
    ),
    dict(
        ref="1.10", category="improvement", tool="lum_list_api_tests",
        title="folder_id 传用例目录 id 会静默返回空列表",
        body="""folder_id 期望的是**场景目录** id。但用例目录和场景目录都叫「目录」，
而 lum_get_folder_tree 返回的是**用例**目录树 —— 顺手把那个 id 传进来是很自然的动作。

传错的结果是 {"scenarios": [], "total": 0}：没有报错、没有警告，就是一个正常的空列表。
它和「这个目录下确实还没有场景」返回完全一样。当时的第一反应是「场景没写进去 /
写到别的分支了」，查了一圈才发现是 id 传错。

建议：folder_id 传进来时先判它属于哪类目录，属于用例目录就直接报错说明。
**能报错的地方不要返回空集合** —— 空集合是个合法答案，它会让调用方去排查一个
不存在的问题。""",
        expected="",
        actual="",
    ),
    dict(
        ref="1.11", category="improvement", tool="lum_add_project_note",
        title="项目须知超长拒收时应报出当前字数和超出量",
        body="""200 字硬上限、超限直接拒绝不截断、并给出三条明确指引（拆成两条 / 长文走
lum_push_skill / 一条只说一件事）—— 这个处理方式本身是正确示范，
正好和 1.8 的静默截断形成对照，**不要改掉它**。

唯一的摩擦：为一条须知压了三轮字数（248 → 211 → 110）。
建议在拒绝信息里直接给出当前字数和超出量（现在只说超了），省一轮试探。""",
        expected="",
        actual="",
    ),

    # ── 二、评审规则误报 ──────────────────────────────────────────
    dict(
        ref="2.1", category="bug", tool="AI 评审规则 control_group_in_one",
        title="control_group_in_one 累计 44 条误报、命中 0 条",
        body="""规则判词：「同一请求换了身份、断言一样，是对照组，拆成两条用例。」
累计 9 轮 44 条，全部驳回，一条未中。

它错在哪（同一处重复 44 次）：命中的不是「两个不同的人做同一件事」（那确实该拆），
而是**同一主体在状态变更前后的两次查询** —— 而这恰恰是被测点本身。
例如「审批人集合变化之后，已派发的待办会怎样」，变化前那次读和变化后那次读，
合起来才构成一个被测点。拆成两条之后前后各剩「某人此刻能查到 N 条待办」，
**「N 没变」这件事本身就没有任何一条用例在验了。**

判据其实很简单，而且平台自己在别处已经在用了：看这两步之间是否夹着一个改变状态的
动作步。夹着 → 前后对照，不能拆；没夹着 → 平行对照组，该拆。
**lum_check_assertion_bite 整个前提就是「动作步 + 前后断言」这个结构** ——
平台已经认了这个结构，只是评审规则这一侧没用上。这是同一个平台内部两个组件
对同一件事的认知不一致。

建议：按上面那条判据加前置条件；在此之前这条规则的收益是负的，
可以直接关掉 —— 44:0 的比分不需要再观察了。""",
        expected="规则命中的应当是真正的平行对照组（同一请求换身份、断言相同）",
        actual="命中的是「动作步前后的同一主体两次查询」，9 轮 44 条全部误报、0 命中",
    ),
    dict(
        ref="2.2", category="bug", tool="AI 评审规则（retry_timeout_ms）",
        title="保持型/竞态型断言被判「没开重试」，累计 13~14 次全部误报",
        body="""断言按语义分三类，重试窗对它们作用完全不同：
· 转换型（行为真的变了）→ 该开，异步下发要等收敛
· 保持型（行为不该变）→ 必须关。开了等于「多等一会儿看它变不变」，而这里要断的
  正是「它一直没变」
· 竞态型（A 与 B 必须同时回来）→ 绝不能开。重试窗会把竞态缺陷**重试掉** ——
  缺陷窗口过去后自然变绿

规则不看断言在断什么，只要 retry_timeout_ms=0 就一律建议改成 10000。

最能说明问题的一点：理由已经逐字写进步骤名了 ——「★保持型断言刻意不开 retry：
retry 只能把红拖成绿，修不了『抢在撤回前断到 pending』这种假绿；抢跑已实测排除
（降权后 0/2/5/10/20/30 秒六点采样恒 pending）」—— **照报不误**，
说明规则完全不读步骤名（见 2.3，这不是孤例）。

**危害方向：它建议的改法会制造假绿。** 竞态型断言一旦开了重试窗，缺陷窗口过去后
请求自然成功 —— 用例变绿、缺陷仍在、而且从此永远不会再红。
这是这一章里唯一一条「照做会主动降低质量」的规则。

建议：规则加例外判据 —— 步骤名/描述中出现「保持/不变/仍/依旧/竞态/单点取样」等
语义，或断言使用 not_contains / != 这类否定形态时不出这条警告。更彻底的做法是让步骤
显式声明 assertion_intent: transition | invariant | race，规则据此判定（也能顺带解决 2.1）。""",
        expected="重试建议只针对转换型断言；保持型/竞态型不该建议开重试窗",
        actual="只看 retry_timeout_ms=0 就一律建议 10000，13~14 次全部误报；照它改会把竞态缺陷重试成永久绿",
    ),
    dict(
        ref="2.3", category="bug", tool="AI 评审规则文案",
        title="规则文案给的两条出路一真一假，按假的那条改会白改一轮",
        body="""某条 blocker 的告警文案给了两条出路：
A. 改成不同的请求；B. 或者在步骤名里写明这是保持型断言（保持/不变/仍/依旧）。

专门按出路 B 做了一次实验：在步骤名里显式写上关键词并说明这是保持型断言，
回推后 —— warning 一处不减、blocker 一字未动。
随后改走出路 A 的变体，warning 从 19 降到 18、blocker 消失。
两次独立实验结论一致：**出路 B 完全无效，出路 A 有效。**

建议（成本极低、收益明确）：把出路 B 从文案里删掉，或者把它实现出来。
现状是最坏的一种 —— 它写在文案里，看起来是官方承诺的豁免路径，执行方按它改一轮、
回推一次，才发现没用。**文案里的每一条出路都应该是可执行的；做不到的那条留在那里，
比不给出路更糟。**""",
        expected="文案里承诺的豁免路径 B（步骤名写明保持型）应当真的能豁免",
        actual="按 B 改回推后 warning 一处不减、blocker 一字未动；按 A 改则 blocker 消失",
    ),
    dict(
        ref="2.4", category="bug", tool="AI 评审（用例正文读取）",
        title="评审对用例正文做读取截断，然后把「读不全」判成「用例写不全」",
        body="""评审报「预期写到一半没了」「expected 断在半句」，指控用例正文不完整。

这是可自证的误报：第八轮报的断点是一处、第九轮是另一处、第十轮又是第三处，
而这三轮之间那两个字段一个字都没改过。同一份文本三次断在不同位置，
只能是读取侧截断，不是写入侧缺失。后来定位到断点精确落在**第 400 个字符**。

累计 6 次（含 3 次可自证的位置漂移）。

建议：评审拿到被截断的字段时，要么明说「以下内容已截断」，要么不要对「结尾是否完整」
下判断。现在的形态会让人反复去改一个本来就完整的字段 —— 而「改一个没坏的东西」
有真实风险：为了让它看起来完整，人会去重写那段文字，可能反而改坏。""",
        expected="评审要么读全，要么明说自己读到的是截断内容，不对结尾完整性下判断",
        actual="读取侧在第 400 字截断，却把它判成用例正文写了一半；三轮报出三个不同断点而字段未变",
    ),
    dict(
        ref="2.5", category="bug", tool="AI 评审（mustFix 输出）",
        title="同一份评审里两条 major 互相否定",
        body="""累计 3 次原样复现。典型形态：
major-1：全条只有控制面证据，要求补数据面入口断言
major-2（同一份评审）：「不要在本条内硬加网关步骤（订阅全程 pending、终态 cancelled，
那组落差立不起来）」
**照任一条改都会被另一条判错。**

最后一次复现还多一个细节：提反对意见的那条自己承认「结论口径已经收窄」「不是本条假绿」——
它一边确认这条用例已经不构成假绿，一边仍然把同一条规则原样吐出来。
这说明问题不在判断，而在**规则层的输出没经过仲裁就并列进了同一份 mustFix**。

建议：mustFix 落盘前过一道一致性检查（哪怕只是「同一份里两条意见指向相反操作时，
合并成一条并标注冲突」）；或者把「规则层机械命中」和「模型层判断」分开标注 ——
现在两者混在同一个列表里、同一种严重度，读的人无从分辨哪条需要认真对待。""",
        expected="同一份 mustFix 里的意见不应互相否定",
        actual="major-1 要求补数据面断言、major-2 要求不要加网关步骤，照任一条改都会被另一条判错（3 次原样复现）",
    ),
    dict(
        ref="2.6", category="bug", tool="AI 评审规则（请求指纹）",
        title="两条规则对「什么算同一个请求」的定义不一致",
        body="""这是可证伪的实现不一致，不是主观分歧：
· blocker 那条规则看 query 参数 —— 同一 path、只把 status=pending 换成 page_size=100，
  它就不报了
· control_group_in_one 只看 method + path —— 同一 path 换 Authorization 头（换身份），
  它照报不误

同一份步骤数据、同一次回推，两条规则对「这两步是不是同一个请求」给出了互不相容的定义。

建议：两条规则共用同一个「请求指纹」函数（method + path + query + 关键请求头 + body 形状）。
现在显然是各写各的。统一之后 2.1 的误报会自动消失一大半。""",
        expected="同一次回推里，两条规则对「同一个请求」应当用同一个判据",
        actual="一条看 query 参数、另一条只看 method+path，对同一份步骤给出互不相容的判定",
    ),
    dict(
        ref="2.7", category="bug", tool="AI 评审规则 control_plane_only",
        title="control_plane_only 累计 14~15 次误报",
        body="""要求「补数据面入口断言」，但在被测点本身就在控制面的用例上
（例如审批流转、权限边界、审计留痕）反复命中。

从第 5 次起就不再逐条往上报，改为把驳回理由写进用例的 preconditions，
让判定留在用例里 —— **然后下一轮评审照样报**（同 2.2，规则不读用例正文）。

建议：和 2.2 同源 —— 规则要么读得懂用例正文里的驳回理由，要么给一个显式的
「本条被测点就在控制面」声明位，让作者一次性关掉它。""",
        expected="被测点本身就在控制面的用例不该被要求补数据面断言",
        actual="14~15 次误报；驳回理由写进 preconditions 后下一轮照报（规则不读用例正文）",
    ),
    dict(
        ref="2.8", category="improvement", tool="AI 评审（建议内容）",
        title="评审诊断对了，但给的方案会踩平台自己的缺陷",
        body="""评审指出竞态探针只靠 not_contains "headers" / not_contains "origin"，
押在「共享回显上游恰好返回 httpbin 那种响应形状」上，换个上游就会永久变绿且不报错 ——
**这个诊断完全正确，我采纳了。**

但它给的建议原文是「用 status != 200（或 in [404,418]）」。
如果选后者，就会撞上 1.2 的 in 恒 false，探针从此永久报红。
我是先花两跑验证了 != 真判、in 会踩坑才定的稿 —— **避开不是运气。**

建议：评审建议里引用具体算子时，最好过一遍平台自己的能力矩阵。
这也再次说明 1.2 值得优先修 —— **一个恒 false 的算子会顺着评审建议扩散出去。**""",
        expected="",
        actual="",
    ),

    # ── 三、工具可用性与「描述≠实现」 ────────────────────────────
    dict(
        ref="3.1", category="improvement", tool="lum_review_case",
        title="run_first=true 在大用例上必然超时，而超时提示写在描述中后段",
        body="""run_first=true 在 full 级用例上会撞 MCP 的 300s 空闲超时；
即使不带 run_first，静态审也会在 120s 转后台。

后果链：调用方看到超时，自然反应是重试一次 —— 而评审是跑完就落库的，
重试会触发第二轮真跑，占环境、也可能撞上并发。

平台已经做对的部分：lum_review_check 存在，能只读查「是在跑还是已出结论」，
且 lum_review_case 在已有评审在跑时会挡回 in_progress 不重复触发。这两条设计是对的。

还缺的：lum_review_case 的工具描述里应当**在最显眼处**写明「本调用可能超时，
超时不代表没跑完，去查 lum_review_check」—— 现在这句话在描述的中后段，
而超时发生时人是在焦虑状态下读它的。

附带一个数据点：run_first=true 的评审强度确实显著更高（同一条用例：静态审 84 分通过、
真跑 56 分打回）。所以这条不是「别用 run_first」，而是「run_first 是最有价值的模式，
却也是最容易超时的模式」—— 值得为它单独做异步化（返回 job id，轮询取结果）。""",
        expected="",
        actual="",
    ),
    dict(
        ref="3.2", category="improvement", tool="lum_sync_orchestrated_scenario",
        title="mode='replace' 传入步骤数少于现有步骤时应默认拒绝，而不是静默删掉",
        body="""patch 模式只能按 name 匹配已有步骤，改不了步骤名、加不了步骤。
任何加步骤或改名的改动都必须走 replace，而 replace 要求重传全部步骤 ——
最大的场景 59 步 / 53KB，为了改一个步骤名要把 53KB 原样重发一次。

**更危险的是它和「部分读取」的组合**：lum_get_api_test 支持 outline=True 和
step_names 点名读（这是好设计，省上下文）。但拿着部分读取的结果去走默认的
mode='replace'，会把没读到的步骤整个删掉，而且当场不报错。

平台已经在返回里带了 partial / stepsTotal 和警告 —— 这是对的。
但「警告」这个强度，对一个会静默删数据的操作来说偏弱。

建议：replace 时若传入步骤数 < 该场景现有步骤数，默认拒绝，要求显式加一个
confirm_delete_missing: true；或者提供 mode='upsert_by_seq' 之类允许按序号增改而不删。""",
        expected="",
        actual="",
    ),
    dict(
        ref="3.3", category="improvement", tool="hardcodeWarnings（回推校验）",
        title="hardcodeWarnings 不看断言在断什么，对所有 retry_timeout_ms=0 一律建议 10000",
        body="""本轮有 5 处刻意为 0（3 处竞态 + 2 处保持型），5 处全部不采纳。

这是 2.2 的工具侧同源表现 —— 评审规则那一侧和回推校验这一侧对同一件事各有一份实现，
所以修的时候要一起修，否则关掉一侧另一侧照报。

建议：和 2.2 共用同一个「断言意图」判据（步骤名语义 / 否定形态算子 /
显式的 assertion_intent 声明）。""",
        expected="",
        actual="",
    ),
    dict(
        ref="3.4", category="improvement", tool="接口场景执行报告",
        title="wait_ms 不计入执行耗时统计",
        body="""duration 不含 wait_ms。一个含 4 秒等待的步骤显示耗时几十毫秒。

不算 bug，但会误导性能判断 —— 尤其在排查「为什么这条用例跑了两分钟」时，
把各步 duration 加起来对不上总时长，会让人以为哪里丢了时间。

建议：分开报 duration 和 wall_time，或至少在描述里写明 duration 不含等待。""",
        expected="",
        actual="",
    ),
    dict(
        ref="3.5", category="improvement", tool="lum_sync_orchestrated_scenario / lum_get_api_test",
        title="断言取值字段、读写大小写、field 路径三处不对称，只能靠撞",
        body="""三处不对称：
① 断言取值字段：status / body_contains 用 value；body_field 用 expected
② 读写大小写：写 snake_case，读回 camelCase
③ field 路径不带 $.

第一条最容易错，因为同在 assertions[] 数组里、同一个位置，却按 type 换 key 名。

建议：两个 key 都接受（择一为正、另一为别名），或在校验失败时明确提示
「type=body_field 请用 expected」。②③ 至少写进工具描述。""",
        expected="",
        actual="",
    ),

    # ── 四、结构性不足：所有门禁都是「内向」的 ────────────────────
    dict(
        ref="4.1", category="requirement", tool="覆盖统计",
        title="覆盖没有分母 —— 平台能报质量，不能报「该测多少、还差多少」",
        body="""23 条全绿 = 100% 通过率，读起来像「系统测过了」，实际含义是
「我们测的这 23 件事没坏」。这两句话之间的距离，就是没有分母造成的。

**缺口是沉默的** —— 一个从没被写过用例的功能点，在平台上不产生任何信号。
它不红、不黄、不出现在任何队列里。

lum_module_checkup 的 coverageGaps 是往这个方向走的，且 observed_actions 参数
（把页面上探到的可操作项传进来对账）是**正确的形状**。但它是「建议清单不是门禁」，
且分母靠模型从标题猜。

这是整份反馈里我认为最值得投入的方向之一：平台所有门禁都是「内向」的 ——
检查用例自己写得好不好；没有一个是「外向」的 —— 检查用例跟被测系统现在的样子
还对不对得上。内向门禁做得非常足，但它们的极限是「这些断言都咬得住」，
回答不了「这个系统能不能发」。""",
        expected="",
        actual="",
    ),
    dict(
        ref="4.2", category="requirement", tool="lum_apply_endpoint_diff / 覆盖对齐",
        title="用例和被测系统之间没有任何对齐检查",
        body="""危险的方向不是字段改名 —— 那会让用例变红、有人看，这是**好的**。

危险的是：产品新增了状态值 / 分支 / 字段，而用例完全不知道 ——
覆盖悄悄下降，而所有指标全绿。

lum_check_assertion_bite 抓不到这个（它验因果，不验对齐）。
lum_apply_endpoint_diff 是往这个方向走的，但它要求调用方**自己报上变更清单** ——
而「我不知道产品加了个新状态」正是这个问题本身。

建议方向：让平台能从被测系统侧主动取一次现状（OpenAPI / 路由表 / 枚举值），
和用例侧的覆盖求差集，而不是依赖调用方先知道自己不知道什么。""",
        expected="",
        actual="",
    ),
    dict(
        ref="4.3", category="requirement", tool="执行报告",
        title="执行报告里没有被测版本，红了分不清「产品改了 / 环境坏了 / 用例过期」",
        body="""用例红了，分不清三种情况：产品改了 / 环境坏了 / 用例过期。
没有版本戳就没有基线。

这也是第四章里**最便宜**的一条：执行时记一个被测系统版本（git sha / 镜像 tag /
health 接口里的 version 字段，取得到哪个算哪个），落进报告。

配合 4.5 还能自动给项目须知填时效标记。""",
        expected="",
        actual="",
    ),
    dict(
        ref="4.4", category="improvement", tool="lum_add_project_note / ai_review",
        title="项目须知混进了 ai_review 的产出，23 条里 9 条是判断结论不是事实",
        body="""读到的 23 条须知里有 9 条是判断结论（形如 [completeness] 缺少…竞态场景验证），
不是事实。

这和须知自己的定义打架 —— 工具描述逐字写着「只记你亲手撞到的事实，不记判断结论
（结论会过期，事实不会）」。而且这 9 条是重复的：同一个模块的同类结论出现多次。

后果：每轮开工都要读 23 条，其中 9 条是噪音。
**而须知的价值恰恰依赖于「短到每轮都会读」。**

建议：ai_review 的产出该落成用例上的待办或独立的评审记录，须知只留「这个系统什么
地方反直觉」。这条立刻可做、纯收益。""",
        expected="",
        actual="",
    ),
    dict(
        ref="4.5", category="improvement", tool="lum_add_project_note",
        title="项目须知没有时效标记",
        body="""带实测数值的须知（如「配置推送约 55~70ms 收敛」）会随版本漂移，
但没有任何字段标注它实测于何时、何版本。

于是读的人无从判断「这个数还作数吗」—— 而一条过期的实测数值比没有更坏：
它看起来像事实。

建议：须知加 measuredAt / measuredVersion 两个字段，配合 4.3 的版本戳可以自动填。""",
        expected="",
        actual="",
    ),
    dict(
        ref="4.6", category="requirement", tool="执行结果状态",
        title="缺「前置不具备」这一态，环境没铺能力和用例坏了长得一样",
        body="""「环境本就没铺这个能力」和「用例坏了」现在长得一样，都是失败。

**平台其实已经握有这个信息**：共享资源探测已经能区分 exists / missing / unknown，
missing 且无 create_def 就是「前置不具备」。只是这个信息没有流到执行结果的状态里。

必须配一条纪律一起做：汇总里**逐条列出，不许拿它掩盖真实失败** ——
否则「没跑」会被读成「跑过了」，比失败更糟。""",
        expected="",
        actual="",
    ),
    dict(
        ref="4.7", category="improvement", tool="lum_get_sync_spec",
        title="kind='all' 上万字，外部 agent 第一次接触往往就调它然后被冲掉上下文",
        body="""lum_get_sync_spec(kind='all') 上万字。我逐条核对过，**没有可删的** ——
每一条背后都有一次真实踩坑。所以这是**分发**问题，不是内容问题。

而 kind 参数已经有了（order / naming / case / api_scenario / scenario_shape /
ui_script / variables / timing），只是 all 是默认值。

建议：默认返回短版（N 条铁律 + 各 kind 一句话索引），要细节再按 kind 取。
铁律候选（都是实测会栽的）：
1 动手顺序：先页面 → 取真流量 → 先写 UI → 再写接口场景
2 新写的断言先让它红一次
3 步骤里禁止写死（数据、凭据、地址、UUID）
4 验唯一性只能用 [*k=v] + length
5 body_contains 别当字段等值用
6 异步用 retry_timeout_ms，不用 sleep/wait_ms，不插假步骤占时间窗
7 文案套占位 ${键|中文}，不写死
8 多角色一人一个 browser context，不清 storage 换人
9 前置和清理走接口，不在页面上点
10 改全局配置的场景必须无条件复原""",
        expected="",
        actual="",
    ),
]


def evidence_of(item: dict) -> dict | None:
    ev = {"refs": [f"{SOURCE_DOC} §{item['ref']}"]}
    if item.get("expected"):
        ev["expected"] = item["expected"]
    if item.get("actual"):
        ev["actual"] = item["actual"]
    return ev
