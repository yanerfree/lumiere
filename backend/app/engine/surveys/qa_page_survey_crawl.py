"""页面枚举爬虫：把被测前端上**有哪些可操作项**扫成账本。

它回答的是三方对账里 Q 侧和 P 侧都答不了的那一问：「页面上有这个操作，用例里一条都
没覆盖」。这条链的每一跳都是纯代码（页面控件 → 请求 → 路由组 → 域码 → 场景 ID），
所以这里只负责**如实记下看到了什么**，一个判断都不做 —— 判断在 `qa_coverage_reconcile`。

**无向枚举不点不认识的控件**（理由在 `qa_survey_guard` 头部：不是环境归谁，
是爬虫不知道自己造了什么、也清理不掉）。五层只读的判定全在 `app/services/qa_survey_guard.py`，
这里只做三件事：调用判定、按判定 abort/跳过、把没做成的事记进账本。
判定不写在这里，也不写在 Playwright 回调里 —— 写在那儿的逻辑要起浏览器才测得到，
实际上就是不会被测（架构 AD-7）。

**账本比结果重要。** 没爬到的页、认不出的控件、抽不出的端点，一律记数并让这一趟落
`partial`；缺的信号一律算「没验证」，绝不算「验证过了」。少一条记录只是少一条，
把「没走到」写成「这个功能没了」会让对账那边凭空报出一批不存在的缺口。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from app.services.qa_page_traffic import (
    api_prefixes_from_routes,
    attach_control_edges,
    bucket_clicks,
    bucket_entries,
    merge_control_edges,
    merge_edges,
)
from app.services.qa_directed_chain import (
    CHAIN_FACTS,
    UNFILLABLE,
    chain_declarations,
    chain_meta,
    edit_value,
    finish_chain,
    new_chain,
    new_probe_tag,
    note_breakpoint,
    note_fact,
    note_step,
    note_write,
    pick_control,
    plan_fill,
    residue_findings,
    summarize_chains,
)
from app.services.qa_domain_map import absorb_reading, map_meta, summarize_maps
from app.services.qa_role_visibility import merge_shards
from app.services.qa_selectors import PROBE_JS, merge_probe
from app.services.ui_selector_render import anchor_selector, infer_kind
from app.services.qa_survey_guard import (
    MAIN_CRAWL_ROLE,
    SAFE_TO_CLICK,
    classify_control,
    click_intent,
    drop_credentials,
    is_write_request,
    pick_main_crawl_role,
    resolve_terminal_status,
    shallow_scan_roles,
)

log = logging.getLogger(__name__)

# 同时开几个浏览器上下文。2 是照架构 AD-4 定的：对方是**测试环境**，
# 不是压测靶子；并发调高省下的几分钟，换来的是"爬一次就把环境拖慢"的名声。
MAX_PARALLEL_SHARDS = 2

# 每个页面等多久算这页没起来。**超时记账、继续下一页**，不是整趟失败 ——
# 一页起不来就整趟红，等于把最常见的偶发变成"这个功能没了"。
PAGE_TIMEOUT_MS = 15_000

# 浅扫每个角色只看菜单和落地页：角色维度要的是「这个角色**看得见**什么」，
# 不需要把每个页面再点一遍。深爬只做主爬那一个角色。
# ⚠ 40 是**上限**不是目标：QA 仓现在只解析出 29 个静态页，40 > 29 ——
# 也就是说这个"浅"在今天的数据上**一页都没浅掉**，7 个角色跑的是同一份全量。
# 真跑一趟才看得出来（§10.5）。改小它会动覆盖面，留给批 2 定。
SHALLOW_MAX_PAGES = 40

# 关上下文（= HAR 落盘）等多久。和页面超时分开：这一步在 `finally` 里，
# 挂在这儿是**静默**的，连"这一片失败了"都报不出来。
CONTEXT_CLOSE_TIMEOUT_MS = 20_000


def _now() -> str:
    """时窗用的时刻。**必须和 HAR 里的 `startedDateTime` 同一把时钟。**

    现在两边都是本机：chromium 是 `pw.chromium.launch()` 起在本地的。哪天改成
    连远端浏览器（CDP），两把时钟就会差出去，表现是 `edgesUnwindowed` 一片 ——
    **那是看得见的**，比按最近的页面猜一个归属好得多（后者会把边归到错的页上，
    而错的归属在报告上和对的长得一模一样）。
    """
    return datetime.now(timezone.utc).isoformat()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _cfg(env_vars, name: str, default: str = "") -> str:
    """取一个配置项：**先看传进来的那份环境变量，再退回进程环境。**

    为什么不图省事在起爬之前 `os.environ.update(env_vars)`：进程环境**只有一份**，
    而这个后端一个进程里可以同时跑两个项目的枚举。后进来的那趟会把前一趟的
    `BASE_URL` 顶掉，于是就成了 `_base_url` 那句话说的事故 ——「打到了不该打的
    那台机器上，而脚本照跑不误、不报任何错」，只不过这回是我们自己造的。
    所以配置**必须跟着这一趟走**，不能挂在进程上。

    退回进程环境是给「在 shell 里手动跑一趟」留的（`BASE_URL=… python -m …`），
    不是主路：接口进来的那条一定带 `env_vars`。
    """
    if env_vars:
        val = env_vars.get(name)
        if val is not None and str(val).strip():
            return str(val)
    return _env(name, default)


def _base_url(env_vars=None) -> str:
    """被测前端地址。**只能从变量取。**

    写死一个地址在这里，后果不是"换环境挂了"（那还看得见），是**打到了不该打的
    那台机器上**，而脚本照跑不误、不报任何错。
    """
    base = _cfg(env_vars, "BASE_URL", "").strip().rstrip("/")
    if not base:
        raise ValueError(
            "没有 BASE_URL —— 页面枚举必须知道爬哪个环境，不猜、不用默认值。"
            "在项目环境变量里配上再跑。")
    return base


def _role_credentials(role: str, env_vars=None) -> tuple[str, str]:
    """角色账号。约定跟 `pw_conftest.py` 一致：`<ROLE>_USERNAME` / `<ROLE>_PASSWORD`。

    角色名里的横线换成下划线再转大写（`auditor` → `AUDITOR_USERNAME`，
    `teamb-admin` → `TEAMB_ADMIN_USERNAME`）。**角色名是变量前缀，不是账号名** ——
    实测那个环境里 `AUDITOR_USERNAME` 的值是 `qa-auditor`，拿值当角色名会查不到凭证。
    """
    prefix = role.replace("-", "_").upper()
    return (_cfg(env_vars, f"{prefix}_USERNAME"),
            _cfg(env_vars, f"{prefix}_PASSWORD"))


# ── L1：路由拦截 ──────────────────────────────────────────────────────────

def make_readonly_guard(ledger: dict, gate=None):
    """返回给 `context.route("**/*", …)` 用的回调。

    这里**只有**「问判定 + abort」两行是有意义的；判定本体在 `qa_survey_guard`。
    被拦下的写请求记进账本 —— 拦到东西不是"没事发生"，是**爬虫差点动了别人的数据**，
    这件事要能在页面上看见，不然下次就没人知道这层网救过命。

    `gate` 是给**有向链路**开的一道窗：它是个零参数函数，返回真时这一刻的写
    请求放行并单独计数（`directedWrites`）。三条纪律，少一条这层网就白搭：

      · **默认关**（`gate=None` ⇒ 恒 fail-closed），无向枚举那一路一个字都没变；
      · 开窗的人负责 `try/finally` 关上 —— 忘了关等于这一趟后面全程无保护；
      · **不许另开一个没有 guard 的 context 来写**。那样写请求既不过判定、
        也不计数，账本上和"什么都没发生"一模一样，
        而它恰恰是唯一一处我们真的在动别人环境的地方。
    """
    async def _guard(route, request):
        if is_write_request(request.method, request.url):
            if gate is not None and gate():
                ledger["directedWrites"] = ledger.get("directedWrites", 0) + 1
                await route.continue_()
                return
            ledger["writesBlocked"] = ledger.get("writesBlocked", 0) + 1
            ledger.setdefault("writesBlockedSample", [])
            if len(ledger["writesBlockedSample"]) < 20:
                ledger["writesBlockedSample"].append(
                    f"{request.method} {urlsplit(request.url).path}")
            await route.abort()
            return
        await route.continue_()

    return _guard


# ── 页面上的可操作项 ──────────────────────────────────────────────────────

# 在页面里跑的那段 JS：只读 DOM，什么都不点。
# 抽 `data-testid` 是为了让 anchor 优先落在稳定标识上（S6.4 接着用）。
_COLLECT_JS = """(rootSel) => {
  // `rootSel` 传了就只枚举那个层里的东西（弹窗/抽屉），不传就是整页。
  // **同一段 JS 两处用**：层里的输入框和页面上的输入框判据必须一模一样，
  // 各写一份的话两边迟早分叉，而分叉出来的差异会被 diff 报成「功能变了」。
  const root = rootSel ? document.querySelector(rootSel) : document;
  if (!root) return [];
  const out = [];
  const CTRL = 'button, a[href], [role="button"], [role="switch"], [role="tab"],'
             + ' [role="menuitem"], input[type="checkbox"], input[type="radio"]';
  // 表单字段。**它不是「点」的对象，是「这一页要填什么」的账** ——
  // 少了它，「表单功能覆盖了没」这个问题连问都问不出来：
  // 上一趟 1266 个控件里一个输入框都没有，看着像这个系统根本没有表单。
  const FIELD = 'input:not([type="checkbox"]):not([type="radio"]):not([type="hidden"]),'
              + ' select, textarea, [role="combobox"], [role="textbox"],'
              + ' [contenteditable="true"]';
  const sel = CTRL + ', ' + FIELD;
  for (const el of root.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;   // 藏起来的不算「页面上有」
    const isField = el.matches(FIELD);
    // ⚠ 字段的文案**绝不能取 `value`** —— 那是用户填进去的东西（用户名、密钥、
    // 备注），取了就等于把被测环境的数据抄进我们的账本，还会顺着 diff 一路留档。
    // HAR 那边凭据是**丢掉不是打码**，这里同一条纪律。
    const label = isField
      ? (el.getAttribute('aria-label') || el.getAttribute('placeholder')
         || el.getAttribute('name') || el.id || '').trim().slice(0, 120)
      : (el.getAttribute('aria-label') || el.innerText || el.value || '').trim().slice(0, 120);
    out.push({
      label: label,
      role: el.getAttribute('role') || el.tagName.toLowerCase(),
      testid: el.getAttribute('data-testid') || '',
      id: el.id || '',
      href: el.getAttribute('href') || '',
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      isField: isField,
      // 必填/只读是**表单规则**，不是控件状态：QA 脚本有没有验「空提交报错」
      // 要靠它对账。抽不到就是 false，不当成「不必填」。
      required: el.required === true || el.getAttribute('aria-required') === 'true',
      readonly: el.readOnly === true || el.getAttribute('aria-readonly') === 'true',
      fieldType: isField ? (el.getAttribute('type') || el.tagName.toLowerCase()) : '',
      // 下面三个只给**有向链路**定位用（`qa_directed_chain.field_selector`）。
      // 它们是页面写死的标记（不是 `value`），所以上面那条「绝不取 value」的
      // 纪律没被动过；少了它们，一半的表单框锚不住，链路会断在
      // 「填不出来」那一格上 —— 而那一格看起来像产品没有表单。
      name: el.getAttribute('name') || '',
      placeholder: el.getAttribute('placeholder') || '',
      ariaLabel: el.getAttribute('aria-label') || '',
    });
  }
  return out;
}"""


# 站内链接。**页面清单原来只有 QA 清单里那些静态字符串** —— 带参数的详情页
# （`routes.teamDetail` 这一类）在解析时就被丢进 `skipped`，一次都没爬过，
# 而"这个域的子菜单覆盖了没"问的正是它们。
#
# 做法是让页面**自己说**它能去哪儿：先把导航里折叠着的子菜单展开（那是纯读操作），
# 再把所有站内 `href` 收上来。**零业务词** —— 换一个项目、换一个域照样成立。
_MENU_JS = r"""async () => {
  // 折叠的子菜单先展开。限定在导航容器里：页面正文里的可折叠面板也带
  // `aria-expanded`，那些不是菜单，展开它们只会把无关内容点开。
  const NAV = 'nav, aside, [role="navigation"], [role="menubar"], .ant-menu, .ant-layout-sider';
  let expanded = 0;
  for (const box of document.querySelectorAll(NAV)) {
    for (const el of box.querySelectorAll('[aria-expanded="false"]')) {
      if (expanded >= 20) break;                     // 上限，别在一页上耗着
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      try { el.click(); expanded++; } catch (e) { /* 展不开就算了 */ }
    }
  }
  if (expanded) await new Promise(r => setTimeout(r, 400));   // 等展开动画
  const seen = new Set();
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    let p = a.getAttribute('href') || '';
    if (p.startsWith('#/')) p = p.slice(1);          // hash 路由
    if (!p.startsWith('/') || p.startsWith('//')) continue;   // 站外/协议相对
    p = p.split('?')[0].split('#')[0].replace(/\/+$/, '') || '/';
    if (seen.has(p)) continue;
    const r = a.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;   // 藏起来的不算"能去"
    seen.add(p);
    out.push(p);
  }
  return {paths: out, expanded: expanded};
}"""

# 弹层长什么样。`[role="dialog"]` 是 ARIA 标准，antd / element-plus / MUI 都带；
# 后面两条是框架兜底 —— **兜底放在最后**，标准命中时就用不上它们。
# 这几类点下去是**换页/排序**，不是开层。它们照旧被采集、照旧算「页面上有」，
# 只是不占开层预算 —— 2026-09-04 那一趟 255 次点击里 234 次是跳转、0 次开层，
# 预算全被左侧导航和表头吃光，真正的「新建」按钮一个都没轮到。
# 走的是**角色**不是文案：换个产品文案全变，角色不变。
NON_OPENER_ROLES = ("a", "link", "tab", "menuitem", "treeitem",
                    "columnheader", "th", "option")

# 层长什么样。**两条路，标准优先、几何兜底。**
#
# 旧写法是一串 CSS：`[role=dialog]` 加 antd 的两个类名。2026-09-04 真跑之后
# 发现它在这个产品上**恒为 0** —— 层开了，但那是 `div.fixed.inset-0.z-50`
# 加一块 `sm:w-[480px]` 的面板，既没 role 也不是 antd 的类。
# 而「一个都没开出来」在报告上和「这个产品没有弹窗」长得一模一样。
#
# 所以判据换成**它表现得像不像一个层**，不是**它叫什么名字**：
#   · 标准路：`role=dialog|alertdialog` 或 `aria-modal=true` —— 有就直接算；
#   · 兜底路：点完之后**新冒出来的**、`position` 是 fixed/absolute、
#     `z-index ≥ 20`、面积 ≥ 240×160 的可见块。
# 「新冒出来的」是关键：点之前先给现有的候选盖一个 `data-qa-pre`，
# 点完只看**没盖章的**。否则页面上常驻的吸顶栏、抽屉式侧边导航
# 都会被当成"刚开的层"，而那种误判会把整页控件挂到某个按钮名下 ——
# 不是少一条账，是**一条错的账**。
#
# ⚠ 别退回写类名。类名是某个 UI 库的实现细节，这套东西要能换产品用。
DIALOG_SEL = '[role="dialog"], [role="alertdialog"], [aria-modal="true"]'
# 认出来之后就地盖一个章，后面枚举/关闭都认这个章 —— 这样"层的范围"
# 只判定一次，不会枚举的时候按 A 算、关闭的时候按 B 算。
LAYER_SEL = '[data-qa-layer="1"]'

# 判「像不像一个层」。两处 JS 共用同一份，别抄成两份 ——
# 抄两份之后盖章的和找章的会慢慢长歪，而长歪了不报错。
_LAYER_CAND_JS = """
  const _cand = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0')
      return false;
    const r = (el.getAttribute('role') || '').toLowerCase();
    if (r === 'dialog' || r === 'alertdialog') return true;
    if (el.getAttribute('aria-modal') === 'true') return true;
    const pos = cs.position;
    if (pos !== 'fixed' && pos !== 'absolute') return false;
    const z = parseInt(cs.zIndex || '0', 10) || 0;
    if (z < 20) return false;
    const b = el.getBoundingClientRect();
    return b.width >= 240 && b.height >= 160;
  };
