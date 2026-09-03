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
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from app.services.qa_page_traffic import (
    api_prefixes_from_routes,
    bucket_entries,
    merge_edges,
)
from app.services.qa_role_visibility import merge_shards
from app.services.qa_selectors import PROBE_JS, merge_probe
from app.services.ui_selector_render import anchor_selector, infer_kind
from app.services.qa_survey_guard import (
    MAIN_CRAWL_ROLE,
    SAFE_TO_CLICK,
    classify_control,
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
SHALLOW_MAX_PAGES = 40


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


def _base_url() -> str:
    """被测前端地址。**只能从变量取。**

    写死一个地址在这里，后果不是"换环境挂了"（那还看得见），是**打到了不该打的
    那台机器上**，而脚本照跑不误、不报任何错。
    """
    base = _env("BASE_URL", "").strip().rstrip("/")
    if not base:
        raise ValueError(
            "没有 BASE_URL —— 页面枚举必须知道爬哪个环境，不猜、不用默认值。"
            "在项目环境变量里配上再跑。")
    return base


def _role_credentials(role: str) -> tuple[str, str]:
    """角色账号。约定跟 `pw_conftest.py` 一致：`<ROLE>_USERNAME` / `<ROLE>_PASSWORD`。

    角色名里的横线换成下划线再转大写（`qa-auditor` → `QA_AUDITOR_USERNAME`）。
    """
    prefix = role.replace("-", "_").upper()
    return _env(f"{prefix}_USERNAME"), _env(f"{prefix}_PASSWORD")


# ── L1：路由拦截 ──────────────────────────────────────────────────────────

def make_readonly_guard(ledger: dict):
    """返回给 `context.route("**/*", …)` 用的回调。

    这里**只有**「问判定 + abort」两行是有意义的；判定本体在 `qa_survey_guard`。
    被拦下的写请求记进账本 —— 拦到东西不是"没事发生"，是**爬虫差点动了别人的数据**，
    这件事要能在页面上看见，不然下次就没人知道这层网救过命。
    """
    async def _guard(route, request):
        if is_write_request(request.method, request.url):
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
_COLLECT_JS = """() => {
  const out = [];
  const sel = 'button, a[href], [role="button"], [role="switch"], [role="tab"],'
            + ' [role="menuitem"], input[type="checkbox"], input[type="radio"]';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;   // 藏起来的不算「页面上有」
    out.push({
      label: (el.getAttribute('aria-label') || el.innerText || el.value || '').trim().slice(0, 120),
      role: el.getAttribute('role') || el.tagName.toLowerCase(),
      testid: el.getAttribute('data-testid') || '',
      id: el.id || '',
      href: el.getAttribute('href') || '',
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
    });
  }
  return out;
}"""


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


def _state_of(raw: dict) -> str:
    """`present` / `enabled` —— 只记**看得见的事实**。

    `reachable`（点了真能进去）要真点一下才知道，而那属于 L2 允许的读操作里
    最贵的一种；这一版不做，账本里就不会出现 `reachable`。
    **宁可少一档，也不要把 `enabled` 当成 `reachable` 写进去** ——
    那是把没验证过的事记成验证过了。
    """
    return "present" if raw.get("disabled") else "enabled"


def collect_items(page_path: str, page_title: str, raw_items, ledger: dict) -> list[dict]:
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
        out.append({
            "key": f"{page_path}::{anchor}",
            "page_path": page_path,
            "page_title": page_title,
            "anchor": anchor,
            "anchor_kind": anchor_kind,
            "label": label,
            "control_type": kind,
            "state": _state_of(raw),
            "endpoints": [],
        })
    return out


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
        res = await page.evaluate(PROBE_JS, payload)
    except Exception as e:                               # noqa: BLE001
        ledger.setdefault("selectorProbeFailed", []).append(
            {"path": path, "error": type(e).__name__})
        return
    merge_probe(ledger.setdefault("selectorProbe", {}), path, res)


# ── 一个角色的一趟 ────────────────────────────────────────────────────────

async def _login(page, base_url: str, role: str, ledger: dict) -> bool:
    """登录。**唯一默认放行的写请求**（`qa_survey_guard.DEFAULT_WRITE_ALLOWLIST`）。

    放行次数记账：它是账本项不是免检项。
    """
    user, pwd = _role_credentials(role)
    if not user or not pwd:
        ledger.setdefault("rolesSkipped", []).append(role)
        return False
    # 登录页也是一格时窗，否则登录那几个请求会整个落进 `edgesUnwindowed`。
    # **但它 `tail: False`（不许延长到下一次导航）**：提交之后浏览器会自己跳到
    # 落地页，延长就把落地页的流量记到 `/login` 名下了 —— 那种错归属在报告上
    # 和对的长得一样。中间这段宁可记进"归不了页"，也不归错页。
    login_path = _env("LOGIN_PATH", "login")
    win = {"path": "/" + login_path.lstrip("/"), "startedAt": _now(), "tail": False}
    ledger.setdefault("pageWindows", {}).setdefault(role, []).append(win)
    await page.goto(urljoin(base_url + "/", login_path.lstrip("/")),
                    timeout=PAGE_TIMEOUT_MS)
    await page.fill(_env("LOGIN_USER_SELECTOR", "input[name=username]"), user)
    await page.fill(_env("LOGIN_PASS_SELECTOR", "input[name=password]"), pwd)
    await page.click(_env("LOGIN_SUBMIT_SELECTOR", "button[type=submit]"))
    await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
    ledger["loginCount"] = ledger.get("loginCount", 0) + 1
    win["endedAt"] = _now()
    return True


async def crawl_role(browser, base_url: str, role: str, page_paths: list[str],
                     ledger: dict, har_dir: Path,
                     selector_probe: list[dict] | None = None) -> list[dict]:
    """爬一个角色。返回账本行；**一页失败不拖垮整趟**，只记数。

    `selector_probe` 是 QA 仓那张公共选择器表（`qa_selectors.probe_payload`
    给的清单）。传了就在每一页上**只读地**数一遍命中，账本落
    `ledger["selectorProbe"]`。判档在 `qa_selectors.roll_up`，这里一个判断都不做。
    """
    har_path = har_dir / f"{role}.har"
    context = await browser.new_context(record_har_path=str(har_path),
                                        record_har_content="omit")
    await context.route("**/*", make_readonly_guard(ledger))
    items: list[dict] = []
    try:
        page = await context.new_page()
        if not await _login(page, base_url, role, ledger):
            return items
        # 这个角色**真正走到**的页面，一页一记。矩阵那边靠它把「没探到」
        # 和「看不见」分开 —— 只有 `pagesVisited` 那个总数的话，
        # 一个浅扫角色在第 41 页什么都没看见，会被算成「它被禁掉了」。
        probed = ledger.setdefault("pagesProbed", {}).setdefault(role, [])
        # P 边的锚：HAR 里没有「这条请求属于哪次导航」这种字段，只能靠时间。
        # 时窗记在账本上（而不是当返回值），一是 `pagesProbed` 已经是这个先例，
        # 二是归页这件事**要能复查** —— 边归错了页的时候，得看得出当时的边界。
        windows = ledger.setdefault("pageWindows", {}).setdefault(role, [])
        for path in page_paths:
            # 先记 startedAt 再 goto：反了的话 goto 期间的请求就落在窗外了。
            # **失败的页也记一格** —— 那一页确实发过请求（它们属于它），
            # 而且这一格还兼作上一页的右边界：不记的话上一页会一路延长过来，
            # 把这一页的流量吃进自己名下。
            win = {"path": path, "startedAt": _now()}
            windows.append(win)
            try:
                await page.goto(urljoin(base_url + "/", path.lstrip("/")),
                                timeout=PAGE_TIMEOUT_MS)
                await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
                raw = await page.evaluate(_COLLECT_JS)
            except Exception as e:                       # noqa: BLE001
                # **记账，不抛。** 这一页的 item 在 diff 里会被降级成 unknown
                # （`qa_page_survey.diff_items`），绝不进 `removed` ——「没走到」和
                # 「功能没了」在产物上长得一模一样，混过去就会凭空报出一批不存在的缺口。
                # 记**结构化的一条**，不拼成 `f"{path}: {err}"`：拼了下游就得反解析，
                # 而路径里本来就可能带 ": "，解析一歪那一页就不算失败页，
                # 它的 item 立刻变成 `removed` —— 正是这条规则要防的那个假缺口。
                ledger.setdefault("pagesFailed", []).append(
                    {"path": path, "error": type(e).__name__})
                win["endedAt"] = _now()
                continue
            if not raw:
                ledger.setdefault("pagesEmptyState", []).append(path)
            title = await page.title()
            items.extend(collect_items(path, title, raw, ledger))
            await _probe_selectors(page, path, selector_probe, ledger)
            win["endedAt"] = _now()
            ledger["pagesVisited"] = ledger.get("pagesVisited", 0) + 1
            # 走到了就记，**哪怕这一页一个控件都没有** —— 空页恰恰是
            # 「探过了，确实看不见」，那是可比的格子，不是未探测。
            probed.append(path)
    finally:
        # HAR 只在 close 时落盘 —— 不 close 就是一个空文件。
        await context.close()
        # 最后一格的右边界。少了它，最后一页的尾巴无处可延，那一页
        # `networkidle` 之后的轮询会全部记进「归不了页」。
        ledger.setdefault("contextClosedAt", {})[role] = _now()
    return items


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
                     totals_probe=None, selector_probe=None) -> dict:
    """跑完一趟，返回 `{status, ledger, items, har, page_edges}`。

    分片：主爬角色深爬全部页面，其余角色**浅扫**（角色维度只问「看得见什么」）。
    并发 `MAX_PARALLEL_SHARDS`，对方是测试环境不是压测靶子。

    终态由 `resolve_terminal_status` 定，**`dirty` 压过 `failed`**：
    一趟全片失败但环境里的数变了，要看的是"我们动了什么"。

    `page_edges` 是**页面级**的 P 边（打开这一页浏览器发了什么），归页规则在
    `qa_page_traffic`。它**不写进 item 的 `endpoints`** —— 这一趟一个控件都没
    点过，写进去等于凭空造一条 `observed` 的控件→端点边。`routes` 只用来推
    API 前缀兜底分类（拿不到就只靠 `_resourceType`，会在 declarations 里说明）。

    `selector_probe` 传了就顺路验一遍 QA 仓那张公共选择器表在真实渲染里指到东西
    没有（`qa_selectors`）。**账本里必须能看出这一趟到底探没探** ——
    `selectorProbe` 这个键在时说明探了（`pages` 是探过的页），不在时就是没探；
    `roll_up(probed=...)` 靠它把「探了、都没见到」和「压根没探」分开，
    混起来会让人去查 400 多条不存在的过期选择器。
    """
    from playwright.async_api import async_playwright

    base_url = (base_url or _base_url()).rstrip("/")
    main_role = pick_main_crawl_role(roles)          # 没有只读账号 → 这里就不许开爬
    others = shallow_scan_roles(roles)
    ledger: dict = {"writesBlocked": 0, "pagesVisited": 0, "controlsUnknown": 0,
                    "loginCount": 0, "rolesShallow": others,
                    # **点过几个控件 = 0，而且要明写出来。** 无向枚举一个控件都
                    # 不点，这不是"这一趟碰巧没点"，是这一版的设计。写出来是为了
                    # 让下游（`compute_gaps(controls_clicked=...)`）能把 G4
                    # 关掉 —— 缺这个数它会把每个 enabled 控件都报成
                    # 「点下去什么都没发生」。键名和那个参数是一对，别单改一边。
                    "controlsClicked": 0,
                    # 这一趟拿了几条选择器去探。**0 也要写出来** —— 清单是空的
                    # （QA 仓没拉到 / 解析全军覆没）和"探了但一条都没命中"在报告上
                    # 长得一模一样，而前者是我们自己没跑成，不是他的选择器有问题。
                    "selectorsProbed": len(selector_probe or [])}

    ledger["selfCheck"] = self_check_label(totals_probe)
    totals_before = await totals_probe() if totals_probe else None

    shards = [(main_role, page_paths)] + [(r, page_paths[:SHALLOW_MAX_PAGES]) for r in others]
    ledger["shardsTotal"] = len(shards)
    shard_rows: list[dict] = []
    hars: dict[str, dict] = {}
    buckets: list[dict] = []
    api_prefixes = api_prefixes_from_routes(routes)
    ok = 0

    with tempfile.TemporaryDirectory(prefix="qa-survey-") as tmp:
        har_dir = Path(tmp)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            sem = asyncio.Semaphore(MAX_PARALLEL_SHARDS)

            async def _one(role: str, paths: list[str]):
                async with sem:
                    return role, await crawl_role(browser, base_url, role, paths,
                                                  ledger, har_dir,
                                                  selector_probe)

            results = await asyncio.gather(
                *(_one(r, p) for r, p in shards), return_exceptions=True)
            await browser.close()

        for res in results:
            if isinstance(res, BaseException):
                ledger.setdefault("shardsFailed", []).append(type(res).__name__)
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

    items = merge_shards(shard_rows, main_role=main_role)
    traffic = merge_edges(buckets)
    ledger["traffic"] = {k: v for k, v in traffic.items() if k != "edges"}

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
