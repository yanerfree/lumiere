"""MCP 工具 — 版本升级·分支对账。文档：docs/version-upgrade-branch-diff.md

**反查必须由 CC 发起，平台只有一半数据。**

| | 谁手里有 | 是什么 |
|---|---|---|
| 用例依赖了哪些端点、哪些字段 | **平台** | 每个步骤都存了 method + url + 断言字段路径 + 期望状态码 |
| 新版本到底改了什么 | **CC** | 在你本机的代码仓库里，`git diff v1.0..v2.0` 才看得到 |

影响清单 = 这两半求交集。所以：

1. 平台单独产不出影响清单 —— 分支复制那一刻还没人告诉它新版本改了什么。
   「复制完自动出清单」在原理上不成立。
2. 不做定时扫、不做每次执行后扫。**一个版本对一次账**（漏了可以补交）。
3. 平台不推，CC 拉 —— 清单落平台、挂 `lum_next_duty` 队列，会话关了也续得上。
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import branch_diff_review, branch_diff_service


async def list_branch_endpoints(session: AsyncSession, branch_id: str) -> dict:
    """【对账第一步】这个分支的用例依赖了哪些端点、哪些字段 —— 反查的**平台那一半**。

    回每个端点的归一化「路径模板」+ 用它的用例编号/场景/步骤名/期望状态码/断言字段路径。
    拿它跟你本机 `git diff <旧版本>..<新版本>` 的结果求交集，再调 lum_apply_endpoint_diff。

    ⚠ **必读返回里的「覆盖不到的」那一节**：手工步骤（JSONB 文本）和 UI 脚本
    （Playwright 正文）里没有结构化的 method/url，所以这套反查**探不到它们**。
    纯 UI 改版（页面拆分、改名、入口挪走）在这份端点表上一个字都不会变 ——
    而「没命中」下一步会被当成「接口没动、可以照抄」。那批用例只能你拿新版本的
    前端/需求改动自己比。

    参数: branch_id(分支UUID)
    """
    return await branch_diff_service.list_branch_endpoints(session, branch_id)


async def apply_endpoint_diff(
    session: AsyncSession,
    branch_id: str,
    changes: list | None = None,
    from_ref: str | None = None,
    to_ref: str | None = None,
) -> dict:
    """【对账第二步】把新版本的变更报上来，平台求交集落清单。**一个用例都不改。**

    分三堆 + 一堆：命中的进「要改」（`removed` 的进「该废候选」），没命中的进
    「照抄」，`kind=added` 的进「待补用例」。清单进 lum_next_duty 队列。

    `changes` 每条 `{url, method, kind, detail}`，kind 取值：
      · `removed`        端点没了 → 该废**候选**（不自动废，走 lum_request_deprecate 交证据）
      · `field_changed`  请求/响应字段变了 → 要改。detail 必填（变成什么）
      · `new_state`      新增了状态值/分支 → 要改。detail 必填
      · `renamed`        端点改名/挪位置 → **要改，不是要废**（改名在 UI 上长得像"没了"）
      · `added`          新版本新端点 → 「待补用例」。不报的话这块功能零覆盖，
                         而且**永远不会报错** —— 没有任何信号说这里本来该有覆盖

    **可以多次调**（补交漏报的）：命中累积，重复报同一条不会重复落。补交时**新命中的
    用例会被撤回待审，包括已经自动过审的** —— 自动过审的全部合法性来自「未命中」。

    命中的用例，预期落款自动打回「待重新确认」（需求变了、步骤没变那种漏网）。

    ⚠ 报 url 用**路由声明里的写法**（`/subscriptions/{id}/approve`），平台会归一化
    再匹配（剥 host、剥 query、把 id 段和变量占位压成通配、允许部署前缀差异）。
    匹配**故意偏向多命中** —— 多命中只是多审一次，漏命中是假绿。

    参数: branch_id(分支UUID), changes(变更数组), from_ref(旧版本号，如 v1.0.0),
    to_ref(新版本号)
    """
    return await branch_diff_service.apply_endpoint_diff(
        session, branch_id, changes=changes, from_ref=from_ref, to_ref=to_ref,
    )


async def request_deprecate(
    session: AsyncSession,
    case_id: str,
    reason: str,
    evidence: dict | None = None,
) -> dict:
    """提请废弃一条用例（新版本上这个场景不存在了）。**平台硬校验证据，交不齐不受理。**

    **假废弃比假绿更毒**：一条用例被误废，那块功能就再没人测了，而且**永远不报错** ——
    没有任何信号会说"这里本来该有覆盖"。假绿至少还在回归池里刷红。

    所以证据要正反两面：
      · 正面 `apiProbe=[{url, method, status}]` 打老端点拿到 404/410；
        或 `uiProbe=[{page, 找了什么, 结论, 截图}]` 在页面上走到那个位置，它不在
      · 反面 `searchedElsewhere=[...]` 功能有没有被搬到别处 —— 改名、挪菜单、
        拆页面在 UI 上**都长得像"没了"**

    提请只挂「待废审」，**用例状态一个字不动**；`lifecycle_status=deprecated`
    要等批准才落。批准有两条路：lum_review_case（这条用例有待决废弃请求时它不审六维，
    改审「该不该废」，平台自己复核接口那半边）、或人在列表页/详情页一条条确认。
    探不出来一律落人。

    参数: case_id(用例UUID), reason(一句话：为什么这个场景不存在了),
    evidence(证据对象，见上)
    """
    return await branch_diff_review.request_deprecate(
        session, case_id, reason, evidence=evidence,
    )