"""

# 点之前：给**现在就在**的候选盖 `data-qa-pre`。顺手清掉上一轮的章。
_MARK_PRE_JS = """() => {
""" + _LAYER_CAND_JS + """
  document.querySelectorAll('[data-qa-layer]').forEach(
    e => e.removeAttribute('data-qa-layer'));
  document.querySelectorAll('[data-qa-pre]').forEach(
    e => e.removeAttribute('data-qa-pre'));
  let n = 0;
  document.querySelectorAll('body *').forEach(el => {
    if (_cand(el)) { el.setAttribute('data-qa-pre', '1'); n++; }
  });
  return n;
}"""

# 点之后：只看**没盖过章**的候选。挑「装着表单控件最多」的那个 ——
# 遮罩层（那块半透明的黑）控件数是 0，面板才是我们要枚举的东西。
_FIND_LAYER_JS = """() => {
""" + _LAYER_CAND_JS + """
  const fresh = [];
  document.querySelectorAll('body *').forEach(el => {
    if (!el.hasAttribute('data-qa-pre') && _cand(el)) fresh.push(el);
  });
  if (!fresh.length) return null;
  const tops = fresh.filter(el => !fresh.some(o => o !== el && o.contains(el)));
  const pool = tops.length ? tops : fresh;
  const score = el => el.querySelectorAll(
    'input,select,textarea,button,[role="combobox"],[role="switch"],' +
    '[role="radio"],[role="checkbox"]').length;
  const area = el => {
    const b = el.getBoundingClientRect();
    return b.width * b.height;
  };
  let best = pool[0];
  for (const el of pool) {
    const d = score(el) - score(best);
    if (d > 0 || (d === 0 && area(el) > area(best))) best = el;
  }
  best.setAttribute('data-qa-layer', '1');
  const r = (best.getAttribute('role') || '').toLowerCase();
  const std = r === 'dialog' || r === 'alertdialog' ||
              best.getAttribute('aria-modal') === 'true';
  const b = best.getBoundingClientRect();
  return {how: std ? 'role' : 'geometry', tag: best.tagName.toLowerCase(),
          role: r, fields: score(best),
          w: Math.round(b.width), h: Math.round(b.height)};
}"""

# 一个角色一趟最多点开几个层。**是预算不是过滤**：用完了要在账本上留一格
# （`dialogBudgetExhausted` 记下是哪个角色），不然"这一页没有表单"和
# "预算用完了没去看"在报告上长得一模一样。
DIALOG_PROBE_BUDGET = 40
DIALOG_PROBE_PER_PAGE = 3
# 每个角色额外爬几个**菜单里发现、清单里没有**的页面。同样是预算。
MENU_EXTRA_MAX_PAGES = 25

_ID_SEG = re.compile(r"^(?:\d+|[0-9a-fA-F]{8,}|[0-9a-fA-F-]{16,})$")


def route_template(path: str) -> str:
    """`/teams/9f3a-…/members` → `/teams/:id/members`。

    详情页的 id 每一趟都不一样。拿**具体路径**当账本键的话，下一趟同一个页面
    会整批报成「新增」+「功能没了」—— 一次改版都没发生，报告却全是缺口。
    所以：**导航用具体路径，记账用模板。**
    """
    segs = []
    for s in (path or "").split("/"):
        segs.append(":id" if s and _ID_SEG.match(s) else s)
    return "/".join(segs)


def self_check_label(totals_probe) -> str:
    """这一趟到底做没做「爬前爬后自检」。

    **没配 probe 时 `dirty` 永远不会触发**，于是一趟 `done` 的含义会从
    「确认没动过环境」悄悄滑成「根本没查过」——**把没验证的事记成验证过了**，
    正是本模块存在的意义要抓的那类错。所以宁可在账本里明写 `notConfigured`。
    """
    return "done" if totals_probe else "notConfigured"


def degrade_for_gaps(status: str, ledger: dict) -> str:
    """账本里有缺口就不许叫 `done`。

    `resolve_terminal_status` 只看**分片**成没成；一个分片里跳过了半数页面，它照样
    算成功。缺的那部分信号在对账那边必须算「没验证」，而 `done` 的意思是「这一趟看
    全了」—— 把有缺口的一趟写成 `done`，下游就会把没爬到的页面报成「功能没了」。

    **只降不升**：已经是 `failed`/`dirty` 的不会因为"没缺口"被抬成 `done`。
    """
    if status != "done":
        return status
    if ledger.get("pagesFailed") or ledger.get("rolesSkipped"):
        return "partial"
    return status


def item_key(page_path: str, anchor: str, scope: str = "") -> str:
    """item 的主键，**唯一出处**。

    `scope` 是弹层前缀（`[新建]`）—— 少了它，弹层里那个「保存」和页面上
    那个「保存」会是同一行。而**点击时窗也拿它当归属键**（`bucket_clicks`）：
    两边各写一遍格式，哪天有人改了一处，边就会静静挂到另一个控件头上 ——
    JSON 列上没有外键，挂错了一条测试都不会红。
    """
    return f"{page_path}::{scope}{anchor}"


def _state_of(raw: dict) -> str:
    """`present` / `enabled` —— 只记**看得见的事实**。

    `reachable`（点了真能进去）要真点一下才知道，而那属于 L2 允许的读操作里
    最贵的一种；这一版不做，账本里就不会出现 `reachable`。
    **宁可少一档，也不要把 `enabled` 当成 `reachable` 写进去** ——
    那是把没验证过的事记成验证过了。
    """
    return "present" if (raw.get("disabled") or raw.get("readonly")) else "enabled"


def collect_items(page_path: str, page_title: str, raw_items, ledger: dict,
                  scope: str = "") -> list[dict]:
    """把一页的原始控件整理成账本行。**认不出的照记不漏，只是不点。**

    `anchor_kind` 走 `ui_selector_render.infer_kind`，**不在这里另写一套**：
    它是选择器登记表用的那套稳定性等级，S6.5 要拿爬到的锚点跟登记表对账，
    两套词表对不上的话「爬到的与登记不符」永远报不准。

    **锚不住的控件不出行**（无 testid / 无 id / 无可读文案的图标按钮）：
    记进 `controlsAnchorless` 让它在页面上看得见，但不凭序号编一个锚点 ——
    编出来的会随 DOM 顺序飘，下次插一个兄弟节点就把它报成「功能没了」。
    少一行只是少一行；凭空多一条「功能没了」会让人去查一个不存在的缺口。
    （**这个数不参与 `partial` 降级**：图标按钮到处都是，一有就降级等于这个信号
    永远亮着，亮着的信号没人看。真正的整改出口在 S6.5 的 `status='gap'` 登记。）
    """
    out = []
    for raw in raw_items or []:
        label = (raw.get("label") or "").strip()
        role = (raw.get("role") or "").strip()
        if raw.get("isField"):
            # 字段**不过 `classify_control`**：那套词表判的是「点下去会不会写」，
            # 而字段不是拿来点的。硬塞进去只会污染 `controlsUnknown` ——
            # 一个 placeholder 叫「请输入名称」的输入框会被记成「认不出的控件」,
            # 而它其实认得很清楚，只是不属于那个问题。
            kind = "field"
            ledger["fieldsSeen"] = ledger.get("fieldsSeen", 0) + 1
            if raw.get("required"):
                # 必填是**表单规则**：QA 脚本有没有验「空提交报错」靠它对账。
                # 这一版只进账本不进行（没有列），**记着比丢了强** ——
                # 丢了的话下次想问「必填项覆盖率」得重爬一趟。
                ledger["fieldsRequired"] = ledger.get("fieldsRequired", 0) + 1
        else:
            kind = classify_control(label, role)
            if kind == "unknown":
                ledger["controlsUnknown"] = ledger.get("controlsUnknown", 0) + 1
        selector = anchor_selector(testid=raw.get("testid") or "",
                                   elem_id=raw.get("id") or "", text=label)
        if not selector:
            ledger["controlsAnchorless"] = ledger.get("controlsAnchorless", 0) + 1
            ledger.setdefault("controlsAnchorlessPages", [])
            if page_path not in ledger["controlsAnchorlessPages"]:
                ledger["controlsAnchorlessPages"].append(page_path)
            continue
        anchor = raw.get("testid") or raw.get("id") or label
        anchor_kind = infer_kind(selector)
        # `scope` = 这一行是在哪个弹层里看见的。**只进 key 和标题，不进
        # `anchor`** —— anchor 是拿去还原选择器的原值（S6.5 要用），
        # 掺一个层名进去，登记表那边就再也对不上了。
        out.append({
            "key": item_key(page_path, anchor, scope),
            "page_path": page_path,
            "page_title": (f"{page_title} · 「{scope.strip('[]')}」层内"
                           if scope else page_title),
            "anchor": anchor,
            "anchor_kind": anchor_kind,
            "label": label,
            "control_type": kind,
            "state": _state_of(raw),
            # **NULL，不是 `[]`。** 这一列的三态一个都不许合：
            # 有边 / 点了没边（`[]`，G4 的料）/ **没点过**（NULL）。
            # 建行的时候写 `[]` 等于替一千多个还没碰过的控件宣布
            # 「点了，什么请求都没发」—— G4 会从个位数涨到四位数，全是假的。
            # 真点过的那几行由 `attach_control_edges` 覆盖。
            "endpoints": None,
            # 点击证据。**默认 False，一行不漏地写出来** —— 缺这个键的行会
            # 退回 run 级的 `controlsClicked`，而那个数从这一版起不再是 0：
            # 于是 1200 多个**没点过**的控件会跟着被记成「点了什么都没发生」。
            # （判据在 `qa_coverage_reconcile._click_evidence`，两边是一对。）
            "clicked": False,
            # 点下去有没有**可见反应**（弹层/跳转）。有反应就不是死按钮，
            # 哪怕它一个请求都没发 —— 「点开一个表单」本来就不该发请求。
            "effect": "",
        })
    return out


def dedupe_items(rows: list[dict], ledger: dict) -> list[dict]:
    """同一趟里 `key` 撞了的行**合成一行，并把撞了几次记成明账**。

    撞 key 的真实成因（2026-09-04 实测）：产品在**表格每一行**上用了同一个
    `data-testid`（`expand-row-button` 之类）。那不是 bug，是常见写法 ——
    `key = page_path + anchor` 于是一页出现 6 个一模一样的 key。

    为什么不让它撞库炸掉：`(survey_id, key)` 那条唯一约束确实是"锚点塌了"的
    探测器，但**探测器不该把病人打死** —— 一条撞库让整趟 214 页、7 个角色的
    产物一格都落不下来，报出来是 `status=failed`，而页面上和"这一趟没跑"
    长得一模一样。现在改成：**这里合并 + 记 `anchorCollisions`**，
    落库路径一个字不改（仍然不许 `on_conflict`，封样照旧盯着）。
    探测器没被拆掉，它从"炸库"挪成了"账本上的一个数"，反而查得到是哪一页哪个锚点。

    ⚠ 合并只在**采集处**做 —— 这里看得见它们是同一页上并排的兄弟节点。
    `merge_shards` 那边**照旧不许合**：跨分片它分不清"撞了"和"两个角色都看见了"。
    """
    # **先把格子建出来再数。** 缺键和 0 在页面上长得一样，而这个数现在是
    # 「锚点塌没塌」唯一的出口（此前那个出口是撞库炸掉整趟）——
    # 渲染成"没算过"等于把探测器拆了还没人知道。
    ledger["anchorCollisions"] = ledger.get("anchorCollisions", 0)
    out: list[dict] = []
    first: dict[str, dict] = {}
    for row in rows:
        k = row.get("key") or ""
        prev = first.get(k)
        if prev is None:
            first[k] = row
            out.append(row)
            continue
        ledger["anchorCollisions"] = ledger.get("anchorCollisions", 0) + 1
        hits = ledger.setdefault("anchorCollisionKeys", [])
        if k not in hits and len(hits) < 50:
            hits.append(k)
        # 合并的是**事实**，不是取第一条了事：后来那份点过 / 有反应的话，
        # 证据得留下来，否则 G4 会凭空多一条"点了没反应"。
        if row.get("clicked"):
            prev["clicked"] = True
        if row.get("effect") and not prev.get("effect"):
            prev["effect"] = row["effect"]
        if row.get("label") and not prev.get("label"):
            prev["label"] = row["label"]
    return out


# ── 页面里的 JS：必须自己带闸 ─────────────────────────────────────────────

async def _eval(page, js, arg=None):
    """`page.evaluate` 外面套一层 `wait_for`。**不套就是无限期。**

    Playwright 的 `evaluate` 没有 `timeout` 参数，也不吃 `set_default_timeout`
    —— 那两个管的是定位和导航。页面里的 JS 转不完，这个 await 就永远不返回，
    而 `goto` / `wait_for_load_state` 的 15s 一个都拦不到它。

    实测（2026-09-04，UAG 全量一趟）：7 个分片里 6 个在 4 分钟内收工、HAR 都落了盘，
    第 7 个（`platadmin`）卡在这里 **1 小时 46 分**，渲染进程一直 19% CPU 在转。
    外面看到的是"还在跑" —— 一趟既没有总时限、也没有逐页进度，
    于是**「挂死」和「慢」长得一模一样**，只能靠数 HAR 文件才看出来是哪一片没动。

    超时按**这一页失败**处理（调用点的 `except` 记 `pagesFailed` /
    `selectorProbeFailed`），不是整趟失败 —— 和 `PAGE_TIMEOUT_MS` 同一个口径。
    """
    coro = page.evaluate(js) if arg is None else page.evaluate(js, arg)
    return await asyncio.wait_for(coro, timeout=PAGE_TIMEOUT_MS / 1000)


# ── 弹层：点开、枚举、关掉 ────────────────────────────────────────────────

CLICK_TIMEOUT_MS = 3_000
DIALOG_WAIT_MS = 1_500
DIALOG_CLOSE_MS = 1_500


async def _close_dialog(page) -> bool:
    """把层关掉。**关不掉要如实返回 False** —— 调用方会整页重载。

    关不掉却当关掉了，后面几次探测全在同一个层上点，枚举出来的东西会挂到
    别的按钮名下 —— 那不是少一条账，是**一条错的账**。
    """
    # 认的是**刚才盖的那个章**，不是"页面上还有没有长得像层的东西"。
    # 后者会被常驻的吸顶栏/侧边抽屉一直判成"还开着"，于是每次都走整页重载。
    for _ in range(2):
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_selector(LAYER_SEL, state="hidden",
                                         timeout=DIALOG_CLOSE_MS)
            return True
        except Exception:                                # noqa: BLE001
            continue
    # 兜底点关闭按钮。`aria-label` 两条是标准，后面两条是 antd 的类名 ——
    # **只在这里留类名**：这是"再试一下"，不是判定层在不在。
    for sel in ('[aria-label="Close"]', '[aria-label="close"]',
                ".ant-modal-close", ".ant-drawer-close"):
        try:
            await page.locator(sel).first.click(timeout=CLICK_TIMEOUT_MS)
            await page.wait_for_selector(LAYER_SEL, state="hidden",
                                         timeout=DIALOG_CLOSE_MS)
            return True
        except Exception:                                # noqa: BLE001
            continue
    return False


async def _probe_dialogs(page, *, page_path: str, page_title: str, page_url: str,
                         raw_items, ledger: dict, budget: int,
                         seen_openers: set, clicks: list | None = None
                         ) -> tuple[list, dict, int, list]:
    """把这一页的「开层」按钮点开几个，**枚举层里的东西**，再关掉。

    返回 `(层里的账本行, {anchor: 反应}, 用掉几次预算, 点出来的新页面)`。

    为什么非做不可：不点开，整个系统的表单一条都枚举不到 —— 上一趟 1266 个
    可操作项里**一个输入框都没有**，而那不是"这系统没表单"，是表单都在层里。

    三条纪律：

    1. **只点 `opener`**（`click_intent`）。删除/退出那一档一个都不点，
       理由在那个词表上，不是"L1 会拦所以随便点"。
    2. **点开的那一下如果发了写请求，要单独记一格**（`openerBlockedWrite`）——
       L1 确实拦住了，但那说明这个按钮不只是开层，下次改判据要看这条。
    3. **关不掉就整页重载并停止这一页的探测**。宁可少探两个层，
       也不要把 B 层里的控件记到 A 按钮名下。
    4. **点下去那一刻记一格时窗**（`clicks`）——「这一次点击发出了什么请求」
       只能靠时间归属（HAR 里没有"这条请求属于哪次点击"这种字段），
       归属规则在 `qa_page_traffic.bucket_clicks`。
       时窗**只在点成功之后才登记**：点不着的那些一格都不留，
       否则它们会以「点了什么都没发」的名义进 G4，而我们根本没碰到它。
    5. **链接和表头不占预算**（`NON_OPENER_ROLES` / 带 `href`）。
       2026-09-04 实测：255 次点击里 234 次是跳转、开层 **0 次** ——
       名额全被左侧导航和 `Created At` 这种表头吃掉，真正的「新建」
       一个都没轮到。账本那时显示「点了 255 下」，看着非常健康。
    """
    out: list = []
    marks: dict = {}
    picked: list = []
    # 点「新建」跳到了一个新地址 —— **那个地址就是表单所在的页**。
    # 2026-09-04 实测：22 次开层点击里 11 次是跳转、弹层 0 次 ——
    # 这个产品的「新建」大多是**跳一页**而不是弹一层。跳走了就 goto 回来、
    # 把地址丢掉的话，表单字段一个也枚举不到，而账本上
    # 「点了 22 下」看着一切正常。**跳出来的页跟菜单发现来的同一个待遇。**
    found_paths: list[str] = []
    for raw in raw_items or []:
        if raw.get("isField") or raw.get("disabled"):
            continue
        label = (raw.get("label") or "").strip()
        role = (raw.get("role") or "").strip().lower()
        if not label or click_intent(label, role) != "opener":
            continue
        # 链接和表头不占预算：点它们是跳走/排序，层里的表单一个都看不到。
        # 带 `href` 的一律算链接 —— 有的产品用 `<a role="button">` 当导航。
        if role in NON_OPENER_ROLES or (raw.get("href") or "").strip():
            continue
        sel = anchor_selector(testid=raw.get("testid") or "",
                              elem_id=raw.get("id") or "", text=label)
        if not sel:
            continue                     # 锚不住的按钮不点：下一趟找不回同一个
        # 去重的粒度：**有 testid/id 的按同一性去重、只认文案的按页去重**。
        # 顶栏那个每页都在的齿轮按钮，`data-testid` 每页一样 —— 一次就够；
        # 而 `新建` 在「团队」和「智能体」两页背后是**两张不同的表单**，
        # 只按文案去重会把第二张整个丢掉，而这一版加开层就是为了看表单。
        memo = sel if not sel.startswith("text=") else f"{page_path}\x00{sel}"
        if memo in seen_openers:
            continue
        seen_openers.add(memo)
        picked.append((raw.get("testid") or raw.get("id") or label, label, sel))
        if len(picked) >= min(DIALOG_PROBE_PER_PAGE, max(budget, 0)):
            break

    used = 0
    for anchor, label, sel in picked:
        before = ledger.get("writesBlocked", 0)
        win: dict = {}
        try:
            # 点之前把「现在就在的层状物」盖上章 —— 点完只认没盖章的。
            await _eval(page, _MARK_PRE_JS)
        except Exception:                                # noqa: BLE001
            pass
        started = _now()      # 先取时间再点：反了的话点击瞬间的请求就落在窗外了
        try:
            await page.locator(sel).first.click(timeout=CLICK_TIMEOUT_MS)
        except Exception as e:                           # noqa: BLE001
            # 点不着（被遮住、瞬间消失、多个同名）。**不算点过** ——
            # 算了的话它会以「点了什么都没发生」的名义进 G4，
            # 而真相是我们根本没碰到它。
            ledger.setdefault("dialogClickFailed", []).append(
                {"page": page_path, "label": label, "error": type(e).__name__})
            continue
        used += 1
        ledger["controlsClicked"] = ledger.get("controlsClicked", 0) + 1
        # 点成了才登记时窗。右边界在下面每一条岔路上各自盖 ——
        # **一律不延长**：点完紧接着是关层 / goto 回来，延长会把关层和
        # 重载的流量算成"这个按钮发的"。
        win = {"page": page_path, "key": item_key(page_path, anchor),
               "anchor": anchor, "label": label, "startedAt": started}
        if clicks is not None:
            clicks.append(win)
        effect = ""
        # 轮询而不是一次 `wait_for_selector`：判据是"新冒出来的"，
        # 这件事只有对比之后才知道，没有哪个 CSS 选择器能等它。
        shape = None
        deadline = time.monotonic() + DIALOG_WAIT_MS / 1000
        while True:
            try:
                shape = await _eval(page, _FIND_LAYER_JS)
            except Exception:                            # noqa: BLE001
                shape = None
            if shape or time.monotonic() >= deadline:
                break
            await page.wait_for_timeout(150)
        if shape:
            effect = "dialog"
            ledger["dialogsOpened"] = ledger.get("dialogsOpened", 0) + 1
            # 是靠标准属性认出来的，还是靠几何兜底认出来的 —— **两格都 0 也渲染**。
            # 兜底那一格常年为 0，说明产品守 ARIA；它一旦变成大头，
            # 就该去问前端为什么层上没有 `role="dialog"`（脚本定位也会跟着难写）。
            by = ledger.setdefault("layersBy", {"role": 0, "geometry": 0})
            by[shape.get("how") or "geometry"] = by.get(
                shape.get("how") or "geometry", 0) + 1
            if len(ledger.setdefault("layerShapes", [])) < 20:
                ledger["layerShapes"].append(
                    {"page": page_path, "label": label, **shape})
            try:
                inner = await _eval(page, _COLLECT_JS, LAYER_SEL)
            except Exception:                            # noqa: BLE001
                inner = []
                ledger.setdefault("dialogCollectFailed", []).append(
                    {"page": page_path, "label": label})
            out.extend(collect_items(page_path, page_title, inner, ledger,
                                     scope=f"[{label}]"))
            # 右边界落在**关层之前**：关层自己会发请求（有的产品在关的时候
            # 提交/刷新），算进来就成了"点开这个按钮会调那条端点"。
            win["endedAt"] = _now()
            if not await _close_dialog(page):
                ledger.setdefault("dialogsStuck", []).append(
                    {"page": page_path, "label": label})
                marks[anchor] = effect
                # 这条岔路 `break` 掉了，收尾那两行走不到 —— 在这儿补。
                win["effect"] = effect
                win.setdefault("endedAt", _now())
                try:
                    await page.goto(page_url, timeout=PAGE_TIMEOUT_MS)
                    await page.wait_for_load_state("networkidle",
                                                   timeout=PAGE_TIMEOUT_MS)
                except Exception:                        # noqa: BLE001
                    pass
                break
        elif urlsplit(page.url).path != urlsplit(page_url).path:
            # 没弹层，但**跳走了** —— 也是"有反应"，同样不是死按钮。
            effect = "navigate"
            ledger["dialogsNavigated"] = ledger.get("dialogsNavigated", 0) + 1
            found_paths.append(urlsplit(page.url).path)
            # 右边界落在**跳回去之前**。这一格里混着**目标页自己的加载流量** ——
            # 那不是这个按钮"调"的。所以 `effect` 必须跟着边走（`bucket_clicks`
            # 会把它带上），丢了就变成一句错话。
            win["endedAt"] = _now()
            try:
                await page.goto(page_url, timeout=PAGE_TIMEOUT_MS)
                await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
            except Exception:                            # noqa: BLE001
                pass
        else:
            # 点了、没弹层、没跳转 —— 看得见的反应是没有了，
            # **但"发没发请求"还得看时窗**：静默保存/静默刷新也长这样。
            # 真正的 G4 是两样都空（`bucket_clicks` 的 `controlsSilent`）。
            ledger["dialogsNoEffect"] = ledger.get("dialogsNoEffect", 0) + 1
            win["endedAt"] = _now()
        if ledger.get("writesBlocked", 0) > before:
            ledger.setdefault("openerBlockedWrite", []).append(
                {"page": page_path, "label": label})
        marks[anchor] = effect
        win["effect"] = effect
        # 兜底：上面三条岔路都盖过章了，这里只管万一漏掉的那条。
        # **盖不上就是盖不上** —— 没有右边界的窗在 `bucket_clicks` 里整条弃掉
        # 并记 `clickWindowsUnclosed`，绝不当成「点了没发请求」。
        win.setdefault("endedAt", _now())
    return out, marks, used, found_paths


# ── 有向链路：造自己那一条，再在它身上把这个域走完 ─────────────────────────
#
# 判据全在 `app/services/qa_directed_chain.py`（纯函数、零业务词）。
# 这里只有「怎么点」，一个判断都不做 —— 那边换个产品照样成立，
# 这边换个产品要改的只有 Playwright 的用法。

# 一趟最多几条链。**是预算不是过滤**：每一页都造一条会把被测环境撑爆，
# 而"撑爆"的表现是下一趟"列表里有 300 条"这类断言开始时红时绿。
CHAIN_BUDGET = 6
# 点完等多久收请求。比 `DIALOG_WAIT_MS` 长：写请求要落库，回得比开层慢。
CHAIN_SETTLE_MS = 2_500

ROW_SEL = '[data-qa-probe-row]'

# 在列表里找到**自己那一行**并圈出它的范围。
#
# 为什么非要圈：行内的「编辑 / 删除」在每一行上都长一模一样（同 testid、
# 同文案），不圈就是 `.first` —— 那点的是**列表第一行**，很可能是别人的数据。
# 这不是"少测一点"，是**动了不该动的东西**，而账本上看不出来。
#
# 优先级 `tr` > `role=row` > `li`：toast 提示里也会出现刚建好的名字
# （「创建成功：qa-probe-7f3a」），那种一般是 `div`，圈不出行来 —— 圈不出来
# 就报 `row_unscoped`，**一个写按钮都不点**。
_MARK_ROW_JS = r"""(tag) => {
  const t = String(tag).toLowerCase();
  for (const el of document.querySelectorAll('[data-qa-probe-row]'))
    el.removeAttribute('data-qa-probe-row');
  const hits = [];
  for (const el of document.querySelectorAll('td, th, li, a, span, div, p')) {
    if ((el.textContent || '').toLowerCase().includes(t)) hits.push(el);
  }
  if (!hits.length) return {found: false, scoped: false, scope: ''};
  // 三档偏好各自找一遍，宁可多走两轮也不要"最深的那个恰好在 toast 里"。
  const PREF = [
    (n) => n.tagName && n.tagName.toLowerCase() === 'tr',
    (n) => n.getAttribute && n.getAttribute('role') === 'row',
    (n) => n.tagName && n.tagName.toLowerCase() === 'li',
  ];
  for (let i = 0; i < PREF.length; i++) {
    for (const hit of hits) {
      let node = hit;
      while (node && node !== document.body) {
        if (PREF[i](node)) {
          node.setAttribute('data-qa-probe-row', '1');
          return {found: true, scoped: true,
                  scope: (node.tagName || '').toLowerCase()};
        }
        node = node.parentElement;
      }
    }
  }
  return {found: true, scoped: false, scope: ''};
}"""

# 下拉挑第一个能挑的。原生 `<select>` 和"点开再挑"的自定义组件都要认 ——
# 只认一种的话，另一种会以「必填项填不出来」的名义把整条链断在第一步，
# 而那一格看起来像"这个产品的表单我们填不了"。
_PICK_OPTION_JS = r"""(sel) => {
  const el = document.querySelector(sel);
  if (!el) return 'gone';
  if (el.tagName && el.tagName.toLowerCase() === 'select') {
    const opts = [...el.options].filter(o => !o.disabled && o.value !== '');
    if (!opts.length) return 'empty';
    el.value = opts[0].value;
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return 'ok';
  }
  return 'interactive';
}"""

# 「点完之后页面变成什么样」—— §14.1 那四样里的三样原料
# （规则的提示原文 / 状态 / 结构；第四样「动作面」用的还是 `_COLLECT_JS`）。
#
# **全走语义标签和 ARIA，零类名、零业务词**：换个 UI 库、换个产品照样成立。
# 判据一个都不在这儿 —— 这里只把原文捞上来，怎么归类在
# `app/services/qa_domain_map.py`（纯函数，可以拿同一份原料重算）。
_READ_JS = r"""() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    return r.width > 0 || r.height > 0;
  };
  const txt = (el) => (el.innerText || el.textContent || '').trim();

  // ① 提示。**原文照抄，一个字不压** —— 提示原文就是规则本身
  // （§14.1 那条 ⚠：压成 pass/fail 之后这条约束下一趟就查不回来了）。
  // `role=alert|status` 和 `aria-live` 是 ARIA 标准，各家 toast/表单报错都用它。
  const hints = [];
  const HSEL = '[role="alert"], [role="status"], [aria-live="polite"],'
             + ' [aria-live="assertive"], [aria-errormessage]';
  for (const el of document.querySelectorAll(HSEL)) {
    if (!vis(el)) continue;
    const s = txt(el);
    if (s && s.length <= 300 && !hints.includes(s)) hints.push(s);
  }
  // 校验没过的字段，它自己指向的那句提示。
  for (const f of document.querySelectorAll('[aria-invalid="true"]')) {
    const ids = (f.getAttribute('aria-describedby') || '').split(/\s+/);
    for (const id of ids) {
      if (!id) continue;
      const el = document.getElementById(id);
      if (!el || !vis(el)) continue;
      const s = txt(el);
      if (s && s.length <= 300 && !hints.includes(s)) hints.push(s);
    }
  }

  // ② 区块标题。差集 = **建完一条数据才出现的结构**（审批记录、操作日志、
  // 关联列表都在这里冒出来）。这里只捞标题，**不解释它是什么** ——
  // 解释要认产品名词。
  const sections = [];
  const SSEL = 'h1, h2, h3, h4, h5, h6, [role="heading"], [role="tab"],'
             + ' legend, caption, summary';
  for (const el of document.querySelectorAll(SSEL)) {
    if (!vis(el)) continue;
    const s = txt(el).slice(0, 80);
    if (s && !sections.includes(s)) sections.push(s);
  }

  // ③ 列表每一行的单元格文本。拿它**数**这个对象一共有几种状态
  // （§14.3：同一列里反复出现的少数几个短词）。⚠ 只取行内文本，
  // 不取 `value` —— 那条纪律和 `_COLLECT_JS` 一样。
  const cells = [];
  let ourRow = [];
  const RSEL = 'tr, [role="row"], li';
  for (const row of document.querySelectorAll(RSEL)) {
    if (!vis(row)) continue;
    const kids = row.querySelectorAll('td, th, [role="cell"],'
                                    + ' [role="gridcell"], [role="columnheader"]');
    let vals = [];
    if (kids.length) {
      for (const c of kids) vals.push(txt(c).slice(0, 60));
    } else {
      const s = txt(row).slice(0, 60);
      if (s) vals = [s];
    }
    if (!vals.length) continue;
    if (cells.length < 200) cells.push(vals);
    if (row.getAttribute('data-qa-probe-row') === '1') ourRow = vals;
  }
  return {hints: hints, sections: sections, cells: cells, ourRow: ourRow};
}"""


async def _run_chain(page, *, page_path: str, page_url: str, raw_items,
                     ledger: dict, chains: list, clicks: list, windows: list,
                     gate: dict) -> list[str]:
    """在这一页上走**一条**有向链路。返回新解锁的页面路径。

    顺序是需求 §12.3 那条：`新建 → 列表 → 详情 → 编辑 → 回列表确认 → 删除
    → 确认没了`。每一环记一格，断了**只记第一个断点**（后面没走过，
    写"失败"是一句我们没验证过的话）。

    三条硬约束（§12.4），少一条这一维就不该开：

    1. **自带清理且能查账** —— 删不掉要报残留（`residue_findings`），
       不是"下次再说"。
    2. **只造改删自己前缀的那一条** —— 判据在文本前缀上，不在按钮名字上；
       圈不出行范围（`row_unscoped`）就一个写按钮都不点。
    3. **每页最多一条链一次新建** —— 这个函数一页只调一次，预算在
       `CHAIN_BUDGET`。

    L1 那道网在这段时间里**为我们开一道窗**（`gate`），窗上的每个写请求都
    单独计数（`directedWrites`）。窗在 `finally` 里关 —— 忘了关等于这一趟
    后面全程无保护，而无保护跑起来一切正常。
    """
    unlocked: list[str] = []
    create = pick_control(raw_items, "create")
    if create is None:
        # 有「新建」但是灰的 —— **这是一条业务规则**（当前状态/当前角色不让建），
        # 比"没有这个功能"值钱得多。两件事不许合成一个"没找到"。
        dis = pick_control(raw_items, "create", allow_disabled=True)
        if dis is not None:
            ledger.setdefault("chainCreateDisabled", []).append(
                {"page": page_path, "label": (dis.get("label") or "").strip()})
        return unlocked
    if not anchor_selector(testid=create.get("testid") or "",
                           elem_id=create.get("id") or "",
                           text=(create.get("label") or "").strip()):
        # 锚不住就不点：下一趟找不回同一个按钮，而"这一趟点的是哪个"就成了悬案。
        ledger.setdefault("chainCreateAnchorless", []).append(page_path)
        return unlocked

    tag = new_probe_tag()
    chain = new_chain(page_path, tag)
    chains.append(chain)
    # 勾选之前页面上有哪些控件。批量条靠**差集**认出来（见 `probe_batch`）。
    base_labels = {(r.get("label") or "").strip() for r in raw_items or []}
    # 页面级那本账里占住这段时间：不占的话这几下会落进 `edgesUnwindowed`，
    # 读起来像"归不了页的漏账"。`tail: False` —— 一格都不许延长。
    win = {"path": page_path, "kind": "directed", "tail": False,
           "startedAt": _now()}
    windows.append(win)

    pending: list = []

    def _on_resp(resp):
        try:
            if is_write_request(resp.request.method, resp.url):
                pending.append(resp)
        except Exception:                                # noqa: BLE001
            pass

    page.on("response", _on_resp)

    async def flush():
        """把这一步发出去的写请求记进链的账本。

        报错原文**只在非 2xx 时留**、而且截断：服务端说"名称已存在"是最好的
        线索；2xx 的响应体里是被测环境的业务数据，一个字都不该抄进我们的账本。
        """
        while pending:
            resp = pending.pop(0)
            body = ""
            status = getattr(resp, "status", None)
            if not (isinstance(status, int) and 200 <= status < 300):
                try:
                    body = (await resp.text())[:300]
                except Exception:                        # noqa: BLE001
                    body = ""
            note_write(chain, method=resp.request.method,
                       path=urlsplit(resp.url).path, status=status, body=body)

    async def click(raw, *, root: str = "", scope: str = "") -> bool:
        """点一下并**登记点击时窗** —— 控件级那条 `按钮 → 写接口` 的边就靠它。

        `root` 非空时定位限定在那个范围里（行内 / 层内）。**行内操作必须给
        `root`** ：不给就是 `.first`，点的是列表第一行 = 别人的数据。
        `scope` 是记账用的 key 前缀，和 `root` 是两件事：行内那几个按钮
        **本来就是这一页的控件**（page 级枚举里有它们），所以 `scope` 留空，
        边才落到那一行 item 的 `endpoints` 上。
        """
        label = (raw.get("label") or "").strip()
        anchor = raw.get("testid") or raw.get("id") or label
        sel = anchor_selector(testid=raw.get("testid") or "",
                              elem_id=raw.get("id") or "", text=label)
        if not sel:
            return False
        loc = page.locator(root).locator(sel) if root else page.locator(sel)
        started = _now()
        try:
            await loc.first.click(timeout=CLICK_TIMEOUT_MS)
        except Exception as e:                           # noqa: BLE001
            ledger.setdefault("chainClickFailed", []).append(
                {"page": page_path, "label": label, "error": type(e).__name__})
            return False
        ledger["controlsClicked"] = ledger.get("controlsClicked", 0) + 1
        w = {"page": page_path, "key": item_key(page_path, anchor, scope),
             "anchor": anchor, "label": label, "startedAt": started}
        clicks.append(w)
        await page.wait_for_timeout(CHAIN_SETTLE_MS)
        w["endedAt"] = _now()
        w["effect"] = ""
        await flush()
        return True

    async def back_to_list() -> dict:
        """回列表、重新圈出自己那一行。**每次导航后都要重圈** ——
        标记是打在 DOM 上的，一次重载就没了，而没重圈的 `ROW_SEL` 会一个都
        匹配不到，行内点击全部"点不着"（看起来像按钮不存在）。
        """
        try:
            await page.goto(page_url, timeout=PAGE_TIMEOUT_MS)
            await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
        except Exception:                                # noqa: BLE001
            pass
        try:
            return await _eval(page, _MARK_ROW_JS, tag) or {}
        except Exception:                                # noqa: BLE001
            return {}

    async def read(step: str, *, where: str = "page", items=None) -> dict:
        """§14.1：每点一步，把**四样**读一遍（规则 / 状态 / 动作面 / 结构）。

        「只记有没有反应」是这一维原来最大的毛病 —— 那样的账本上，
        「点完弹了一句『名称已存在』」和「点完什么都没发生」长得一样，
        而前者是**一条业务规则**，后者才是断点。

        `where` 说这批控件**属于哪一行 / 哪一层**（§14.2 那句「欠的是归类」）。
        调用它本身就记一笔「探过这一层」—— 探过、一个都没有是产品的事实，
        压根没探才是我们的欠账，两者不许混（§15.3）。
        """
        try:
            got = await _eval(page, _READ_JS) or {}
        except Exception:                                # noqa: BLE001
            got = {}
        absorb_reading(chain, step=step, where=where, read=got, items=items)
        return got

    async def probe_batch(row_items) -> None:
        """勾一行，看**批量条**冒出来什么（§15.2 那条因果里的一层）。

        不勾选就没有批量条 —— 这一层「结构上就看不到」，
        而看不到在报告上和"这个产品没有批量操作"长得一样。
        勾选本身是纯前端状态，不发写请求；勾完**立刻取消**，不留痕。

        新冒出来的按钮靠**和勾选前的页面控件做差集**认出来 ——
        不认类名、不认位置，换个产品照样成立。
        """
        box = next((r for r in row_items or []
                    if (r.get("fieldType") or r.get("role") or "").lower()
                    in ("checkbox", "radio")), None)
        if box is None:
            # 行上没有勾选框 —— **探过了**，这是产品的事实，不是欠账。
            await read("list", where="batch", items=[])
            return
        if not await click(box, root=ROW_SEL):
            await read("list", where="batch", items=[])
            return
        try:
            after = await _eval(page, _COLLECT_JS) or []
        except Exception:                                # noqa: BLE001
            after = []
        fresh = [r for r in after
                 if (r.get("label") or "").strip()
                 and (r.get("label") or "").strip() not in base_labels]
        await read("list", where="batch", items=fresh)
        await click(box, root=ROW_SEL)                   # 取消勾选，不留痕

    async def fill_form(root: str) -> bool:
        """填一张表单。返回「能不能提交」。填不出来的**逐条记明账**。"""
        try:
            fields = await _eval(page, _COLLECT_JS, root) if root else \
                await _eval(page, _COLLECT_JS)
        except Exception:                                # noqa: BLE001
            fields = []
        plan = plan_fill(fields, tag)
        chain["unfillable"].extend(plan["unfillable"])
        if plan["blocked"]:
            note_breakpoint(chain, "form_unfillable",
                            detail="；".join(f"{u['label'] or '(无标签)'}：{u['why']}"
                                             for u in plan["unfillable"][:3])
                                   or "表单里没有能承载前缀的文本框")
            return False
        for f in plan["fills"]:
            target = page.locator(root).locator(f["selector"]) if root \
                else page.locator(f["selector"])
            try:
                if f["kind"] == "select":
                    got = await _eval(page, _PICK_OPTION_JS,
                                      (root + " " + f["selector"]).strip())
                    if got == "ok":
                        continue
                    kind = "no_option" if got == "empty" else "interactive"
                    chain["unfillable"].append(
                        {"label": f["label"], "kind": kind,
                         "why": UNFILLABLE[kind], "required": f["required"]})
                    if f["required"]:
                        note_breakpoint(chain, "form_unfillable",
                                        detail=f"必填下拉「{f['label']}」：{UNFILLABLE[kind]}")
                        return False
                    continue
                await target.first.fill(f["value"], timeout=CLICK_TIMEOUT_MS)
            except Exception:                            # noqa: BLE001
                chain["unfillable"].append(
                    {"label": f["label"], "kind": "fill_failed",
                     "why": UNFILLABLE["fill_failed"], "required": f["required"]})
                if f["required"]:
                    note_breakpoint(chain, "form_unfillable",
                                    detail=f"必填项「{f['label']}」：{UNFILLABLE['fill_failed']}")
                    return False
        return True

    async def submit_form(root: str, *, step: str) -> bool:
        """找到提交按钮点下去，再看服务端答了什么。"""
        try:
            inner = await _eval(page, _COLLECT_JS, root) if root else \
                await _eval(page, _COLLECT_JS)
        except Exception:                                # noqa: BLE001
            inner = []
        btn = pick_control(inner, "submit")
        if btn is None:
            note_breakpoint(chain, "submit_failed", detail="表单上找不到提交按钮")
            return False
        before = len(chain["writes"])
        if not await click(btn, root=root, scope="[表单]"):
            note_breakpoint(chain, "submit_failed", detail="提交按钮点不着")
            return False
        # 提交完页面说了什么 —— **成没成都读**。没成时那句提示就是规则本身
        # （§14.4：「不能为空」是约束、「无权限」是这一步归别人、
        # 「当前状态不允许」是状态机的一条边，三类都不算失败）。
        await read(step, where="layer" if root else "page")
        fresh = chain["writes"][before:]
        if not fresh:
            # 点了、一个请求都没出去 —— **多半是前端校验没过**（我们少填了
            # 用样式类标必填的那些框）。它不是"这个域没有写接口"，
            # 所以要说清是哪一种。
            note_breakpoint(chain, "submit_failed",
                            detail="点了提交但一个请求都没发出去（多半是前端校验没过）")
            return False
        bad = [w for w in fresh if not w["ok"]]
        if bad and not [w for w in fresh if w["ok"]]:
            note_breakpoint(chain, "submit_failed",
                            detail=bad[0].get("error")
                                   or f"{bad[0]['method']} {bad[0]['path']} → {bad[0]['status']}")
            return False
        return True

    gate["open"] = True
    try:
        # 底片：动手之前这一页长什么样。结构那一格靠它和最后一次做差集 ——
        # 差出来的就是**建了一条数据才出现的东西**（§14.1 的「结构」）。
        await read("create", where="page", items=raw_items)

        # ① 新建：点开表单
        label = (create.get("label") or "").strip()
        if not await click(create):
            note_breakpoint(chain, "no_form", detail=f"「{label}」点不着")
            return unlocked
        shape = None
        deadline = time.monotonic() + DIALOG_WAIT_MS / 1000
        while True:
            try:
                shape = await _eval(page, _FIND_LAYER_JS)
            except Exception:                            # noqa: BLE001
                shape = None
            if shape or time.monotonic() >= deadline:
                break
            await page.wait_for_timeout(150)
        jumped = urlsplit(page.url).path != urlsplit(page_url).path
        if not shape and not jumped:
            # 点了「新建」既没弹层也没跳页。**谁的问题分不出来** ——
            # 可能是产品的死按钮，也可能是我们没认出它的层。别默认归给自己。
            note_breakpoint(chain, "no_form",
                            detail="点了「新建」既没弹层也没跳页")
            note_step(chain, "create", ok=False, detail="找不到表单",
                      control=label)
            return unlocked
        form_root = LAYER_SEL if shape else ""
        if jumped:
            unlocked.append(urlsplit(page.url).path)
        # 表单这一层的动作面：主/次按钮都在这儿（「保存」旁边那个「保存并新建」
        # 一直没人数过）。
        try:
            layer_now = await _eval(page, _COLLECT_JS, form_root) if form_root \
                else await _eval(page, _COLLECT_JS)
        except Exception:                                # noqa: BLE001
            layer_now = []
        await read("create", where="layer" if form_root else "page",
                   items=layer_now)

        # ① 新建：填 + 提交
        if not await fill_form(form_root):
            note_step(chain, "create", ok=False, detail="表单填不出来",
                      control=label)
            return unlocked
        if not await submit_form(form_root, step="create"):
            note_step(chain, "create", ok=False, detail="提交没成", control=label)
            return unlocked
        chain["created"] = True
        note_step(chain, "create", ok=True, detail=f"建了 {tag}", control=label)

        # ② 列表：找自己那一行
        scope = await back_to_list()
        note_step(chain, "list", ok=bool(scope.get("found")))
        if not scope.get("found"):
            # **这本身就是一条发现**：列表没刷新 / 分页在后面 / 需要审批才可见。
            # 不是"建失败了" —— 写请求是 2xx，服务端认了。
            note_breakpoint(chain, "row_not_found",
                            detail="写请求成功了，但列表里搜不到这个名字："
                                   "列表没刷新 / 它在后面的分页 / 要审批后才可见")
            return unlocked
        if not scope.get("scoped"):
            note_breakpoint(chain, "row_unscoped",
                            detail="名字在页面上，但认不出它属于哪一行 —— "
                                   "**这时一个写按钮都不许点**（点了可能删的是别人那条）")
            return unlocked

        # ③ 详情：这一步才第一次解锁子页面
        try:
            row_items = await _eval(page, _COLLECT_JS, ROW_SEL)
        except Exception:                                # noqa: BLE001
            row_items = []
        # 行内那一层的动作面 + 我们那一行现在是什么状态。
        await read("list", where="row", items=row_items)
        await probe_batch(row_items)
        det = pick_control(row_items, "detail")
        if det is None:
            note_fact(chain, "no_detail_entry",
                      detail=CHAIN_FACTS["no_detail_entry"]["why"])
            note_step(chain, "detail", ok=False, detail="行上没有详情入口")
        else:
            if await click(det, root=ROW_SEL):
                path = urlsplit(page.url).path
                ok = path != urlsplit(page_url).path
                if ok:
                    unlocked.append(path)
                note_step(chain, "detail", ok=ok,
                          detail=f"进了 {path}" if ok else "点了详情但没换页",
                          control=(det.get("label") or "").strip())
                # 详情页那一层：页签、页签里的按钮、审批/日志/关联那几块区块。
                # **这一层不建数据就进不来**（§15.2），所以它一直是空的。
                try:
                    det_items = await _eval(page, _COLLECT_JS) or []
                except Exception:                        # noqa: BLE001
                    det_items = []
                await read("detail", where="detail", items=det_items)
                tabs = [r for r in det_items
                        if (r.get("role") or "").lower() == "tab"]
                # 页签**探过**这件事要记账（哪怕这个产品没有页签）——
                # 没探和探到 0 个是两回事。
                await read("detail", where="tab", items=tabs)
            else:
                note_step(chain, "detail", ok=False, detail="详情点不着",
                          control=(det.get("label") or "").strip())

        # ④ 编辑
        scope = await back_to_list()
        try:
            row_items = await _eval(page, _COLLECT_JS, ROW_SEL) \
                if scope.get("scoped") else []
        except Exception:                                # noqa: BLE001
            row_items = []
        edt = pick_control(row_items, "edit")
        if edt is None:
            # **记成事实，不当缺口**（§12.3）。链继续走 —— 不继续的话
            # 我们造的那一条就删不掉了。
            note_fact(chain, "no_edit_entry",
                      detail=CHAIN_FACTS["no_edit_entry"]["why"])
            note_step(chain, "edit", ok=False, detail="行上没有编辑入口")
        elif await click(edt, root=ROW_SEL):
            try:
                shape = await _eval(page, _FIND_LAYER_JS)
            except Exception:                            # noqa: BLE001
                shape = None
            eroot = LAYER_SEL if shape else ""
            try:
                efields = await _eval(page, _COLLECT_JS, eroot) if eroot else \
                    await _eval(page, _COLLECT_JS)
            except Exception:                            # noqa: BLE001
                efields = []
            eplan = plan_fill(efields, tag)
            done = False
            for f in eplan["fills"]:
                if f["kind"] != "text":
                    continue
                target = page.locator(eroot).locator(f["selector"]) if eroot \
                    else page.locator(f["selector"])
                try:
                    # 改一个字，但**改完还得认得出来是自己的**（`edit_value`）。
                    await target.first.fill(edit_value(tag),
                                            timeout=CLICK_TIMEOUT_MS)
                    done = True
                    break
                except Exception:                        # noqa: BLE001
                    continue
            if done and await submit_form(eroot, step="edit"):
                note_step(chain, "edit", ok=True, detail=f"改成了 {edit_value(tag)}",
                          control=(edt.get("label") or "").strip())
            else:
                note_step(chain, "edit", ok=False,
                          detail="改不动（没有可改的文本框，或提交没成）",
                          control=(edt.get("label") or "").strip())
        else:
            note_step(chain, "edit", ok=False, detail="编辑点不着",
                      control=(edt.get("label") or "").strip())

        # ⑤ 回列表确认
        scope = await back_to_list()
        # 改完之后我们那一行的状态、和列表上一共有几种状态（§14.3 是**数**出来的）。
        await read("verify", where="row")
        note_step(chain, "verify", ok=bool(scope.get("scoped")),
                  detail="回列表还能找到自己那一行" if scope.get("scoped")
                         else "回列表圈不出自己那一行了")
        if not scope.get("scoped"):
            note_breakpoint(chain, "row_unscoped",
                            detail="改完之后圈不出自己那一行 —— 不敢往下删")
            return unlocked

        # ⑥ 删除 + ⑦ 确认没了
        try:
            row_items = await _eval(page, _COLLECT_JS, ROW_SEL)
        except Exception:                                # noqa: BLE001
            row_items = []
        dele = pick_control(row_items, "delete")
        if dele is None:
            note_fact(chain, "no_delete_entry",
                      detail=CHAIN_FACTS["no_delete_entry"]["why"])
            note_step(chain, "delete", ok=False, detail="行上没有删除入口")
            return unlocked
        chain["deleteTried"] = True
        if not await click(dele, root=ROW_SEL):
            note_step(chain, "delete", ok=False, detail="删除点不着",
                      control=(dele.get("label") or "").strip())
            return unlocked
        # 二次确认框。**这是唯一允许点「确认删除」的地方** ——
        # 前提是我们自己刚点的删除、删的是自己那一行。
        try:
            shape = await _eval(page, _FIND_LAYER_JS)
        except Exception:                                # noqa: BLE001
            shape = None
        if shape:
            try:
                layer_items = await _eval(page, _COLLECT_JS, LAYER_SEL)
            except Exception:                            # noqa: BLE001
                layer_items = []
            ok_btn = pick_control(layer_items, "confirm")
            # 二次确认层里还有什么（「同时删除关联数据」这类勾选项就在这儿）。
            await read("delete", where="layer", items=layer_items)
            if ok_btn is not None:
                await click(ok_btn, root=LAYER_SEL, scope="[确认]")
        note_step(chain, "delete", ok=True,
                  control=(dele.get("label") or "").strip())
        scope = await back_to_list()
        # 删完页面说了什么。「该数据已被引用，不能删除」这一句就是一条业务规则，
        # 落成"删除失败"就把它丢了。
        await read("confirm", where="page")
        chain["deleted"] = not scope.get("found")
        note_step(chain, "confirm", ok=chain["deleted"],
                  detail="列表里已经没有了" if chain["deleted"] else "还在")
        if not chain["deleted"]:
            # **最值钱的那种发现**：自己造的数据删不掉。
            # 同时它现在是被测环境里的残留 —— 两件事都要说。
            note_breakpoint(chain, "delete_failed",
                            detail="点了删除并确认，回列表这条还在 —— "
                                   "自己造的数据删不掉（产品缺陷），"
                                   "而且它现在是环境残留")
    except Exception as e:                               # noqa: BLE001
        # 链自己崩了**不许拖垮这一页**（更不许拖垮整趟）。但要记数：
        # 崩在中途十有八九留了残留，而 `finish_chain` 正是靠 created/deleted
        # 把它算出来的。
        ledger.setdefault("chainCrashed", []).append(
            {"page": page_path, "error": type(e).__name__})
    finally:
        gate["open"] = False
        win["endedAt"] = _now()
        try:
            page.remove_listener("response", _on_resp)
        except Exception:                                # noqa: BLE001
            pass
        try:
            await flush()
        except Exception:                                # noqa: BLE001
            pass
        finish_chain(chain)
        chain["unlockedPaths"] = list(unlocked)
    return unlocked


# ── 选择器活体命中 ────────────────────────────────────────────────────────

async def _probe_selectors(page, path: str, payload, ledger: dict) -> None:
    """在这一页上数一遍 QA 仓那张表的命中数。**只读，一个 DOM 都不动。**

    失败**记账不抛**，跟这一页的其它步骤一个待遇：探测挂了只是少一页的命中数，
    而抛出去会把整页（连带它的控件账和时窗）一起废掉 —— 那是拿一个附加产出
    换掉主产出。

    **不区分角色。** 命中是并集：任何一个角色在任何一页上看见过，就算"真实渲染里
    存在"。角色维度那件事（谁看得见）有 `qa_role_visibility` 专门管，
    在这里再分一次只会给出第二份口径不同的角色可见性数据。
    """
    if not payload:
        return
    try:
        res = await _eval(page, PROBE_JS, payload)
    except Exception as e:                               # noqa: BLE001
        ledger.setdefault("selectorProbeFailed", []).append(
            {"path": path, "error": type(e).__name__})
        return
    merge_probe(ledger.setdefault("selectorProbe", {}), path, res)


# ── 一个角色的一趟 ────────────────────────────────────────────────────────

def _login_hint(stage: str, env_vars=None) -> str:
    """登录挂了该去改什么。**按哪一步挂的判，不按"环境里配没配某个键"判。**

    这两种分诊法在 `stage=fill` 上会给出**相反**的结论。只看变量的那种会说
    「你把登录接口当页面路径配了，去补 `LOGIN_PATH`」—— 可 `goto` 已经过了，
    路径本来就是通的，真正挂的是三个输入框的选择器。

    实测（2026-09-04，UAG `192.168.51.138:3000`）：默认路径 `/login` **恰好是对的**
    （`goto` 拿到 200），而默认选择器 `input[name=username]` 在那套前端上
    **一个都命中不到** —— 它用的是 `input[autocomplete="username"]`。
    上一版就据此把 7 个角色的失败全归到了「缺 `LOGIN_PATH`」上。
    **指错方向的诊断比不给诊断更贵**：人会照着去改一个本来就对的配置，
    改完照样红，然后开始怀疑别的地方。
    """
    if stage == "goto":
        if not _cfg(env_vars, "LOGIN_PATH") and _cfg(env_vars, "LOGIN_URL"):
            return ("打不开登录页，而环境里只有 `LOGIN_URL`（那是登录**接口**，"
                    "接口场景用的）—— 浏览器登录要的是**页面路径** `LOGIN_PATH`。")
        return "打不开登录页：确认 `BASE_URL` + `LOGIN_PATH` 指到的是前端页面。"
    if stage in ("fill", "submit"):
        return ("登录页打开了（路径没问题），是控件没找到 —— 改 "
                "`LOGIN_USER_SELECTOR` / `LOGIN_PASS_SELECTOR` / "
                "`LOGIN_SUBMIT_SELECTOR`。默认值按 `name=` 猜，"
                "而不少前端用的是 `autocomplete=`。")
    if stage == "settle":
        return ("表单交上去了，但**登录框没消失** —— 会话没建起来。"
                "凭据被拒 / 有二次验证 / 点到的不是登录按钮：拿 "
                "`<ROLE>_USERNAME` + `_PASSWORD` 直接打一次 `LOGIN_URL`，"
                "看接口认不认（认了就是前端这一步的问题，不是账号的问题）。")
    # 落到这儿说明加了新步骤却没给提示 —— 那是个 bug，别拿一句通用话糊过去。
    return f"登录挂在 `{stage}`，而这一步还没登记过对应的排查方向。"


async def _login(page, base_url: str, role: str, ledger: dict,
                 env_vars=None) -> bool:
    """登录。**唯一默认放行的写请求**（`qa_survey_guard.DEFAULT_WRITE_ALLOWLIST`）。

    放行次数记账：它是账本项不是免检项。

    登录崩了要**在账本上说清是登录崩的**（`loginFailed`）再往上抛。不记的话
    `run_survey` 那边收到的只有一个 `type(e).__name__` —— 于是「登录表单的选择器
    对不上」和「那台机器打不开」在报告上都是一行 `TimeoutError`，而这两件事
    一个改配置、一个找运维。抛出去这件事本身不改：登录不成这个角色什么都没看到，
    那个分片必须算失败，不能带着一份空 items 混成 `done`。
    """
    user, pwd = _role_credentials(role, env_vars)
    if not user or not pwd:
        ledger.setdefault("rolesSkipped", []).append(role)
        return False
    # 登录页也是一格时窗，否则登录那几个请求会整个落进 `edgesUnwindowed`。
    # **但它 `tail: False`（不许延长到下一次导航）**：提交之后浏览器会自己跳到
    # 落地页，延长就把落地页的流量记到 `/login` 名下了 —— 那种错归属在报告上
    # 和对的长得一样。中间这段宁可记进"归不了页"，也不归错页。
    #
    # ⚠ 这里要的是**页面路径**，不是登录接口。环境里常见的是
    # `LOGIN_URL=/api/auth/login`（接口场景用的那个），拿它去 goto 会打开一段
    # JSON，然后卡在"找不到用户名输入框"——报出来是选择器的错，实际是配错了键。
    # 所以只认 `LOGIN_PATH`；配错了长什么样，由 `_login_hint` 按**哪一步挂的**判。
    login_path = _cfg(env_vars, "LOGIN_PATH", "login")
    win = {"path": "/" + login_path.lstrip("/"), "startedAt": _now(), "tail": False}
    ledger.setdefault("pageWindows", {}).setdefault(role, []).append(win)
    stage = "goto"
    try:
        await page.goto(urljoin(base_url + "/", login_path.lstrip("/")),
                        timeout=PAGE_TIMEOUT_MS)
        stage = "fill"
        await page.fill(_cfg(env_vars, "LOGIN_USER_SELECTOR", "input[name=username]"), user)
        await page.fill(_cfg(env_vars, "LOGIN_PASS_SELECTOR", "input[name=password]"), pwd)
        stage = "submit"
        await page.click(_cfg(env_vars, "LOGIN_SUBMIT_SELECTOR", "button[type=submit]"))
        # ⚠ 这一步**不能**用 `wait_for_load_state("networkidle")`。
        # 登录是一发 XHR，页面根本不导航 —— 而那个 API 只要**当前**已经是
        # networkidle 就立刻返回，它等的是"页面加载"，不是"我刚点的这一下"。
        #
        # 实测（2026-09-04，UAG `192.168.51.138:3000`）：它秒回，紧接着
        # `crawl_role` 的第一个 `goto` 把还在飞的 `POST /api/auth/login` 掐了
        # （HAR 里那条边没有响应状态）。于是 7 个角色全都带着一个**没建起来的
        # 会话**往下爬，**一个异常都没抛**：
        #   · `shardsOk 7/7`、`loginCount 7`、181 页、232 条 P 边 —— 全是绿的
        #   · 而 29 个页面渲染的其实都是登录页：232 个控件只有 7 种标签
        #     （`Sign In` / `Forgot password?` / `Show password` …）
        #   · 557 条选择器只命中 4 条 —— 就是登录框自己那 4 条
        #   · 232 条 P 边里 82 条是 401
        # **一份完整的假绿**，而且报告里没有任何一格在说这件事。
        #
        # 改成**等登录框消失**：它一个信号同时管两件事 —— XHR 回来了、
        # 而且会话真的建起来了。等不到就是没登上，按登录失败抛
        # （`_login` 的纪律：登不上的角色什么都没看到，那个分片必须算失败，
        # 不能带着一份登录页的 items 混成 `done`）。
        stage = "settle"
        await page.wait_for_selector(
            _cfg(env_vars, "LOGIN_PASS_SELECTOR", "input[name=password]"),
            state="hidden", timeout=PAGE_TIMEOUT_MS)
    except Exception as e:                               # noqa: BLE001
        ledger.setdefault("loginFailed", []).append({
            "role": role, "stage": stage, "error": type(e).__name__,
            "loginPath": "/" + login_path.lstrip("/"),
            "usedDefaultPath": not _cfg(env_vars, "LOGIN_PATH"),
            "hint": _login_hint(stage, env_vars),
        })
        raise
    ledger["loginCount"] = ledger.get("loginCount", 0) + 1
    win["endedAt"] = _now()
    return True


async def crawl_role(browser, base_url: str, role: str, page_paths: list[str],
                     ledger: dict, har_dir: Path,
                     selector_probe: list[dict] | None = None,
                     env_vars=None, run_chains: bool = False,
                     chains: list | None = None) -> list[dict]:
    """爬一个角色。返回账本行；**一页失败不拖垮整趟**，只记数。

    `selector_probe` 是 QA 仓那张公共选择器表（`qa_selectors.probe_payload`
    给的清单）。传了就在每一页上**只读地**数一遍命中，账本落
    `ledger["selectorProbe"]`。判档在 `qa_selectors.roll_up`，这里一个判断都不做。

    `run_chains` 打开**有向链路**（`_run_chain`）：造一条自己前缀的数据、
    在它身上把建→详情→编辑→删走完。**只给主爬角色开**（`run_survey` 传
    `role == main_role`）—— 七个角色各造一条，被测环境里就是七份垃圾，
    而多出来的六份一条新信息都不带（同一个域、同一个表单）。
    """
    har_path = har_dir / f"{role}.har"
    context = await browser.new_context(record_har_path=str(har_path),
                                        record_har_content="omit")
    # 有向链路要往外发写请求，就得在 L1 那道网上开一道**受控的窗**。
    # 闸默认关着（`{"open": False}`）—— 无向枚举那一半的 fail-closed 一个字没改；
    # 只有 `_run_chain` 在自己的 `try/finally` 里开合它，窗上的每个写请求
    # 单独计数（`directedWrites`），账对不上时能立刻看出来。
    chain_gate = {"open": False}
    await context.route("**/*",
                        make_readonly_guard(ledger,
                                            gate=lambda: chain_gate["open"]))
    items: list[dict] = []
    try:
        page = await context.new_page()
        if not await _login(page, base_url, role, ledger, env_vars):
            return items
        # 这个角色**真正走到**的页面，一页一记。矩阵那边靠它把「没探到」
        # 和「看不见」分开 —— 只有 `pagesVisited` 那个总数的话，
        # 一个浅扫角色在第 41 页什么都没看见，会被算成「它被禁掉了」。
        probed = ledger.setdefault("pagesProbed", {}).setdefault(role, [])
        # P 边的锚：HAR 里没有「这条请求属于哪次导航」这种字段，只能靠时间。
        # 时窗记在账本上（而不是当返回值），一是 `pagesProbed` 已经是这个先例，
        # 二是归页这件事**要能复查** —— 边归错了页的时候，得看得出当时的边界。
        windows = ledger.setdefault("pageWindows", {}).setdefault(role, [])
        # 控件级的同一套。**和页面级分开两本账**：一条请求要么属于某次点击、
        # 要么属于某次导航，摊到两边去会让"这一页自己会调什么"再也问不出来。
        clicks = ledger.setdefault("clickWindows", {}).setdefault(role, [])
        # 页面清单是**一条队列**，不是一个 for：菜单里发现的页要能接到后面去。
        # 清单里那些（QA 仓声明的）永远排在前面且一页不少 —— 发现来的排在后面、
        # 受预算约束，**两者的账要分得开**，不然"他没声明这一页"和
        # "我们多爬了一页"会混成同一条。
        queue = list(page_paths)
        planned = set(queue)
        seen_tmpl = {route_template(p) for p in queue} | planned
        extras = 0
        idx = 0
        dialog_budget = DIALOG_PROBE_BUDGET
        seen_openers: set = set()   # 这一趟已经探过的开层按钮（角色内去重）
        while idx < len(queue):
            path = queue[idx]
            idx += 1
            # 记账用的路径：清单里那些**原样**（换写法会让整批 item 的 key 变，
            # diff 立刻报一片"功能没了"），菜单发现来的用模板（详情页的 id
            # 每趟都不一样，拿具体路径记账等于每趟全是新增）。
            record = path if path in planned else route_template(path)
            # 先记 startedAt 再 goto：反了的话 goto 期间的请求就落在窗外了。
            # **失败的页也记一格** —— 那一页确实发过请求（它们属于它），
            # 而且这一格还兼作上一页的右边界：不记的话上一页会一路延长过来，
            # 把这一页的流量吃进自己名下。
            win = {"path": record, "startedAt": _now()}
            windows.append(win)
            page_url = urljoin(base_url + "/", path.lstrip("/"))
            try:
                await page.goto(page_url, timeout=PAGE_TIMEOUT_MS)
                await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
                raw = await _eval(page, _COLLECT_JS)
                # `title()` 原来在 try 外面 —— 它一抛就会连带废掉**整个分片**
                # （后面所有页一页不剩）。一页的标题取不到，代价该和取不到控件一样。
                title = await page.title()
            except Exception as e:                       # noqa: BLE001
                # **记账，不抛。** 这一页的 item 在 diff 里会被降级成 unknown
                # （`qa_page_survey.diff_items`），绝不进 `removed` ——「没走到」和
                # 「功能没了」在产物上长得一模一样，混过去就会凭空报出一批不存在的缺口。
                # 记**结构化的一条**，不拼成 `f"{path}: {err}"`：拼了下游就得反解析，
                # 而路径里本来就可能带 ": "，解析一歪那一页就不算失败页，
                # 它的 item 立刻变成 `removed` —— 正是这条规则要防的那个假缺口。
                ledger.setdefault("pagesFailed", []).append(
                    {"path": record, "error": type(e).__name__})
                win["endedAt"] = _now()
                continue
            if not raw:
                ledger.setdefault("pagesEmptyState", []).append(record)
            page_rows = collect_items(record, title, raw, ledger)
            items.extend(page_rows)
            await _probe_selectors(page, path, selector_probe, ledger)
            # ③ 弹层：写按钮背后的表单在这里才第一次被看见。
            if dialog_budget > 0:
                inner, marks, used, jumped = await _probe_dialogs(
                    page, page_path=record, page_title=title, page_url=page_url,
                    raw_items=raw, ledger=ledger, budget=dialog_budget,
                    seen_openers=seen_openers, clicks=clicks)
                dialog_budget -= used
                items.extend(inner)
                # 把「点过 / 有反应」回填到**这一页自己那几行**上。
                # 不回填的话，刚点开过的按钮照旧是 `clicked=False`，
                # G4 那边永远看不到这一趟真点过东西。
                for row in page_rows:
                    if row["anchor"] in marks:
                        row["clicked"] = True
                        row["effect"] = marks[row["anchor"]]
            else:
                jumped = []
            # ① 菜单树：让页面**自己说**它还能去哪儿。
            try:
                menu = await _eval(page, _MENU_JS)
            except Exception:                            # noqa: BLE001
                menu = None
            # 形状不对**也算没扫成**（页面塞了个别的东西回来、脚本被 CSP 掐了）。
            # 当成"这一页没有菜单"会安静地少爬一片，而少爬的表现是
            # 「这个域就这么几页」—— 和真的只有几页分不开。
            if not isinstance(menu, dict):
                ledger.setdefault("menuScanFailed", []).append(record)
                menu = {}
            # **页面级时窗到此为止。** 有向链路那几下写请求有自己的窗
            # （`kind: "directed"`），落进这一格就成了一句错话：
            # 「打开这一页会自动 POST」—— 而那句话接下来会被拿去和对方的
            # 脚本比，凭空报出一批"他没测的写接口"。
            win["endedAt"] = _now()
            # ④ 有向链路：造一条自己的数据，在它身上把这个域走完。
            # **每页最多一条链一次新建**（§12.4），整趟受 `CHAIN_BUDGET` 约束。
            chain_paths: list[str] = []
            if run_chains and chains is not None and len(chains) < CHAIN_BUDGET:
                chain_paths = await _run_chain(
                    page, page_path=record, page_url=page_url, raw_items=raw,
                    ledger=ledger, chains=chains, clicks=clicks,
                    windows=windows, gate=chain_gate)
            elif run_chains and chains is not None:
                # **预算用完不是"这一页没有写操作"。** 记数，不然报告上
                # 「这个域只有这几个写入口」和「还有 20 页没去建过」长得一样。
                ledger["chainBudgetCapped"] = ledger.get("chainBudgetCapped", 0) + 1
            # 菜单里读到的 + 点「新建」跳出来的 + 建完才解锁的，走**同一条**
            # 队列和预算：三者都是"页面自己说它还能去哪儿"，分开只会让预算算几遍。
            for found in list(menu.get("paths") or []) + jumped + chain_paths:
                tmpl = route_template(found)
                if tmpl in seen_tmpl or found in seen_tmpl:
                    continue
                seen_tmpl.add(tmpl)
                ledger.setdefault("menuDiscovered", []).append(tmpl)
                if extras < MENU_EXTRA_MAX_PAGES:
                    queue.append(found)
                    extras += 1
                else:
                    # **预算用完不是"没发现"。** 记数，否则报告上
                    # 「这个域只有这些页」和「还有 30 页没去看」长得一样。
                    ledger["menuExtraCapped"] = ledger.get("menuExtraCapped", 0) + 1
            ledger["pagesVisited"] = ledger.get("pagesVisited", 0) + 1
            # 走到了就记，**哪怕这一页一个控件都没有** —— 空页恰恰是
            # 「探过了，确实看不见」，那是可比的格子，不是未探测。
            probed.append(record)
        if dialog_budget <= 0:
            ledger.setdefault("dialogBudgetExhausted", []).append(role)
    finally:
        # HAR 只在 close 时落盘 —— 不 close 就是一个空文件。
        # **也得上闸**：渲染进程还在转 JS 的时候 close 一样会等下去，
        # 而这里是 `finally`，挂在这儿连"这一片失败了"都报不出来。
        try:
            await asyncio.wait_for(context.close(),
                                   timeout=CONTEXT_CLOSE_TIMEOUT_MS / 1000)
        except (TimeoutError, asyncio.TimeoutError):
            # HAR 多半是残的或空的 —— 记一格，别让它冒充"这个角色没流量"。
            ledger.setdefault("contextCloseTimedOut", []).append(role)
        # 最后一格的右边界。少了它，最后一页的尾巴无处可延，那一页
        # `networkidle` 之后的轮询会全部记进「归不了页」。
        ledger.setdefault("contextClosedAt", {})[role] = _now()
    # 同页撞 key 的（表格每行同一个 testid）合成一行 —— 见 `dedupe_items`。
    # 放在**最后**做：前面那些 `page_rows` 的回填还得按原样一行一行改。
    return dedupe_items(items, ledger)


def sanitize_har(har_path: Path) -> dict | None:
    """读 HAR、**扔掉凭证**、返回可落库的那份。原始文件随沙箱目录一起消失。

    HAR 里的 `Authorization` 是**完整可用凭证**，不是"敏感字段"。
    落库前必须 drop 而不是脱敏，理由和实测在 `qa_survey_guard.drop_credentials`。
    """
    if not har_path.exists():
        return None
    try:
        raw = json.loads(har_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return drop_credentials(raw)


# ── 编排 ─────────────────────────────────────────────────────────────────

async def run_survey(*, base_url: str | None = None, roles: list[str],
                     page_paths: list[str], routes=None,
                     totals_probe=None, selector_probe=None,
                     env_vars=None) -> dict:
    """跑完一趟，返回 `{status, ledger, items, har, page_edges}`。

    分片：主爬角色深爬全部页面，其余角色**浅扫**（角色维度只问「看得见什么」）。
    并发 `MAX_PARALLEL_SHARDS`，对方是测试环境不是压测靶子。

    终态由 `resolve_terminal_status` 定，**`dirty` 压过 `failed`**：
    一趟全片失败但环境里的数变了，要看的是"我们动了什么"。

    P 边有**两个粒度，两本账，绝不互相摊派**（归属规则都在 `qa_page_traffic`）：

    · `page_edges` = **页面级**（打开这一页浏览器发了什么），靠导航时窗归页。
      它**不写进 item 的 `endpoints`** —— 摊到这一页每个控件头上等于凭空造
      一条 `observed` 的控件→端点边。
    · item 上的 `endpoints` = **控件级**（点这个按钮发出了什么），靠点击时窗归属，
      2026-09-04 补上（此前那一列建了、一行没写过）。**三态不许合**：
      有边 / 点了没边（`[]`）/ 没点过（NULL）。账在 `ledger["controlTraffic"]`。

    `routes` 只用来推 API 前缀兜底分类（拿不到就只靠 `_resourceType`，
    会在 declarations 里说明）。

    `env_vars` 是**这一趟**的配置（`BASE_URL` / `LOGIN_*` / `<ROLE>_USERNAME` …），
    由接口层从项目环境合出来直接传进来。**不走 `os.environ`**，理由在 `_cfg`：
    进程环境只有一份，两个项目同时爬会互相顶掉 `BASE_URL`。传空则退回进程环境
    （手动跑一趟用）。它带着真凭证，**只在进程内传，一个字节都不落库** ——
    survey 上存的是角色**名**。

    `selector_probe` 传了就顺路验一遍 QA 仓那张公共选择器表在真实渲染里指到东西
    没有（`qa_selectors`）。**账本里必须能看出这一趟到底探没探** ——
    `selectorProbe` 这个键在时说明探了（`pages` 是探过的页），不在时就是没探；
    `roll_up(probed=...)` 靠它把「探了、都没见到」和「压根没探」分开，
    混起来会让人去查 400 多条不存在的过期选择器。
    """
    from playwright.async_api import async_playwright

    base_url = (base_url or _base_url(env_vars)).rstrip("/")
    main_role = pick_main_crawl_role(roles)          # 没有只读账号 → 这里就不许开爬
    others = shallow_scan_roles(roles)
    ledger: dict = {"writesBlocked": 0, "pagesVisited": 0, "controlsUnknown": 0,
                    "loginCount": 0, "rolesShallow": others,
                    # **点过几个控件。0 也要明写出来**，它是 G4 那张表为什么
                    # 空的唯一解释（`compute_gaps(controls_clicked=...)`，
                    # 键名和那个参数是一对，别单改一边）。
                    # 这一版起它不再恒为 0：开层按钮会被点开一次（`_probe_dialogs`），
                    # **但也只有那一档** —— 删除/退出那些一个都不点。
                    # ⚠ 与之配套的是每一行 item 上的 `clicked`：run 级这个数
                    # 一旦 > 0，没有 `clicked` 键的行会**全部**被当成点过，
                    # 1200 多个没碰过的控件会一起变成假的 G4。
                    "controlsClicked": 0,
                    # 下面这几格都是「0 也要渲染」：没弹层和没去探在报告上
                    # 长得一模一样，而前者是结论、后者是欠账。
                    "dialogsOpened": 0, "dialogsNoEffect": 0, "fieldsSeen": 0,
                    # 「点了跳走的」和「认不出锚点的」同理：只在发生时 +1 的话，
                    # 一次都没发生的那一趟这两格根本不存在，页面上渲染成
                    # 「没记过」——而它们的真值是一个有意义的 0。
                    "dialogsNavigated": 0, "controlsAnchorless": 0,
                    # 层是靠标准属性（`role=dialog`/`aria-modal`）认出来的，
                    # 还是靠几何兜底认出来的。**两格都得先摆在这儿** ——
                    # 只在认出来的时候 `setdefault`，那么"一个层都没开"这一趟
                    # 连这两个格子都不存在，页面上只能什么都不显示，
                    # 于是「产品没有弹层」和「我们的判据认不出它的弹层」
                    # 又长回一模一样。2026-09-04 踩的就是这个坑（判据照 antd 写，
                    # 换个 UI 库整维恒 0，报告上一点痕迹都没有）。
                    "layersBy": {"role": 0, "geometry": 0},
                    # 这一趟拿了几条选择器去探。**0 也要写出来** —— 清单是空的
                    # （QA 仓没拉到 / 解析全军覆没）和"探了但一条都没命中"在报告上
                    # 长得一模一样，而前者是我们自己没跑成，不是他的选择器有问题。
                    "selectorsProbed": len(selector_probe or []),
                    # 有向链路在 L1 那道网上**放过**了几个写请求。
                    # 0 也要摆出来：它和 `writesBlocked` 是一对账 ——
                    # 「这一趟一个写请求都没放过」和「这一格压根没记」
                    # 在页面上长得一样，而前者说明链一条都没跑起来。
                    "directedWrites": 0}

    ledger["selfCheck"] = self_check_label(totals_probe)
    totals_before = await totals_probe() if totals_probe else None

    shards = [(main_role, page_paths)] + [(r, page_paths[:SHALLOW_MAX_PAGES]) for r in others]
    if len(page_paths) > SHALLOW_MAX_PAGES and others:
        # 这道闸**从来没响过**（计划一直不到 40 页），于是"保护"和"没触发过的
        # 常量"长得一样。真截断的时候留一行：浅扫角色在第 41 页看不见任何东西
        # 是**必然**的，不写出来会被读成「这个角色被禁掉了」。
        ledger["rolePagesCapped"] = {"cap": SHALLOW_MAX_PAGES,
                                     "planned": len(page_paths),
                                     "roles": list(others)}
    ledger["shardsTotal"] = len(shards)
    # 有向链路的账。**只有主爬角色往里写**（`run_chains`），所以不按角色分本 ——
    # 分了会让"这一格是空的"读起来像"这个角色没跑链"，而真相是它压根没资格跑。
    chains: list[dict] = []
    shard_rows: list[dict] = []
    hars: dict[str, dict] = {}
    buckets: list[dict] = []
    click_buckets: list[dict] = []
    api_prefixes = api_prefixes_from_routes(routes)
    ok = 0

    with tempfile.TemporaryDirectory(prefix="qa-survey-") as tmp:
        har_dir = Path(tmp)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            sem = asyncio.Semaphore(MAX_PARALLEL_SHARDS)

            async def _one(role: str, paths: list[str]):
                async with sem:
                    return role, await crawl_role(
                        browser, base_url, role, paths, ledger, har_dir,
                        selector_probe, env_vars,
                        # **只主爬角色造数据。** 七个角色各造一条，环境里就是
                        # 七份垃圾，而多出来的六份一条新信息都不带
                        # （同一个域、同一张表单）。
                        run_chains=(role == main_role), chains=chains)

            results = await asyncio.gather(
                *(_one(r, p) for r, p in shards), return_exceptions=True)
            await browser.close()

        # `gather` 保序，所以第 i 个结果就是第 i 个分片 —— 崩掉那个的角色只能
        # 从这里对回去（异常里没有角色）。**记角色不是为了好看**：主爬角色崩了
        # 这一趟等于什么都没看到，浅扫角色崩了只是少一列角色可见性，
        # 而只记一个 `TimeoutError` 的话这两件事在报告上一模一样。
        for (shard_role, _paths), res in zip(shards, results, strict=True):
            if isinstance(res, BaseException):
                ledger.setdefault("shardsFailed", []).append({
                    "role": shard_role, "error": type(res).__name__,
                    "isMainRole": shard_role == main_role})
                continue
            role, rows = res
            ok += 1
            # 并集在 `qa_role_visibility.merge_shards` 里，这里只收集分片。
            # 原先这段是「拿主爬那份当底、浅扫只做标注」—— 那样
            # **只有浅扫角色看得见的控件整个消失**，而「低权角色看得见、
            # 主爬这个只读账号看不见」正是角色维度唯一有价值的信号。
            shard_rows.append({"role": role, "items": rows})
            har = sanitize_har(har_dir / f"{role}.har")
            if har is not None:
                hars[role] = har
            # 归页就在这儿做完：HAR 本身**不落库**（它是凭证的原产地，
            # `sanitize_har` 之后也只是"扔干净了"，不是"该存"），
            # 沙箱目录一出 `with` 就没了。边和账要在这之前拿出来。
            buckets.append(bucket_entries(
                har, (ledger.get("pageWindows") or {}).get(role) or [],
                role=role,
                closed_at=(ledger.get("contextClosedAt") or {}).get(role),
                api_prefixes=api_prefixes))
            # 同一份 HAR 再走一遍控件级的时窗。**不传 `closed_at`** ——
            # 控件级一律不延长尾巴（延长会把关层/重载算成点击发的）。
            click_buckets.append(bucket_clicks(
                har, (ledger.get("clickWindows") or {}).get(role) or [],
                role=role, api_prefixes=api_prefixes))

    items = merge_shards(shard_rows, main_role=main_role)
    traffic = merge_edges(buckets)
    ledger["traffic"] = {k: v for k, v in traffic.items() if k != "edges"}
    # 控件级的边不单独返回，直接挂到 item 的 `endpoints` 上 —— 那一列就是它的家。
    control = merge_control_edges(click_buckets)
    ledger["controlTraffic"] = {k: v for k, v in control.items()
                                if k not in ("edges", "attempted")}
    ledger["controlTraffic"].setdefault("counters", {}).update(
        attach_control_edges(items, control["edges"], control["attempted"]))

    # 有向那一维的账：链本身 + 计数 + **没验到什么的声明** + 残留清单。
    # 声明和残留分开两格：前者是"这一维量到哪儿了"，后者是"我们在别人环境里
    # 留下了什么" —— 后一件必须能被单独找出来清掉，别混在声明里等人读。
    chain_summary = summarize_chains(chains)
    ledger["directed"] = {"chains": chains, "counters": chain_summary,
                          "declarations": chain_declarations(
                              chain_summary,
                              create_disabled=len(
                                  ledger.get("chainCreateDisabled") or []),
                              main_role=main_role),
                          "residue": residue_findings(chains),
                          # 「新建」是灰的那几页跟着账本走：一条链都没开时，
                          # 这份名单就是**唯一**能解释"为什么没开"的东西。
                          "createDisabled": list(
                              ledger.get("chainCreateDisabled") or []),
                          # 名字表跟着账本走（见 `chain_meta` 的注）
                          "meta": chain_meta()}
    # §14.5 + §15：功能地图 + 广度/深度两个数。**这里不带脚本那一半** ——
    # 爬取侧手上没有 Q 边，`pair_actions` 会自己声明 `paired: False`。
    # 两边一拼在 `qa_live_survey.reconcile` 里补（那儿 `q` 在作用域内）。
    ledger["domainMap"] = summarize_maps(chains)
    ledger["domainMap"]["meta"] = map_meta()

    totals_after = await totals_probe() if totals_probe else None
    status = resolve_terminal_status(shards_total=len(shards), shards_ok=ok,
                                     totals_before=totals_before,
                                     totals_after=totals_after)
    status = degrade_for_gaps(status, ledger)
    ledger["shardsOk"] = ok
    ledger["safeToClick"] = list(SAFE_TO_CLICK)
    ledger["mainRole"] = MAIN_CRAWL_ROLE
    return {"status": status, "ledger": ledger, "items": items, "har": hars,
            "page_edges": traffic["edges"]}
