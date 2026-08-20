"""场景形态检查 —— 「验证链缺一环」这类问题，回推时当场说出来。

全部是**软警告，一条都不拦**（用户拍的口径）。理由：这几条判据都得从标题/步骤名的
自然语言里猜意图，猜错就是滥报 —— 而人看两条假的就再也不看这个提示了。
提示的价值在**时机**：CC 回推那一刻还在上下文里，补一步很便宜；等人看出来再返工，
它得把整条链重新读一遍。

来自外部 CC 真实返工的三类（网关订阅审批那批）：
  ① 一条场景验两个功能，还把对照组塞进同一条 —— 标题一眼看不出在验什么
  ② 只断创建接口的响应，没有 GET 回读，也没验真生效
  ③ 「生效」的判据错了：改了控制面的状态字段就算生效，
     而真正的判据是**拿凭据去调那个需要认证的服务能不能通**

②③ 这两条的共同形状是"所有断言都打在同一个入口上"。
**数据面入口叫什么，平台不猜也不硬编码** —— 每个项目不一样（网关叫 gatewayBase，
别的项目可能是另一套）。CC 在测的过程中摸清了，就自己写进共享数据
（tb_upsert_automation_resource），下次用 ${资源名} 取。这里只判"是不是只打了一个入口"。
"""
from __future__ import annotations

import re

# 「这一步是在验生效」的措辞。用词是从真实那批场景里抄的，不是想当然列的。
# 步骤名的**角色前缀**。有了它，下面几条判据就不用猜意图了 ——
# 「哪步是制备、哪步在验证」原来靠词表猜（制备|准备|前置|清理|登录…），
# CC 换个说法就误判。现在读前缀；没有前缀的老数据才回退去猜。
_ROLE_PREFIX = re.compile(r"^\s*(前置|制备|准备|操作|动作|验证|校验|清理|收尾)\s*[:：]")
_ROLE_MAP = {"前置": "setup", "制备": "setup", "准备": "setup",
             "操作": "act", "动作": "act",
             "验证": "verify", "校验": "verify",
             "清理": "cleanup", "收尾": "cleanup"}
# 没写前缀时的兜底猜法（老数据）。词表永远补不全，这正是要推前缀的原因。
_SETUP_GUESS = re.compile(r"制备|准备|前置|清理|收尾|建号|造数|登录|取|查询基准")
_EFFECT_WORDS = re.compile(
    r"生效|可调通|调得通|调不通|转发|放行|拦截|不中断|恢复调用|真的可用|真正可用|"
    r"能访问|访问不了|返回 ?40[13]|页面上|入口.*(灰|禁用)|按钮.*(灰|禁用)")


def step_role(name: str) -> str | None:
    """读步骤名的角色前缀：setup / act / verify / cleanup。没写前缀返回 None。"""
    m = _ROLE_PREFIX.match(name or "")
    return _ROLE_MAP.get(m.group(1)) if m else None


_HOST_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_WRITE = ("POST", "PUT", "PATCH", "DELETE")


def _host_var(url: str) -> str | None:
    """URL 开头那个变量就是入口（${BASE_URL} / ${gatewayBase} / ${资源名}）。"""
    m = _HOST_VAR.match((url or "").strip())
    return m.group(1) if m else None


def _path_shape(url: str) -> str:
    """把 URL 归一成"哪个资源"：去掉 host 变量、把路径里的 ${var} 换成占位。"""
    u = _HOST_VAR.sub("{v}", (url or "").strip())
    u = u.split("?")[0]
    return re.sub(r"/\{v\}", "/{id}", u)


def _is_setup(name: str) -> bool:
    """这步是不是制备/清理。**先读前缀，读不到才猜**。"""
    role = step_role(name)
    if role is not None:
        return role in ("setup", "cleanup")
    return bool(_SETUP_GUESS.search(name or ""))


def _expects_rejection(st: dict) -> bool:
    """这一步断的是"应当被拒"（4xx/5xx）。**被拒就等于什么都没写**，
    没有任何东西可以读回来 —— 活体自测时这条误报过：
    「验证: 同级重名被拒 409」被要求"写完要读回"。
    """
    for a in (st.get("assertions") or []):
        if not isinstance(a, dict) or a.get("type") != "status":
            continue
        v = a.get("value", a.get("expected"))
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, int) and x >= 400:
                return True
    return False


