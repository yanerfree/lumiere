"""页面枚举爬虫：把被测前端上**有哪些可操作项**扫成账本。

它回答的是三方对账里 Q 侧和 P 侧都答不了的那一问：「页面上有这个操作，用例里一条都
没覆盖」。这条链的每一跳都是纯代码（页面控件 → 请求 → 路由组 → 域码 → 场景 ID），
所以这里只负责**如实记下看到了什么**，一个判断都不做 —— 判断在 `qa_coverage_reconcile`。

**爬的是别人的测试环境。** 五层只读的判定全在 `app/services/qa_survey_guard.py`，
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
from pathlib import Path
from urllib.parse import urljoin, urlsplit

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


# ── 一个角色的一趟 ────────────────────────────────────────────────────────

async def _login(page, base_url: str, role: str, ledger: dict) -> bool:
    """登录。**唯一默认放行的写请求**（`qa_survey_guard.DEFAULT_WRITE_ALLOWLIST`）。

    放行次数记账：它是账本项不是免检项。
    """
    user, pwd = _role_credentials(role)
    if not user or not pwd:
        ledger.setdefault("rolesSkipped", []).append(role)
        return False
    await page.goto(urljoin(base_url + "/", _env("LOGIN_PATH", "login").lstrip("/")),
                    timeout=PAGE_TIMEOUT_MS)
    await page.fill(_env("LOGIN_USER_SELECTOR", "input[name=username]"), user)
    await page.fill(_env("LOGIN_PASS_SELECTOR", "input[name=password]"), pwd)
    await page.click(_env("LOGIN_SUBMIT_SELECTOR", "button[type=submit]"))
    await page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT_MS)
    ledger["loginCount"] = ledger.get("loginCount", 0) + 1
    return True


async def crawl_role(browser, base_url: str, role: str, page_paths: list[str],
                     ledger: dict, har_dir: Path) -> list[dict]:
    """爬一个角色。返回账本行；**一页失败不拖垮整趟**，只记数。"""
    har_path = har_dir / f"{role}.har"
    context = await browser.new_context(record_har_path=str(har_path),
                                        record_har_content="omit")
    await context.route("**/*", make_readonly_guard(ledger))
    items: list[dict] = []
    try:
        page = await context.new_page()
        if not await _login(page, base_url, role, ledger):
            return items
        for path in page_paths:
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
                continue
            if not raw:
                ledger.setdefault("pagesEmptyState", []).append(path)
            title = await page.title()
            items.extend(collect_items(path, title, raw, ledger))
            ledger["pagesVisited"] = ledger.get("pagesVisited", 0) + 1
    finally:
        # HAR 只在 close 时落盘 —— 不 close 就是一个空文件。
        await context.close()
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
                     page_paths: list[str],
                     totals_probe=None) -> dict:
    """跑完一趟，返回 `{status, ledger, items, har}`。

    分片：主爬角色深爬全部页面，其余角色**浅扫**（角色维度只问「看得见什么」）。
    并发 `MAX_PARALLEL_SHARDS`，对方是测试环境不是压测靶子。

    终态由 `resolve_terminal_status` 定，**`dirty` 压过 `failed`**：
    一趟全片失败但环境里的数变了，要看的是"我们动了什么"。
    """
    from playwright.async_api import async_playwright

    base_url = (base_url or _base_url()).rstrip("/")
    main_role = pick_main_crawl_role(roles)          # 没有只读账号 → 这里就不许开爬
    others = shallow_scan_roles(roles)
    ledger: dict = {"writesBlocked": 0, "pagesVisited": 0, "controlsUnknown": 0,
                    "loginCount": 0, "rolesShallow": others}

    ledger["selfCheck"] = self_check_label(totals_probe)
    totals_before = await totals_probe() if totals_probe else None

    shards = [(main_role, page_paths)] + [(r, page_paths[:SHALLOW_MAX_PAGES]) for r in others]
    ledger["shardsTotal"] = len(shards)
    items: list[dict] = []
    hars: dict[str, dict] = {}
    ok = 0

    with tempfile.TemporaryDirectory(prefix="qa-survey-") as tmp:
        har_dir = Path(tmp)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            sem = asyncio.Semaphore(MAX_PARALLEL_SHARDS)

            async def _one(role: str, paths: list[str]):
                async with sem:
                    return role, await crawl_role(browser, base_url, role, paths,
                                                  ledger, har_dir)

            results = await asyncio.gather(
                *(_one(r, p) for r, p in shards), return_exceptions=True)
            await browser.close()

        for res in results:
            if isinstance(res, BaseException):
                ledger.setdefault("shardsFailed", []).append(type(res).__name__)
                continue
            role, rows = res
            ok += 1
            # 主爬那份是账本本体；浅扫只贡献「这个角色看得见哪些 key」。
            if role == main_role:
                items = rows
            else:
                seen = {r["key"] for r in rows}
                for row in items:
                    if row["key"] in seen:
                        row.setdefault("roles_visible", []).append(role)
            har = sanitize_har(har_dir / f"{role}.har")
            if har is not None:
                hars[role] = har

    totals_after = await totals_probe() if totals_probe else None
    status = resolve_terminal_status(shards_total=len(shards), shards_ok=ok,
                                     totals_before=totals_before,
                                     totals_after=totals_after)
    status = degrade_for_gaps(status, ledger)
    ledger["shardsOk"] = ok
    ledger["safeToClick"] = list(SAFE_TO_CLICK)
    ledger["mainRole"] = MAIN_CRAWL_ROLE
    return {"status": status, "ledger": ledger, "items": items, "har": hars}