def no_readback(steps: list[dict], has_ui_script: bool = False) -> list[dict]:
    """写完之后**一点都没验效果**。判据不是"有没有 GET 回读"。

    上一版就是照"有没有同资源 GET"判的，那是错的：
    `发布服务 → 打一下这个服务，通了` 是**比回读更强**的验证 ——
    回读只能证明控制面记下了，下游调通才证明真生效。逼它再 GET 一次是加冗余接口。

    所以"验了效果"算这三种，任一即可：
      · 同资源的后续 GET（读回控制面）
      · 打到**另一个入口**的后续请求（下游/数据面调通，这是最强的）
      · 这条用例挂着 UI 脚本（页面上验可见结果）
    三种都没有，才是真的"写完就完了"。
    """
    out = []
    reads = {}
    hosts_by_index = []
    for i, st in enumerate(steps):
        hosts_by_index.append(_host_var(st.get("url", "")))
        if (st.get("method") or "GET").upper() == "GET":
            reads.setdefault(_path_shape(st.get("url", "")), []).append(i)
    for i, st in enumerate(steps):
        m = (st.get("method") or "GET").upper()
        name = st.get("name") or f"第 {i + 1} 步"
        if m not in _WRITE or _is_setup(name):
            continue
        if _expects_rejection(st):
            continue                      # 断的是"应当被拒" → 什么都没写，没得读回
        if has_ui_script:
            continue                      # 页面上验可见结果，比接口回读更接近用户
        shape = _path_shape(st.get("url", ""))
        base = shape.rsplit("/", 1)[0] if shape.endswith("/{id}") else shape
        later_read = [j for cand, idxs in reads.items() for j in idxs
                      if j > i and (cand == shape or cand.startswith(base))]
        # 后面有明确标了「验证:」的步骤 → 它就是在验这件事，不用再猜验法
        later_verify = [j for j in range(i + 1, len(steps))
                        if step_role(steps[j].get("name") or "") == "verify"]
        if later_verify:
            continue
        my_host = hosts_by_index[i]
        # 后面打到别的入口 = 下游调通，这就是在验生效
        later_downstream = [j for j in range(i + 1, len(steps))
                            if hosts_by_index[j] and hosts_by_index[j] != my_host]
        if later_read or later_downstream:
            continue
        out.append({"step": i + 1, "kind": "no_readback", "value":
                    f"第 {i + 1} 步「{name}」写完之后，后面既没有读回来确认、"
                    f"也没有打到别的入口验证生效。响应体是接口自己说的，"
                    f"不能只凭它就算验过了 —— 实测踩过：接口回 200、字段压根没落库。"
                    f"**下游调通比回读更强**，两者有其一就够，不用两样都写；"
                    f"制备类步骤不需要，那就在步骤名里写明「制备：…」。"})
    return out


def single_entry_effect(steps: list[dict], title: str = "") -> list[dict]:
    """声称验「生效」，但所有断言都打在同一个入口上。

    「转 approved」只是控制面写了个状态；真正的生效判据是**拿那个凭据去调
    需要认证的服务通不通**、或者**页面上那个入口还灰不灰**。
    平台不猜你的数据面叫什么：摸清了就写进共享数据（tb_upsert_automation_resource），
    下次 ${资源名} 直接取。
    """
    hosts = {h for st in steps if (h := _host_var(st.get("url", "")))}
    # 标了「验证:」前缀的步骤优先 —— 那是作者自己声明的"这步在验什么"，
    # 比在所有步骤名里搜关键词准得多。
    verify_steps = [st for st in steps if step_role(st.get("name") or "") == "verify"]
    pool = verify_steps or [st for st in steps if not _is_setup(st.get("name") or "")]
    claims = [st for st in pool if _EFFECT_WORDS.search(st.get("name") or "")]
    if not claims and _EFFECT_WORDS.search(title or ""):
        claims = [{"name": title}]
    if not claims or len(hosts) != 1:
        return []
    only = next(iter(hosts))
    names = "、".join((c.get("name") or "")[:18] for c in claims[:2])
    return [{"step": 0, "kind": "control_plane_only", "value":
             f"这条在验「生效」（{names}），但所有请求都打在 ${{{only}}} 一个入口上。"
             f"控制面的状态字段变了不等于真生效 —— 判据是拿凭据去调那个需要认证的服务、"
             f"或者看页面上入口还灰不灰。这个项目的数据面入口摸清了就写进共享数据"
             f"（tb_upsert_automation_resource），之后 ${{资源名}} 直接取。"}]


def control_group_in_one(steps: list[dict]) -> list[dict]:
    """对照组塞进了同一条场景：同一个请求换个身份再做一遍、断言一模一样。

    对照不用挤在一条里 —— **两条用例互为对照**，标题各自说清在验谁，
    跑起来还能各自定位。挤在一条里的代价是真实的：
    真实那批里「租户管理员本租户订阅」和「跨租户订阅」塞成一条，
    标题只能写得很泛，而且前半段把审批开关关了，后半段的结论就假了。
    """
    seen: dict[tuple, dict] = {}
    out = []
    for i, st in enumerate(steps):
        name = st.get("name") or ""
        if _is_setup(name):
            continue
        m = (st.get("method") or "GET").upper()
        key = (m, _path_shape(st.get("url", "")),
               repr(sorted((st.get("assertions") or []), key=repr)))
        auth = str((st.get("headers") or {}).get("Authorization", ""))
        prev = seen.get(key)
        if prev is not None and prev["auth"] != auth and auth and prev["auth"]:
            out.append({"step": i + 1, "kind": "control_group_in_one", "value":
                        f"第 {prev['i'] + 1} 步和第 {i + 1} 步是同一个请求换了身份、"
                        f"断言一模一样 —— 这是**对照组**。拆成两条用例互为对照："
                        f"标题各自说清在验谁，跑红了也能直接定位是哪个角色。"
                        f"挤在一条里，前半段改过的开关会让后半段的结论失真。"})
        else:
            seen[key] = {"auth": auth, "i": i}
    return out


# **「一条验了两件事」这条判据，试过之后没做。** 拿真实的两个标题一比就知道判不了：
#   「平台关闭审批开关后申请免审批直接生效，开关恢复后申请重新回到待审批」← 该拆
#   「禁用服务后网关停止转发，重新启用后恢复调用」                    ← 不该拆（一件事两阶段）
# 两个都是「A 后 X，B 后 Y」的形状，文本上没有任何可靠差别 —— 上一版按逗号/连接词判，
# 第二个直接误报。这类只能靠人和 CC 自己判，所以只写进规范（见 _SPEC_SCENARIO_SHAPE），
# 不做检查：一个分不清对错的提示，比没有提示更糟。

def check_shape(steps: list[dict], title: str = "", has_ui_script: bool = False) -> list[dict]:
    """全部软警告，一条都不拦。"""
    return (no_readback(steps, has_ui_script) + single_entry_effect(steps, title)
            + control_group_in_one(steps))
