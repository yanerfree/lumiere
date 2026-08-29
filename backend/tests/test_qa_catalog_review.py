"""QA 域级 AI 评审：环境变量对账、prompt 组装、结果解析。

这一层不碰数据库也不打模型 —— 真正值得盯的是三件**能算错**的事：
① 「环境缺这个变量」是不是真缺（误报一次，人就再也不信这一列）；
② prompt 里有没有把变量**值**带出去（带出去就是泄密）；
③ 模型胡说时会不会退化成一份"没发现问题"的空壳（那比报错难查得多）。

Test ID: qa-catalog-review-UT-001
Priority: P0
"""
import json
import pathlib

import pytest

from app.services import qa_catalog_review as qr
from app.services import qa_evidence_check as ec

SCRIPT = """\
#!/usr/bin/env bash
# @scenario AGT-11
# @tier scenario
set -euo pipefail
source "$(dirname "$0")/../lib/common.sh"

local_tmp=$(mktemp)
RETRIES=3
for i in $(seq 1 $RETRIES); do
  curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "$API_BASE/agents" -o "$local_tmp"
done
echo "${LOG_LEVEL:-info}"
echo "$HOME $PATH"
"""

LIB = """\
export API_BASE="https://uag.example.com/api"
COMMON_TIMEOUT=30
"""


class TestEnvGaps:
    """「脚本要、环境没有」这一列。宁可漏报不可误报。"""

    def test_只报环境真没有的那个(self):
        gaps = qr.env_gaps([{"path": "a.sh", "content": SCRIPT}], {"BASE_URL"}, [LIB])

        names = [g["name"] for g in gaps]
        assert names == ["ADMIN_TOKEN"], names
        assert gaps[0]["scripts"] == ["a.sh"]

    def test_环境里配了就不算缺(self):
        gaps = qr.env_gaps([{"path": "a.sh", "content": SCRIPT}], {"ADMIN_TOKEN"}, [LIB])

        assert gaps == []

    def test_公共库里定义的不算缺(self):
        """每个脚本都 source 同一份 lib —— 不认这一层，这一列会全是假的。"""
        no_lib = qr.env_gaps([{"path": "a.sh", "content": SCRIPT}], {"ADMIN_TOKEN"}, [])

        assert [g["name"] for g in no_lib] == ["API_BASE"]

    def test_有默认值的不算缺(self):
        """`${LOG_LEVEL:-info}`：脚本自己兜住了，环境没有也跑得起来。"""
        gaps = qr.env_gaps([{"path": "a.sh", "content": "echo ${LOG_LEVEL:-info}\n"}], set())

        assert gaps == []

    def test_脚本自己赋过值的和shell自带的都不算(self):
        gaps = qr.env_gaps([{"path": "a.sh", "content": SCRIPT}],
                           {"ADMIN_TOKEN", "API_BASE"}, [])

        # RETRIES 自己赋的、HOME/PATH 是 shell 的、local_tmp 是小写临时变量
        assert gaps == []

    def test_同一个变量多个脚本都要时列出来源(self):
        body = 'curl -H "x: $TENANT_ID" "$API_BASE/x"\n'
        gaps = qr.env_gaps([{"path": "a.sh", "content": body},
                            {"path": "b.sh", "content": body}], {"API_BASE"})

        assert len(gaps) == 1
        assert gaps[0]["scripts"] == ["a.sh", "b.sh"]

    # ── 下面这几条都是 2026-08-26 在 uag-qa 上真跑出来的假阳/漏报 ──

    def test_一行上的第二个赋值也算定义过(self):
        """`export A=1 B=2` 和 `A=""; B=""` 都是仓库里的真实写法。

        只认每行第一个的话，MCP 域会凭空多出 5 个"环境缺的"。
        """
        lib = ('export MCPB_TEAM="$tid" MCPB_AGENT="$aid" MCPB_OWNER="$owner"\n'
               'TA_ID=""; MB_ID=""\n')
        body = 'echo "$MCPB_AGENT $MCPB_OWNER $TA_ID $MB_ID $MCPB_TEAM"\n'

        assert qr.env_gaps([{"path": "a.sh", "content": body}], set(), [lib]) == []

    def test_read一次读进好几个也算定义过(self):
        gaps = qr.env_gaps(
            [{"path": "a.sh", "content": 'read -r CODE BODY LEN <<<"$resp"\n'
                                         'echo "$CODE $BODY $LEN"\n'}], set())

        assert gaps == []

    def test_夹具运行时拼出来的名字不算缺(self):
        """`make_identity MB` 造出 MB_ID/MB_TOKEN/…，仓库里搜不到 `MB_USER=`。"""
        lib = 'make_identity() {\n  printf -v "${1}_ID" %s "$id"\n'\
              '  printf -v "${1}_TOKEN" %s "$tok"\n}\n'
        body = 'make_identity MB\ncurl -H "t: $MB_TOKEN" "$X/u/$MB_ID"\n'

        gaps = qr.env_gaps([{"path": "a.sh", "content": body}], {"X"}, [lib])

        assert gaps == []

    def test_QA_ROOT不会被拆成动态后缀(self):
        """`${QA_ROOT}` 拆成 `${QA}`+`_ROOT` 的话，`_APIKEY`/`_DSN` 也会变成后缀，
        把真缺口一起吃掉 —— 这一列唯一有价值的东西就没了。"""
        lib = 'export QA_ROOT="${PWD}"\nexport LOG_DIR="${QA_ROOT}/logs"\n'

        assert qr.dynamic_suffixes(lib) == set()

        gaps = qr.env_gaps([{"path": "a.sh", "content": 'echo "$UAG_APIKEY"\n'}],
                           set(), [lib])
        assert [g["name"] for g in gaps] == ["UAG_APIKEY"]

    def test_声明从外面拿的变量算缺口(self):
        """`export X="${X:-}"` 不是定义，是仓库在明说 X 得从环境传进来。

        uag-qa 的 config/env.sh 就这么列外部输入；脚本那头写的是
        `[ -n "${UAG_APIKEY:-}" ] || skip_case ...` —— 带默认值，按"引用"扫不出来，
        没配就整条静默跳过：报告是绿的，一条数据面用例都没跑。
        """
        lib = ('export UAG_APIKEY="${UAG_APIKEY:-}"\n'
               'export PSQL_DSN="${PSQL_DSN:-}"\n')
        gaps = qr.env_gaps([{"path": "a.sh", "content": "echo hi\n"}], set(),
                           [lib], ["config/env.sh"])

        assert [g["name"] for g in gaps] == ["PSQL_DSN", "UAG_APIKEY"]
        assert gaps[0]["scripts"] == ["config/env.sh"]      # 指到声明它的那份库

    def test_声明了但环境里有就不算缺(self):
        lib = 'export UAG_APIKEY="${UAG_APIKEY:-}"\n'

        assert qr.env_gaps([{"path": "a.sh", "content": "echo hi\n"}],
                           {"UAG_APIKEY"}, [lib]) == []

    def test_自带兜底值的声明不算缺(self):
        """`${API_BASE:-http://localhost:3000}` 没配也跑得起来，跟空兜底是两回事。"""
        lib = ('export API_BASE="${API_BASE:-http://localhost:3000}"\n'
               'export UAG_APIKEY="${UAG_APIKEY:-}"\n')
        gaps = qr.env_gaps([{"path": "a.sh", "content": "echo hi\n"}], set(), [lib])

        assert [g["name"] for g in gaps] == ["UAG_APIKEY"]

    def test_声明完又被赋了真值就不算缺(self):
        lib = 'export TOKEN="${TOKEN:-}"\nTOKEN="$(login)"\n'

        assert qr.env_gaps([{"path": "a.sh", "content": 'echo "$TOKEN"\n'}],
                           set(), [lib]) == []

    def test_声明后面带注释也算缺(self):
        """uag-qa 里最该报的那条恰好带尾注释：

            export PASSWORD="${PASSWORD:-}"          # 刻意不给默认值,强制外部注入

        匹配失败不是"不知道"，是倒向另一边被当成定义过了 —— 于是登录凭据这个
        最硬的缺口一声不吭，而它一缺，整个域的脚本连 require_login 都过不去。
        """
        lib = 'export PASSWORD="${PASSWORD:-}"          # 刻意不给默认值,强制外部注入\n'

        gaps = qr.env_gaps([{"path": "a.sh", "content": "echo hi\n"}],
                           set(), [lib], ["config/env.sh"])

        assert [g["name"] for g in gaps] == ["PASSWORD"]

    def test_带注释的自带兜底值仍不算缺(self):
        lib = 'export WEB_URL="${WEB_URL:-http://127.0.0.1:3000}"  # 前端控制台\n'

        assert qr.env_gaps([], set(), [lib], ["config/env.sh"]) == []


class TestSourcedFiles:
    def test_按文件名在仓库里找公共库(self):
        """路径里带 `$(dirname "$0")` 拼接，解析不了，只能按文件名找。"""
        hits = qr.sourced_files(SCRIPT, ["scenarios/agt/x.sh", "lib/common.sh", "docs/a.md"])

        assert hits == ["lib/common.sh"]

    def test_没有对应文件时不瞎猜(self):
        assert qr.sourced_files(SCRIPT, ["scenarios/agt/x.sh"]) == []


class TestBuildPayload:
    def _payload(self, env_keys, missing=None):
        domain = {"code": "AGT", "name": "Agent 生命周期"}
        scenarios = [{
            "id": "AGT-11", "title": "挂起的 Agent 用其 apikey 打数据面被拒",
            "priority": "P0", "risk": 9, "tier": "scenario", "state": "covered",
            "scripts": [{"path": "scenarios/agt/agent-lifecycle.sh"}], "knownBugs": [],
        }, {
            "id": "AGT-12", "title": "删除后 apikey 立即失效", "priority": "P1", "risk": 6,
            "tier": "api", "state": "gap", "scripts": [], "knownBugs": [],
        }]
        scripts = [{"path": "scenarios/agt/agent-lifecycle.sh", "content": SCRIPT,
                    "truncated": False}]
        return qr.build_payload(domain, scenarios, scripts, "预发", env_keys, missing or [])

    def test_变量值一个字节都不外传(self):
        """环境变量里放的是真凭证。**只能带键名。**"""
        out = self._payload(["ADMIN_TOKEN", "BASE_URL"])

        assert "ADMIN_TOKEN" in out and "BASE_URL" in out
        # 调用方传进来的就只有键名，这里再钉一遍：任何看着像值的东西都不该出现
        assert "https://uag.example.com" not in out.split("## 脚本正文")[0]

    def test_提示词只喂真缺的那一档(self):
        """`ambiguous` 混进「环境里没有的变量名」那一行就废了这一档。

        模型看见一个名字挂在"没有"下面，就会顺着推出「这条场景在这个环境跑不起来」——
        而那正是这一档要拦的那条误报，只是换了个地方冒出来。
        """
        out = self._payload(
            ["ADMIN_PASSWORD"],
            [{"name": "PASSWORD", "scripts": ["a.sh"], "state": "ambiguous",
              "family": ["ADMIN_PASSWORD"]},
             {"name": "UAG_APIKEY", "scripts": ["a.sh"], "state": "absent"}])
        gap_line = [x for x in out.splitlines() if x.startswith("脚本引用了、")][0]

        assert "UAG_APIKEY" in gap_line
        assert "PASSWORD" not in gap_line
        assert "不许由这一行推出任何覆盖结论" in out

    def test_没标state的按真缺算(self):
        """存量结论、以及任何漏标的路径，都要落回「响的那一档」。

        这里的退化方向是刻意选的：漏标当 `absent` 只是多一条要人看的行，
        漏标当 `ambiguous` 是把真缺口悄悄洗白。
        """
        out = self._payload([], [{"name": "UAG_APIKEY", "scripts": ["a.sh"]}])

        assert "UAG_APIKEY" in [x for x in out.splitlines()
                                if x.startswith("脚本引用了、")][0]

    def test_场景清单带上P和R和覆盖状态(self):
        out = self._payload([])

        assert "AGT-11" in out and "P0" in out and "R=9" in out
        assert "已覆盖" in out and "待补" in out
        assert "挂起的 Agent" in out

    def test_脚本正文和环境都进去了(self):
        out = self._payload(["X"], [{"name": "ADMIN_TOKEN", "scripts": ["a.sh"]}])

        assert "环境名：预发" in out
        assert "scenarios/agt/agent-lifecycle.sh" in out
        assert "@scenario AGT-11" in out            # 正文真带进去了，不是只有路径
        assert "ADMIN_TOKEN" in out.split("## 脚本正文")[0]   # 缺口那一段也在


class TestTakeScripts:
    def test_超长脚本截断并打标(self):
        big = "x" * (qr.MAX_SCRIPT_BYTES + 500)
        out = qr.take_scripts(lambda p: big, ["a.sh"])

        assert out[0]["truncated"] is True
        assert len(out[0]["content"]) <= qr.MAX_SCRIPT_BYTES

    def test_读不到的文件跳过而不是塞空字符串(self):
        """塞空的话模型会对着一份空脚本说「什么都没验」—— 凭空造一条假结论。"""
        out = qr.take_scripts(lambda p: None if p == "gone.sh" else "ok", ["gone.sh", "a.sh"])

        assert [s["path"] for s in out] == ["a.sh"]

    def test_总量到顶就不再往里塞(self):
        """一个域最多 14 份脚本，全是大文件时不能把上下文撑爆。"""
        big = "x" * qr.MAX_SCRIPT_BYTES
        out = qr.take_scripts(lambda p: big, [f"{i}.sh" for i in range(40)])

        total = sum(len(s["content"].encode("utf-8")) for s in out)
        assert total <= qr.TOTAL_SCRIPT_BYTES
        assert 0 < len(out) < 40


class TestParseResult:
    def test_带围栏的正常解析(self):
        text = """好的，我看完了。
```json
{"verdict": "bad", "summary": "AGT-11 声明覆盖了但没验",
 "scriptGaps": [{"id": "AGT-11", "path": "a.sh", "severity": "blocker",
                 "problem": "只断了 200", "fix": "断 403"}],
 "catalogGaps": [], "nextUp": [{"id": "AGT-12", "why": "P1 且风险 6"}]}
```"""
        out = qr.parse_result(text)

        assert out["verdict"] == "bad"
        assert out["scriptGaps"][0]["id"] == "AGT-11"
        # nextUp 2026-08-29 停产：模型照旧回了也**在解析这一层就丢掉**。
        # 只删渲染的话它还会继续在库里长，下一个人翻 result JSON 会以为它是活的。
        assert "nextUp" not in out

    def test_没围栏也能从花括号里扒出来(self):
        out = qr.parse_result('{"verdict":"ok","summary":"还行","scriptGaps":[]}')

        assert out["verdict"] == "ok" and out["summary"] == "还行"

    def test_扒不出来就报错而不是返回空壳(self):
        """空壳会在页面上显示成「没发现问题」，而真相是根本没评上。"""
        with pytest.raises(ValueError):
            qr.parse_result("我觉得这个域挺好的，没什么问题。")

    def test_模型乱填verdict时落到risky(self):
        out = qr.parse_result('{"verdict":"完美","scriptGaps":[]}')

        assert out["verdict"] == "risky"

    def test_字符串数组也吃得下(self):
        """S8.1 改了它的归宿，**没改它吃不吃得下**。

        一句白话的清单缺口按定义就指不出出处，所以清单侧的落进被丢那一桶。
        但**它没有消失** —— 原话还在，页面上数得出丢了几条。
        """
        out = qr.parse_result('{"verdict":"ok","catalogGaps":["缺删除后越权"],'
                              '"scriptGaps":["断言太松"]}')

        assert out["scriptGaps"][0]["problem"] == "断言太松"
        assert out["catalogGaps"] == []
        assert out["droppedNoAnchor"][0]["problem"] == "缺删除后越权"

    # ── Epic 2 #1：**替换**了原来的 test_每一项最多留六条 ──
    # 那条没写错，它忠实封样了当时的行为；本次改的就是那个行为。
    # 原行为：`_rows` 的 `[:6]` + 提示词里「每一项最多 6 条」两处一起把结论砍在 6 条。
    # 实测（去掉提示词上限量的那一趟）一个批次能出 104 条，`[:6]` 静默扔掉 30%，
    # 而页面只显示剩下的 —— 看起来就像"这个域只有这么多问题"。
    def test_一批三十条结论一条都不许丢(self):
        gaps = ",".join(f'{{"id":"A-{i}"}}' for i in range(30))
        out = qr.parse_result('{"verdict":"bad","scriptGaps":[' + gaps + ']}')

        assert len(out["scriptGaps"]) == 30
        # 顺序也不许动：severity 排序是 merge_results 的事，parse 这层原样往下传
        assert [g["id"] for g in out["scriptGaps"]] == [f"A-{i}" for i in range(30)]

    # ── ★#2 ──
    def test_提示词里不许再有条数上限(self):
        """删了 `[:6]` 却留着提示词那句 = 模型仍然只写 6 条，代码这层白改。

        **写窄一点**：`brief` 里那句「points 最多 3 条」是有意的（那一段是给人
        三十秒扫一眼的结论，不是清单），别一起误伤。
        """
        assert "每一项最多" not in qr._SYSTEM
        # 反过来兜一下：真正该留的那条还在，说明上面那句断言不是靠"整段没了"过的
        assert "brief" in qr._SYSTEM

    # ── #3：只盯 _rows 这一处切片，别对整个文件扫 ──
    def test_rows里不许再有条数切片(self):
        """⚠ 这条**必须写窄**，否则要么永远红要么删错东西。

        `qa_catalog_review.py` 里有三处 `[:6]`，只有 `_rows` 里那个是目标：
        `env_gaps` 的 `srcs[:6]`（每个变量留几条引用位置）和 markdown 里的
        `splitlines()[:6]`（evidence 显示几行）**都该留**。
        所以只看 `parse_result`（`_rows` 嵌在它里面），不是整个文件。

        ⚠ 而且**看的是 AST 不是源码字符串**：第一版写成 `"[:6]" not in src`，
        当场红了 —— 红在 `_rows` 里那句解释"上一版这里是 `[:6]`"的注释上。
        注释里提一句被删掉的写法是正常的（不写反而没人知道为什么删），
        所以判据不能是"源码里不出现这四个字符"。
        顺带：`[:600]`（单字段长度）该留，AST 判法天然不会误伤它。
        """
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(qr.parse_result)))
        rows = [n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_rows"]
        assert len(rows) == 1, "_rows 没了或改名了 —— 这条测试得跟着改"

        caps = [n for n in ast.walk(rows[0])
                if isinstance(n, ast.Slice) and isinstance(n.upper, ast.Constant)
                and n.upper.value == 6]
        assert not caps, "_rows 里又出现了 [:6] 条数封顶"
        # 兜一下：单字段截断还在，说明上面那句不是靠"整个函数没了"过的
        assert any(isinstance(n, ast.Slice) and isinstance(n.upper, ast.Constant)
                   and n.upper.value == 600 for n in ast.walk(rows[0]))

    # ── #E：§9 E 条。Epic 0 之前写不出来，因为那个数当时还不存在 ──
    def test_MAX_OUTPUT_TOKENS装得下实测最多的那一批(self):
        """对标 test_单份上限装得下实测最大的脚本。

        实测最大 6386 output token（2026-08-28，MCP 域两轮 × 6 批）。
        留 1.5 倍余量：这套评审**本来就不确定**（`_NO_SAMPLING_PARAMS` 摘掉了
        `temperature=0`），同一个域两趟写的长度不一样，按观测最大值贴边定必然常态截断。
        谁重新量了要改 `MEASURED_MAX_OUTPUT_TOKENS`，这条会逼他把上限一起改。
        """
        assert qr.MAX_OUTPUT_TOKENS >= qr.MEASURED_MAX_OUTPUT_TOKENS * 1.5
        # 也别虚高到没意义 —— 上限本身还要受 MIN_TIMEOUT_SECONDS 的墙钟约束
        assert qr.MAX_OUTPUT_TOKENS <= qr.MEASURED_MAX_OUTPUT_TOKENS * 3


class TestCollect:
    CATALOG = {
        "domains": [{"code": "AGT", "name": "Agent"}, {"code": "AUT", "name": "权限"}],
        "scenarios": [
            {"id": "AGT-01", "domain": "AGT", "priority": "P2", "risk": 2,
             "scripts": [{"path": "b.sh"}]},
            {"id": "AGT-02", "domain": "AGT", "priority": "P0", "risk": 9,
             "scripts": [{"path": "a.sh"}]},
            {"id": "AUT-01", "domain": "AUT", "priority": "P0", "risk": 9,
             "scripts": [{"path": "z.sh"}]},
        ],
    }

    def test_只取这个域(self):
        info, scenarios, paths = qr.collect(self.CATALOG, "AGT")

        assert info["name"] == "Agent"
        assert [s["id"] for s in scenarios] == ["AGT-01", "AGT-02"]
        assert "z.sh" not in paths

    def test_脚本按P0优先排(self):
        """脚本正文有总量上限，截断得截在不重要的那头。"""
        _, _, paths = qr.collect(self.CATALOG, "AGT")

        assert paths == ["a.sh", "b.sh"]

    def test_清单里没有的域回空场景(self):
        info, scenarios, paths = qr.collect(self.CATALOG, "ZZZ")

        assert scenarios == [] and paths == []
        assert info["code"] == "ZZZ"


def test_评审模块不写QA仓():
    """封样：这个模块只读文本、只写本库。出现任何写远端的 git 子命令都是 bug。"""
    import pathlib
    src = pathlib.Path(qr.__file__).read_text(encoding="utf-8")

    for bad in ("_run_git", "git push", "subprocess", "worktree", "checkout"):
        assert bad not in src, f"{bad} 不该出现在域评审模块里"


class TestBrief:
    """人话那一段。它的失败模式跟细节那边不一样：**空着比错着更坏**。

    「给人看」那一页空白，人不会去点隔壁那页 —— 他只会得出"这个域没问题"。
    所以模型不给 brief 时必须退回 summary，绝不能留空。
    """

    def test_模型给了就原样收下(self):
        out = qr.parse_result('{"verdict":"bad","brief":{"headline":"标着已覆盖的有 5 条 P0 这次一条都没跑",'
                              '"points":["a","b"],"nextStep":"先把环境补齐"},"summary":"s"}')

        assert out["brief"]["headline"].startswith("标着已覆盖")
        assert out["brief"]["points"] == ["a", "b"]
        assert out["brief"]["nextStep"] == "先把环境补齐"

    def test_模型没给就退回summary而不是留空(self):
        out = qr.parse_result('{"verdict":"ok","summary":"这个域的脚本普遍有读回"}')

        assert out["brief"]["headline"] == "这个域的脚本普遍有读回"

    def test_老记录读出来也要有brief(self):
        """**存的时候兜底不够，读的时候还得兜一次。**

        brief 是后加的字段，库里那些老 result 根本没有它，parse 那道兜底对它们
        从来没执行过。实测就这么踩了：老记录读出来 brief={}，页面「给人看」那页
        整版空白 —— 正是这个字段要防的那件事。
        """
        assert qr.brief_of({"verdict": "risky", "summary": "老记录只有 summary"}) == {
            "headline": "老记录只有 summary", "points": [], "nextStep": "", "solid": []}
        assert qr.brief_of(None)["headline"] == ""

    def test_人话那段是怎么来的四态不许并(self):
        """`briefSource`。**2026-08-29 验收跑 TEM 时撞出来的活体缺陷。**

        收口那一趟（把各批结论收成一段人话）被网关限流打成空响应，代码按设计
        退回拼接版 —— 明细 14 条脚本缺口 + 6 条清单缺口一条没少，可「给人看」
        那一页只剩一句 120 字的概述，重点 / 下一步 / 撑得住的部分**全是空的**。
        读的人得出的是「这个域没什么重点」，不是「总结那一趟没跑成」。
        **退回本身没错，不盖戳才是错** —— 和这个模块要抓的
        「跑绿了但没验到」是同一个病，它自己又犯了一次。
        """
        assert qr.brief_source_of({"briefSource": "merged"}) == "merged"
        assert qr.brief_source_of({"briefSource": "stitched"}) == "stitched"
        assert qr.brief_source_of({"briefSource": "single"}) == "single"
        # 认不出的值一律当没记：宁可多说一句"不知道"，也不谎报一个"跑成了"
        assert qr.brief_source_of({"briefSource": "ok"}) is None

    def test_存量没这个键不许折成收口跑成了(self):
        """老记录里同样混着收口挂过的那些，折进去就是把「不知道」渲染成「跑成了」。"""
        assert qr.brief_source_of({"verdict": "ok", "summary": "老记录"}) is None
        assert qr.brief_source_of({}) is None
        assert qr.brief_source_of(None) is None

    def test_points给成一句话也吃得下(self):
        out = qr.parse_result('{"verdict":"ok","brief":{"points":"就一条"},"summary":"s"}')

        assert out["brief"]["points"] == ["就一条"]

    def test_最多留三条(self):
        out = qr.parse_result('{"verdict":"ok","brief":{"points":["1","2","3","4","5","6"]},"summary":"s"}')

        # 3 条封顶：24 个域挨个看，每多一条就是多一屏
        assert len(out["brief"]["points"]) == 3


class TestMarkdown:
    """导出的那份 Markdown —— **QA 那边取结论的唯一形态**（我们只生成，他自己来拉）。

    盯的是"两周后还能不能复核"：评的是哪个 commit、在哪个环境上评的、
    哪几份脚本进了模型。少一样，这份结论就没法判断还算不算数。
    """

    def _review(self, **kw):
        from datetime import datetime, timezone

        from app.models.qa_catalog_review import QaCatalogReview
        r = QaCatalogReview(
            domain="MCP", domain_name="MCP 能力", status="done",
            environment_name="uag-138:3000", commit_sha="dae9b4fc41501345f5a8", branch="main",
            actor="admin", scenario_count=53, script_count=2,
            result={
                "verdict": "risky", "summary": "细节给动手的人看",
                "brief": {"headline": "已覆盖打了折", "points": ["P0 有 5 条这次没执行"],
                          "nextStep": "补环境变量"},
                "scriptGaps": [{"id": "MCP-04", "path": "api/mcp/x.sh", "severity": "blocker",
                                "problem": "只断了不含明文", "evidence": "assert_not_contains \"$SECRET\"",
                                "fix": "补一条读回断言"}],
                "envMissing": [{"name": "UAG_APIKEY", "scripts": ["config/env.sh"]}],
                "catalogGaps": [], "nextUp": [{"id": "MCP-05", "why": "P0 R=9"}],
                "reviewedScripts": [{"path": "api/mcp/x.sh", "truncated": False}],
                "scenarioCount": 53,
            },
        )
        r.created_at = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
        for k, v in kw.items():
            setattr(r, k, v)
        return r

    def test_拼接版要在人话那一节里当场说(self):
        """**不能只写在末尾的免责清单里。** 上面那三行空着的时候，读的人已经得出
        「这个域没什么重点」了，翻到末尾再看到一句解释也改不回来。
        """
        r = self._review()
        r.result = {**r.result, "briefSource": "stitched",
                    "brief": {"headline": "一句概述", "points": [], "nextStep": "", "solid": []}}
        head = qr.to_markdown(r).split("## 我是怎么看的")[0]

        assert "拼接版" in head
        assert "不是这个域没有重点" in head

    def test_收口跑成了就不说这句(self):
        r = self._review()
        r.result = {**r.result, "briefSource": "merged"}

        assert "拼接版" not in qr.to_markdown(r)

    def test_单批也不算收口跑成了但同样不用报警(self):
        """只有一批本来就没有收口这一步 —— 既不是 `merged` 也不该吓唬人。"""
        r = self._review()
        r.result = {**r.result, "briefSource": "single"}
        md = qr.to_markdown(r)

        assert "拼接版" not in md
        assert "没记「人话那段是怎么来的」" not in md

    def test_存量结论导出要说这一趟没记(self):
        """老记录的 brief 可能本来就是拼接来的，看不出来 —— 那就说"没记"，别装作跑成了。"""
        assert "没记「人话那段是怎么来的」" in qr.to_markdown(self._review())

    def test_带齐复核这份结论要的三样(self):
        md = qr.to_markdown(self._review())

        assert "dae9b4fc41" in md          # 评的是哪个 commit
        assert "uag-138:3000" in md        # 在哪个环境上评的
        assert "api/mcp/x.sh" in md        # 哪几份脚本进了模型

    def test_判据锚点原样带出去(self):
        """接手的人拿它直接 grep 定位；没有它，"断言不够"就只是一句评价。"""
        md = qr.to_markdown(self._review())

        assert 'assert_not_contains "$SECRET"' in md

    def test_老记录导出也不能是空的人话页(self):
        r = self._review()
        r.result.pop("brief")

        assert "细节给动手的人看" in qr.to_markdown(r)

    def test_人话和细节都在同一份里(self):
        md = qr.to_markdown(self._review())

        assert "已覆盖打了折" in md and "细节给动手的人看" in md

    def test_明写只读(self):
        """这份东西会流到 QA 那边去。他必须一眼看见平台没动过他的仓库。"""
        md = qr.to_markdown(self._review())

        assert "没有对该仓库做任何写操作" in md
        assert "建议" in md and "门禁" in md

    def test_截断的脚本要标出来(self):
        r = self._review()
        r.result["reviewedScripts"] = [{"path": "a.sh", "truncated": True}]

        assert "已截断" in qr.to_markdown(r)


class TestCoverage:
    """**截了多少，必须报出来。**

    MCP 域实测：75 条场景只有 60 条进了 prompt、14 份脚本只读进 11 份，
    而页面上写的是「场景 75 条」—— 读的人只会以为这 75 条都评过了。
    额度有限、先给 P0/高风险，这没问题；**把截断说成全量才是问题**。
    """

    def _md(self, coverage, **res):
        from datetime import datetime, timezone

        from app.models.qa_catalog_review import QaCatalogReview
        r = QaCatalogReview(
            domain="MCP", domain_name="MCP 能力", status="done",
            environment_name="uag-138:3000", commit_sha="0da11d75fa", branch="main",
            actor="admin", scenario_count=75, script_count=11,
            result={"verdict": "bad", "summary": "s", "brief": {"headline": "h"},
                    "reviewedScripts": [], "coverage": coverage, **res},
        )
        r.created_at = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
        return qr.to_markdown(r)

    def test_场景被截了要在导出里明说(self):
        md = self._md({"scenariosTotal": 75, "scenariosShown": 60,
                       "scriptsTotal": 11, "scriptsRead": 11, "scriptsTruncated": 0})
        assert "75" in md and "60" in md
        assert "15 条这次没评" in md

    def test_脚本没读全也要明说(self):
        md = self._md({"scenariosTotal": 20, "scenariosShown": 20,
                       "scriptsTotal": 14, "scriptsRead": 11, "scriptsTruncated": 7})
        assert "14 份脚本" in md and "11 份" in md

    def test_没截断就别吓唬人(self):
        """**没截的时候一个字都不该冒出来。** 每份结论都挂个 ⚠，读的人两天就学会跳过它。"""
        md = self._md({"scenariosTotal": 20, "scenariosShown": 20,
                       "scriptsTotal": 3, "scriptsRead": 3, "scriptsTruncated": 0})
        assert "这次没评" not in md and "没读到的那几份" not in md

    def test_老记录没有coverage也不能炸(self):
        """coverage 也是后加的字段，库里那批老结论里没有 —— 导出不能因此 500。"""
        md = self._md(None)
        assert "MCP" in md and "这次没评" not in md


class TestBlame:
    """**「谁动手」必须由结论自己带，别让读的人自己分栏。**

    上一轮实测就是这个坑：MCP 域 6 条问题里有 2 条根子是「我们这条环境记录里
    没有 UAG_APIKEY」，跟 QA 的脚本一点关系没有 —— 混在同一张表里，看的人
    先把它当成"人家脚本写得不行"，理解半天才反应过来是配置的事。
    更糟的是，这份变量名单只反映**我们这侧**记着什么，**推不出** QA 自己跑的时候也缺。
    """

    def test_认不出来的落script(self):
        """宁可多审一条，也别把该发给仓库主人的漏进"不是你的事"那堆里。"""
        g = qr.by_blame([{"id": "A"}, {"id": "B", "blame": "什么鬼"}, {"id": "C", "blame": "env"}])
        assert [x["id"] for x in g["script"]] == ["A", "B"]
        assert [x["id"] for x in g["env"]] == ["C"]
        assert g["catalog"] == []

    def test_parse认blame白名单(self):
        out = qr.parse_result('```json{"verdict":"risky","scriptGaps":['
                              '{"id":"X","blame":"env"},{"id":"Y","blame":"胡说"},{"id":"Z"}]}```')
        assert [x["blame"] for x in out["scriptGaps"]] == ["env", "script", "script"]

    def test_导出按谁动手分栏且环境那类不写成脚本的错(self):
        from datetime import datetime, timezone

        from app.models.qa_catalog_review import QaCatalogReview
        r = QaCatalogReview(
            domain="MCP", domain_name="MCP 能力", status="done",
            environment_name="uag-138:3000", commit_sha="0da11d75fa", branch="main",
            actor="admin", scenario_count=75, script_count=11,
            result={
                "verdict": "risky", "summary": "s",
                "brief": {"headline": "h", "points": ["p"], "solid": ["接口层那几条断言是硬的"]},
                "scriptGaps": [
                    {"id": "MCP-41", "blame": "script", "severity": "major",
                     "problem": "反面断言恒真", "path": "a.sh"},
                    {"id": "MCP-28", "blame": "env", "severity": "blocker",
                     "problem": "没有数据面 apikey，整条 skip", "path": "b.sh"},
                ],
                "envMissing": [{"name": "UAG_APIKEY", "scripts": ["config/env.sh"]}],
                "reviewedScripts": [], "coverage": None,
            },
        )
        r.created_at = datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc)
        md = qr.to_markdown(r)
        assert "QA 的脚本要改（1 条）" in md
        assert "不是脚本的问题：环境没铺东西（1 条）" in md
        # 结论词后面必须跟释义，光一个词读的人各猜各的
        assert "部分认领不算数" in md and "断言太松" in md
        # 凭什么这么说 —— 别人第一个念头
        assert "这条断言能不能失败" in md and "为什么一份都没跑" in md
        # 撑得住的部分要说出来，整页只有坏消息读的人会当成全域不能用
        assert "接口层那几条断言是硬的" in md
        # 环境那一列不是对 QA 的意见
        assert "不是脚本的问题" in md and "QA 自己的 runner 里有没有，平台看不到" in md


class TestBatching:
    """脚本装不下的时候，是**分批读完**还是**截掉多的**。

    这两件事在页面上长得一样，都显示成"评过了"。截掉的那版实测：
    MCP 域 47 份脚本只进去 11 份，8 份还被截了正文 —— 而"没读到"的地方
    模型不下结论，于是呈现出来就是"没发现问题"。
    """

    @staticmethod
    def _mk(n: int, size: int) -> list[dict]:
        return [{"path": f"s{i}.sh", "content": "x" * size, "truncated": False}
                for i in range(n)]

    def test_一份都不许丢(self):
        scripts = self._mk(30, 20_000)          # 600KB，怎么切都得切
        batches = qr.split_batches(scripts)
        assert len(batches) > 1
        got = [s["path"] for b in batches for s in b]
        assert got == [s["path"] for s in scripts]      # 顺序和份数都不变

    def test_装得下就一批(self):
        assert len(qr.split_batches(self._mk(3, 1_000))) == 1

    def test_没有脚本也回一批空的(self):
        # 回 [] 的话 run_review 一次都不调，这个域会静悄悄地"评完了"
        assert qr.split_batches([]) == [[]]

    def test_单份超预算也单独成批(self):
        # 一份就比 BATCH_SCRIPT_BYTES 还大：得让它自己占一批，不能因为塞不下就丢
        big = [{"path": "big.sh", "content": "x" * (qr.BATCH_SCRIPT_BYTES + 5_000),
                "truncated": False},
               {"path": "small.sh", "content": "y", "truncated": False}]
        batches = qr.split_batches(big)
        assert [s["path"] for b in batches for s in b] == ["big.sh", "small.sh"]

    def test_take_scripts_能产出的任何输入都一份不丢(self):
        """★#9。原来这里是「丢了要报出来」—— 那是给一个不会发生的事件加仪表。

        真正要钉的是**永不丢**：`split_batches` 从前有一句
        `if len(out) >= MAX_BATCHES: break`，超了就无声地把剩下的脚本扔掉，
        而 `scriptsRead` 数的是从 git 读到的份数 —— 页面照样写「N 份全读了」。
        它已经删了。这条测试是防它被谁"顺手加回来省额度"（它一分钱也没省）。
        """
        for size in (1, 500, 5_000, 12_001, 15_001, qr.MAX_SCRIPT_BYTES):
            paths = [f"s{i}.sh" for i in range(qr.MAX_SCRIPTS)]
            scripts = qr.take_scripts(lambda p, _sz=size: "x" * _sz, paths)
            batches = qr.split_batches(scripts)
            got = [c["path"] for b in batches for c in b]
            assert got == [c["path"] for c in scripts], f"size={size} 丢了脚本"
            # 顺带：真实输入下批数根本够不着封顶（★#9b 是它的常量版证明）
            assert len(batches) <= qr.MAX_BATCHES, f"size={size} 批数 {len(batches)}"

    def test_批数封顶够不着所以不会静默丢(self):
        """★#9b。把一个**隐形的常量耦合**变成一句会红的断言。

        贪心装箱下，除最后一批外每批都装了 > (BATCH - 单份上限) 字节
        （否则下一份就该塞进来了）。所以 B 批需要总量
        > (B-1) × (BATCH_SCRIPT_BYTES - MAX_SCRIPT_BYTES)，
        而总量被 TOTAL_SCRIPT_BYTES 钉住 ⇒ 撞到 MAX_BATCHES 需要
        (MAX_BATCHES-1) × 72_000 = 504_000 字节，预算只有 480_000 ⇒ **够不着**。

        今天不丢靠的就是这个 24_000 字节的余量，而**没有任何东西写着这件事**。
        谁把 TOTAL_SCRIPT_BYTES 调过 504_000 而没动 MAX_BATCHES，
        静默丢当场开始 —— 现在这条会先替他红一次。
        （MCP 域脚本数已从 47 长到 49，调大预算不是假想的改动。）
        """
        assert (qr.MAX_BATCHES - 1) * (qr.BATCH_SCRIPT_BYTES - qr.MAX_SCRIPT_BYTES) \
            >= qr.TOTAL_SCRIPT_BYTES

    def test_单份上限装得下实测最大的脚本(self):
        # 2026-08-27 量过 uag-qa 109 份：中位数 5.3KB、p90 11.6KB、最大 17.7KB。
        # 这个数掉回 6000 的话，全仓一半的脚本会被截 —— 而截断的不下结论。
        assert qr.MAX_SCRIPT_BYTES >= 18_000

    def test_合批取最坏的结论(self):
        merged = qr.merge_results([
            {"verdict": "ok", "scriptGaps": [], "catalogGaps": []},
            {"verdict": "bad", "scriptGaps": [], "catalogGaps": []},
            {"verdict": "risky", "scriptGaps": [], "catalogGaps": []},
        ])
        # 平均一下会把最要命的那批稀释掉：5 批里 1 批「多数认领不算数」，整个域就不能当数
        assert merged["verdict"] == "bad"

    def test_合批去重且按严重度排(self):
        same = {"id": "MCP-07", "path": "a.sh", "problem": "没读回来确认",
                "severity": "minor", "blame": "script"}
        merged = qr.merge_results([
            {"verdict": "risky", "scriptGaps": [dict(same)], "catalogGaps": []},
            {"verdict": "risky", "catalogGaps": [],
             "scriptGaps": [dict(same),
                            {"id": "MCP-11", "path": "b.sh", "problem": "断言恒真",
                             "severity": "blocker", "blame": "script"}]},
        ])
        assert len(merged["scriptGaps"]) == 2
        assert merged["scriptGaps"][0]["id"] == "MCP-11"     # blocker 排前面

    def test_分批的payload要说清这是第几批(self):
        md = qr.build_payload({"code": "MCP", "name": "MCP 能力"},
                              [{"id": "MCP-01", "title": "t"}],
                              self._mk(1, 10), "e", [], [], batch=(2, 5))
        assert "第 2 批" in md and "切成了 5 批" in md
        # 最关键的一句：别对没给你的脚本下"没验到"的结论
        assert "别对没给你的脚本说" in md

    def test_不分批时不提批次(self):
        md = qr.build_payload({"code": "MCP"}, [], self._mk(1, 10), "e", [], [])
        assert "批" not in md

    def test_markdown要说清是全读了还是抽的(self):
        from datetime import datetime, timezone

        from app.models.qa_catalog_review import QaCatalogReview
        r = QaCatalogReview(
            domain="MCP", domain_name="MCP 能力", status="done", environment_name="e",
            commit_sha="abc", branch="main", actor="a", scenario_count=75, script_count=47,
            result={"verdict": "risky", "summary": "s",
                    "brief": {"headline": "h", "points": [], "solid": []},
                    "scriptGaps": [], "envMissing": [], "reviewedScripts": [],
                    "coverage": {"scenariosTotal": 75, "scenariosShown": 75,
                                 "scriptsTotal": 47, "scriptsRead": 47,
                                 "scriptsBatched": 47,
                                 "scriptsTruncated": 0, "batches": 5}},
        )
        r.created_at = datetime(2026, 8, 27, tzinfo=timezone.utc)
        md = qr.to_markdown(r)
        # 「全进了模型」这句话现在有依据了：scriptsBatched == scriptsRead。
        # 原来它是**无条件**输出的 —— 读的人无法区分"核过"和"照着模板写的"。
        assert "分 5 批" in md and "47 份全进了模型" in md
        # 全读完了就不许再挂"只读进 N 份"那种警告
        assert "只读进" not in md


class TestMergePayloadCounts:
    """收口那一步的数字：让模型自己数，就会数出跟页面对不上的数。

    实测撞到过：brief 写「清单要商量：17 处」，页面那一栏底下只列 1 条 ——
    17 数的是 catalogGaps，1 数的是 scriptGaps 里 blame=catalog 的。
    两个数都对，摆在同一屏里就是打架。所以条数由代码算好喂进去，模型只抄。
    """

    def _merged(self):
        return {
            "verdict": "risky",
            "scriptGaps": [
                {"id": "X-01", "blame": "script", "severity": "major", "oneLine": "a"},
                {"id": "X-02", "severity": "minor", "oneLine": "b"},          # 缺 blame → 算 script
                {"id": "X-03", "blame": "env", "severity": "major", "oneLine": "c"},
                {"id": "X-04", "blame": "catalog", "severity": "minor", "oneLine": "d"},
            ],
            "catalogGaps": [{"scenario": "s1", "why": "w1"}, {"scenario": "s2", "why": "w2"}],
        }

    def test_三堆的条数要算好喂给模型(self):
        txt = qr._merge_payload({"code": "MCP", "name": "MCP 能力"}, self._merged(), 5, 47)
        assert "- 脚本要改：2 条" in txt          # 含那条没写 blame 的
        assert "- 环境要铺：1 条" in txt
        # 清单那堆 = blame=catalog(1) + catalogGaps(2)，页面上摆在一起就得一起数
        assert "- 清单要商量：3 条" in txt

    def test_收口的指令要禁止自己数(self):
        assert "不许自己数" in qr._MERGE_SYSTEM


class TestPartialBatchFailure:
    """一批挂掉不许把另外几批读到的东西一起扔了，但**必须说出来**。

    实测撞到过：5 批里 3 批同吃网关 429（那次降级通道没加载到），
    gather 抛出去，已读完的两批结果跟着没了，页面只剩一句"评审失败"。
    """

    def _rec(self, cov):
        from datetime import datetime, timezone

        from app.models.qa_catalog_review import QaCatalogReview
        r = QaCatalogReview(
            domain="MCP", domain_name="MCP 能力", status="done",
            environment_name="e", commit_sha="abc123", branch="main", actor="cc",
            scenario_count=75, script_count=47,
            result={"verdict": "risky", "summary": "x", "scriptGaps": [], "envMissing": [],
                    "reviewedScripts": [], "coverage": cov},
        )
        r.created_at = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)
        return r

    def test_有批次没读成要在markdown里说出来(self):
        r = self._rec({
            "scenariosTotal": 75, "scenariosShown": 75, "scriptsTotal": 47,
            "scriptsRead": 47, "scriptsTruncated": 0, "batches": 5, "batchesFailed": [2, 4],
        })
        md = qr.to_markdown(r)
        assert "第 2 批" in md and "第 4 批" in md
        assert "没读成" in md
        # 「没抓到问题」不能被当成「没问题」
        assert "不包括它们" in md

    def test_全读成了就不提批次失败(self):
        r = self._rec({
            "scenariosTotal": 10, "scenariosShown": 10, "scriptsTotal": 3,
            "scriptsRead": 3, "scriptsTruncated": 0, "batches": 2, "batchesFailed": [],
        })
        assert "没读成" not in qr.to_markdown(r)


class TestDims:
    """维度：一个域几十条明细没人读，人要的是「哪一块塌了」。

    大维度只用人认得的行话（**覆盖面 / 场景设置 / 断言**）—— 上一版摆的是六条查法
    （「断言能不能失败」「一条认领拆没拆开」），实测反馈就一句「我咋看不懂」。
    条数**必须代码数**（模型只负责给每条打 `dim`）—— 让模型自己数，
    页面上就会出现「清单 17 处」底下只列 1 条那种自相矛盾。
    """

    def _res(self, **kw):
        base = {"verdict": "risky", "summary": "x", "scriptGaps": [], "catalogGaps": [],
                "envMissing": [], "reviewedScripts": [], "coverage": {}}
        base.update(kw)
        return base

    def test_两个来源都算进维度(self):
        rows = qr.dim_flat(self._res(
            scriptGaps=[{"dim": "assert"}, {"dim": "assert"}, {"dim": "skip"}],
            catalogGaps=[{"dim": "grain"}, {"dim": "assert"}]))
        n = {r.get("key"): r["count"] for r in rows if r["level"] == 1}
        assert n["assert"] == 3          # 2 条脚本 + 1 条清单
        assert n["skip"] == 1 and n["grain"] == 1
        assert n["both"] == 0 and n["depth"] == 0

    def test_三个大维度的数是子项之和_不许各算一套(self):
        rows = qr.dim_rollup(self._res(
            scriptGaps=[{"dim": "assert"}, {"dim": "depth"}, {"dim": "skip"}],
            catalogGaps=[{"dim": "coverage"}]))
        n = {r["axis"]: r["count"] for r in rows}
        assert n["assertion"] == 2       # assert + depth
        assert n["cover"] == 2           # skip + coverage
        assert n["design"] == 0
        for ax in rows:
            assert ax["count"] == sum(i["count"] for i in ax["items"]) or not ax["items"]

    def test_大维度用人认得的词_不是我的查法(self):
        names = [r["name"] for r in qr.dim_rollup(self._res())]
        assert names == ["覆盖面", "场景设置", "断言"]   # 没有 other 这一行

    def test_维度顺序固定_便于横着比二十四个域(self):
        keys = [r["key"] for r in qr.dim_flat(self._res()) if r["level"] == 1]
        assert keys == list(qr.DIM_KEYS)

    def test_没归维度的落其它这一格_不许静默塞进某一维(self):
        rows = qr.dim_flat(self._res(scriptGaps=[{"dim": "assert"}, {}, {"dim": "瞎编的"}]))
        n = {r.get("key") or r.get("axis"): r["count"] for r in rows}
        assert n["assert"] == 1 and n["other"] == 2

    def test_旧结论的六个key还认_别把存量渲染成一堆其它(self):
        """`coverage`/`shape`/`expect` 是后加的；`dim` 刚上线那一批只有这六个 key，
        它们必须照旧各归各位 —— 否则存量 24 个域全渲染成「其它」。"""
        old = ["assert", "claim", "skip", "both", "depth", "grain"]
        rows = qr.dim_flat(self._res(scriptGaps=[{"dim": d} for d in old]))
        n = {r.get("key") or r.get("axis"): r["count"] for r in rows}
        assert all(n[d] == 1 for d in old)
        assert "other" not in n

    def test_加维度之前评的旧结论_新那几条要标没查不是零(self):
        """0 和「压根没查」在表里长得一样。旧结论（`dimSpec` 缺失或 <2）里
        `coverage`/`shape`/`expect` 模型没被问过 —— 摆个 0 就是假安心。"""
        old = qr.dim_flat(self._res(scriptGaps=[{"dim": "assert"}]))          # 没有 dimSpec
        u = {r["key"]: r["unavailable"] for r in old if r["level"] == 1}
        assert u["coverage"] and u["shape"] and u["expect"]
        assert not u["assert"] and not u["skip"] and not u["both"]
        fresh = qr.dim_flat(self._res(dimSpec=qr.DIM_SPEC, scriptGaps=[{"dim": "assert"}]))
        assert not any(r["unavailable"] for r in fresh if r["level"] == 1)

    def test_旧结论导出的报告要写明那几项没查(self):
        r = TestPartialBatchFailure()._rec({"scriptsRead": 5, "batches": 1})
        r.result["scriptGaps"] = [{"dim": "assert", "blame": "script", "id": "MCP-1"}]
        r.result.pop("dimSpec", None)
        md = qr.to_markdown(r)
        assert "这一趟没查" in md and "重评一次这个域就补上了" in md

    def test_markdown里有维度表且写明零条不等于没问题(self):
        r = TestPartialBatchFailure()._rec({"scriptsRead": 5, "batches": 1})
        r.result["scriptGaps"] = [{"dim": "assert", "blame": "script", "id": "MCP-1"}]
        md = qr.to_markdown(r)
        assert "按维度看" in md
        for d in qr.dim_flat(r.result):
            assert d["name"] in md
        # 0 条最容易被读成"这一块过了"，报告里必须先堵掉
        assert "不等于那一块没问题" in md


class TestDimWhitelist:
    """S8.2：维度按数组分白名单。

    两个数组的差别不是格式，是**收件人**：`scriptGaps` 发给写脚本的人，
    `catalogGaps` 发给清单主人。`coverage`（清单里就没有这条场景）落进 `scriptGaps`，
    等于给写脚本的人派一件他一句代码都改不了的活 —— 他合理地不处理，
    然后这条缺口就在"已提出"的状态里躺着，谁都没错。
    """

    def _out(self, script=(), catalog=()):
        import json
        # S8.1：清单侧的结论指不出出处会被**整条丢掉**。这一组测的是维度，
        # 不是锚点 —— 不给默认出处的话每条夹具都在闸门那儿就没了，
        # 底下的断言全变成 IndexError，红得跟维度一点关系都没有。
        # `{**默认, **c}` 留了口子：调用方给空 `evidence` 就能测丢弃那条路。
        cat = [{"evidence": 'assert_status 200 "$resp"', **c} for c in catalog]
        return qr.parse_result(json.dumps({
            "verdict": "bad", "summary": "x",
            "scriptGaps": list(script), "catalogGaps": cat}))

    # ── #28 ──
    def test_脚本那一堆不许落coverage(self):
        out = self._out(script=[{"id": "A-1", "dim": "coverage"}])

        assert out["scriptGaps"][0]["dim"] == "other"
        assert out["scriptGaps"][0]["dimRaw"] == "coverage"
        assert qr.dim_coerced(out) == 1

    # ── #29 ──
    def test_清单那一堆照样能落coverage(self):
        """**这条是 #28 的另一半，缺了它 #28 就会被"整删 coverage"糊弄过去。**

        对 `scriptGaps` 该拦的理由完全成立，对 `catalogGaps` 完全不成立：
        那是它唯一的归宿，删了「缺了删除后的越权访问」只能硬塞进 `shape`。
        """
        out = self._out(catalog=[{"scenario": "缺删除后越权", "dim": "coverage"}])

        assert out["catalogGaps"][0]["dim"] == "coverage"
        assert "dimRaw" not in out["catalogGaps"][0]
        assert qr.dim_coerced(out) == 0
        # **"没被改判"还不够。** 只断这一条的话，把 `coverage` 从维度表里整删掉
        # 照样绿：白名单还放行它，只是它不再是一个维度了 —— 页面上那条落进「其它」，
        # 而 `dimCoerced` 是 0，看起来一切正常。所以还得断它**确实还是覆盖面下面那一格**。
        assert "coverage" in qr.DIM_KEYS
        cover = [a for a in qr.dim_rollup(out) if a["axis"] == "cover"][0]
        assert cover["count"] == 1
        assert [i["count"] for i in cover["items"] if i["key"] == "coverage"] == [1]

    def test_清单那一堆不许落断言维度(self):
        """反过来也得拦。断言怎么写是脚本正文的事，清单管不着 ——
        `assert` 落进 `catalogGaps` 会让人拿着它去找清单主人商量断言写法。"""
        out = self._out(catalog=[{"scenario": "x", "dim": "assert"}])

        assert out["catalogGaps"][0]["dim"] == "other"
        assert out["catalogGaps"][0]["dimRaw"] == "assert"

    def test_两边都合法的维度两边都不动(self):
        """`grain` / `shape` 是真的两边都成立：一条清单说了三件事，
        既可以说清单粒度粗，也可以说脚本只兑现了其中一件。

        **两个维度都得在两个数组里各试一遍。** 只拿 `grain` 试 `scriptGaps`、
        拿 `shape` 试 `catalogGaps` 的话，把 `grain` 砍成 script-only 是全绿的 ——
        白名单窄了一格没人知道，直到清单主人那边的 `grain` 开始成片落进「其它」。
        """
        for dim in ("grain", "shape"):
            out = self._out(script=[{"id": "A-1", "dim": dim}],
                            catalog=[{"scenario": "x", "dim": dim}])

            assert out["scriptGaps"][0]["dim"] == dim, dim
            assert out["catalogGaps"][0]["dim"] == dim, dim
            assert qr.dim_coerced(out) == 0, dim

    def test_越界的原话必须留住(self):
        """抹掉原话，页面上就只剩一个「其它」—— 没人查得出模型当时说的是什么，
        也就没人知道该去修提示词还是修白名单。"""
        out = self._out(script=[{"id": "A-1", "dim": "coverage"}])

        assert out["scriptGaps"][0]["dimRaw"] == "coverage"

    def test_不许往最近的合法维度上靠(self):
        """`coverage` 越界了不许改判成 `both`/`skip` 这些近的。
        猜一个近的等于替模型做判断，而猜对和猜错在页面上长得一模一样。"""
        out = self._out(script=[{"id": "A-1", "dim": "coverage"}])

        assert out["scriptGaps"][0]["dim"] == "other"

    def test_模型压根没给维度的不算越界(self):
        """没给维度 → rollup 自己会归进「其它」。在这儿也记一笔的话，
        `dimCoerced` 就同时在数两件事，那个数就没法照着改任何东西了。"""
        out = self._out(script=[{"id": "A-1"}, {"id": "A-2", "dim": ""}])

        assert qr.dim_coerced(out) == 0
        assert all("dimRaw" not in g for g in out["scriptGaps"])
        # **还得直接问 `coerce_dim` 一句。** 上面两条断言隔着调用方：空维度就算被
        # 判成越界，留档那一格也是空的，`if raw:` 当场把它拦下 —— 于是守卫整条删掉
        # 照样全绿。信号是**透过参数传导**到断言的，得把参数固定住单独问一次。
        assert qr.coerce_dim("", "script") == ("", "")
        assert qr.coerce_dim("   ", "catalog") == ("", "")

    def test_瞎编的维度也算越界(self):
        out = self._out(script=[{"id": "A-1", "dim": "瞎编的"}])

        assert out["scriptGaps"][0]["dim"] == "other"
        assert qr.dim_coerced(out) == 1

    def test_这个数是代码数的_不是模型报的(self):
        """同 `evidence_stats` 的纪律：模型报一份、代码算一份，
        两条路径哪天分歧了没人看得出来是哪边错。"""
        import json
        out = qr.parse_result(json.dumps({
            "verdict": "bad", "dimCoerced": 99,
            "scriptGaps": [{"id": "A-1", "dim": "coverage"}], "catalogGaps": []}))

        assert qr.dim_coerced(out) == 1

    def test_两个数组的越界都算进这个数(self):
        out = self._out(script=[{"id": "A-1", "dim": "coverage"}],
                        catalog=[{"scenario": "x", "dim": "assert"}])

        assert qr.dim_coerced(out) == 2

    def test_每个维度都得说清自己能落哪个数组(self):
        """**加维度必须同时登记 `DIM_SIDE`。** 漏登记的维度在两个数组里都非法、
        整片被改判成「其它」—— 这条测试就是那个漏登记当场红的地方。"""
        assert set(qr.DIM_SIDE) == set(qr.DIM_KEYS)
        assert all(set(v) <= {"script", "catalog"} and v for v in qr.DIM_SIDE.values())

    def test_coverage只归清单_断言那几条只归脚本(self):
        """白名单的具体内容也封样：改动它得有人明确改这条测试。"""
        assert qr.DIM_SIDE["coverage"] == ("catalog",)
        for k in ("assert", "claim", "depth", "expect", "both", "skip"):
            assert qr.DIM_SIDE[k] == ("script",)

    def test_提示词里得写明哪个数组能用哪几个维度(self):
        """白名单只写在代码里 = 模型天天越界、天天被改判成「其它」，
        页面上一片「其它」，而模型其实是照着提示词写的。"""
        p = qr._SYSTEM
        assert "不许用 `coverage`" in p
        assert '"dim": "both|skip|grain|shape|assert|claim|depth|expect"' in p
        assert '"dim": "coverage|grain|shape"' in p

    def test_报告里得说出改判了几条(self):
        """一个不报数的 coerce 就是新造的静默行为 —— 正是这套表要堵的那种。"""
        r = TestPartialBatchFailure()._rec({"scriptsRead": 5, "batches": 1})
        r.result["scriptGaps"] = [{"dim": "other", "dimRaw": "coverage",
                                   "blame": "script", "id": "MCP-1"}]
        md = qr.to_markdown(r)

        assert "落错了数组" in md and "dimRaw" in md

    def test_没改判过就不写那一句(self):
        r = TestPartialBatchFailure()._rec({"scriptsRead": 5, "batches": 1})
        r.result["scriptGaps"] = [{"dim": "assert", "blame": "script", "id": "MCP-1"}]

        assert "落错了数组" not in qr.to_markdown(r)


class TestDimSpec3:
    """S8.3：覆盖面那一栏换血 —— `coverage` 换定义，另加 `unmet`(G3) / `blind`(G2)。

    这三条从此都是**算**出来的（页面枚举 ∩ 路由表 ∩ 清单），不是模型猜的。
    """

    def _out(self, script=(), catalog=(), spec=None):
        import json
        # S8.1：清单侧的结论指不出出处会被**整条丢掉**。这一组测的是维度，
        # 不是锚点 —— 不给默认出处的话每条夹具都在闸门那儿就没了，
        # 底下的断言全变成 IndexError，红得跟维度一点关系都没有。
        # `{**默认, **c}` 留了口子：调用方给空 `evidence` 就能测丢弃那条路。
        cat = [{"evidence": 'assert_status 200 "$resp"', **c} for c in catalog]
        d = {"verdict": "bad", "summary": "x",
             "scriptGaps": list(script), "catalogGaps": cat}
        if spec is not None:
            d["dimSpec"] = spec
        return qr.parse_result(json.dumps(d))

    # ── ★#30 ──
    def test_维度口径变了DIM_SPEC必须跟着升(self):
        """**这条是整个 Epic 8 的闸门。**

        加一条子项而不升 `DIM_SPEC`，存量结论会把「这一趟压根没查」渲染成一个
        漂亮的 0 —— 页面上那一格写着 0，读的人得到的信息是"这一维没问题"。
        **假安心比报错难发现得多**，而且它不会自己好。

        所以词表按版本钉死在这里：动 `AXES` 的键 ⇒ 这条红；
        升了 `DIM_SPEC` 却忘了给新键登记 `DIM_SINCE` ⇒ 也红
        （`DIM_SINCE.get(k, 1)` 默认 1 = "第 1 版就有"，那正是渲染成 0 的那条路）。
        """
        vocab = {
            1: {"both", "skip", "grain", "assert", "claim", "depth"},
            2: {"both", "skip", "grain", "assert", "claim", "depth",
                "coverage", "shape", "expect"},
            3: {"both", "skip", "grain", "assert", "claim", "depth",
                "coverage", "shape", "expect", "unmet", "blind"},
        }
        assert qr.DIM_SPEC == 3
        assert set(qr.DIM_KEYS) == vocab[qr.DIM_SPEC]
        for k in qr.DIM_KEYS:
            since = min(v for v, ks in vocab.items() if k in ks)
            assert qr.DIM_SINCE.get(k, 1) == since, k
        # 反过来也钉：`DIM_SINCE` 里不许有词表外的键（改名留下的孤儿会一直"生效"）
        assert set(qr.DIM_SINCE) <= set(qr.DIM_KEYS)

    def test_新那两条在旧结论上标没查不是零(self):
        """存量结论（`dimSpec: 2`）没被问过 G2/G3，那两格得是「这一趟没查」。

        这儿**直接造结论字典**，不走 `parse_result` —— 那一层压根不盖 `dimSpec`
        （盖戳的是 `review_domain`），从它出来的东西一律按第 1 版算，
        "第 2 版的存量结论"这个情形根本造不出来。
        """
        def rolled(spec):
            return {i["key"]: i
                    for a in qr.dim_rollup({"verdict": "risky", "scriptGaps": [],
                                            "catalogGaps": [], "dimSpec": spec})
                    for i in a.get("items", [])}

        old, fresh = rolled(2), rolled(3)

        assert old["unmet"]["unavailable"] and old["blind"]["unavailable"]
        assert not old["coverage"]["unavailable"]          # 第 2 版就有了
        assert not any(fresh[k]["unavailable"] for k in ("unmet", "blind", "coverage"))

    def test_G3归脚本_G2归清单_两条blame反号(self):
        """**别图省事写成"覆盖面的都归清单"。** G3 是清单认领了、脚本没写
        （账在脚本），G2 是路由表里有、两头都没人管（账在清单）。"""
        assert qr.DIM_SIDE["unmet"] == ("script",)
        assert qr.DIM_SIDE["blind"] == ("catalog",)

    def test_这两条只许代码写_模型写了一律改判(self):
        """模型手上没有页面枚举、路由表、清单这三份输入，它写出来的 `unmet`
        只能是猜的 —— 而猜出来的和算出来的摆在页面上长得一模一样，
        还更可信，因为它带着一个像事实的维度名。"""
        out = self._out(script=[{"id": "A-1", "dim": "unmet"}],
                        catalog=[{"scenario": "x", "dim": "blind"}])

        assert out["scriptGaps"][0]["dim"] == "other"
        assert out["scriptGaps"][0]["dimRaw"] == "unmet"
        assert out["catalogGaps"][0]["dim"] == "other"
        assert out["catalogGaps"][0]["dimRaw"] == "blind"
        assert qr.dim_coerced(out) == 2

    def test_模型写在哪个数组里都不算数(self):
        """就算写对了 `DIM_SIDE` 登记的那一侧，也照样改判 —— 这条判的是
        **谁写的**，不是**写在哪**。"""
        out = self._out(script=[{"id": "A-1", "dim": "unmet"}])      # unmet 正是 script 侧
        assert out["scriptGaps"][0]["dim"] == "other"

        out = self._out(catalog=[{"scenario": "x", "dim": "blind"}])  # blind 正是 catalog 侧
        assert out["catalogGaps"][0]["dim"] == "other"

    def test_coverage不在代码专用里(self):
        """G1 是代码找出来的，但「该补哪条场景」仍然要模型写（S8.1）。
        把 `coverage` 一起锁掉，模型就再没有任何一格能写覆盖面的问题了。"""
        assert "coverage" not in qr.DIM_CODE_ONLY
        out = self._out(catalog=[{"scenario": "缺删除后越权", "dim": "coverage"}])
        assert out["catalogGaps"][0]["dim"] == "coverage"

    def test_提示词不许给模型这两个维度(self):
        """列进提示词 = 请它猜。提示词里得**点名说不许写**，
        光是"没提到"不够：上面还写着「都套不上就挑最接近的」。"""
        p = qr._SYSTEM
        for k in qr.DIM_CODE_ONLY:
            assert k in p, k                       # 得点名
        assert "不给你用" in p
        # 但不许出现在那两行"只许用"的枚举里
        allow = [ln for ln in p.splitlines() if "只许用" in ln]
        assert allow and not any(k in ln for ln in allow for k in qr.DIM_CODE_ONLY)

    def test_提示词里那个数字得等于模型真能用的条数(self):
        """提示词里写着「九选一」。S8.3 之前这个数就是维度总数，改不错；
        现在 `DIM_KEYS` 有 11 条而模型只能用 9 条，**这个数第一次跟别的东西脱钩了**。

        写错了模型不会报错，它会自己找补 —— 数字比列表大就硬凑一个出来
        （多半是从「都套不上就挑最接近的」那句里挑），比列表小就漏掉最后几条。
        同 Epic 9 的 `test_提示词说几件就得列几件`：**前后矛盾的提示词，
        错在输出里，不在日志里。**
        """
        usable = [k for k in qr.DIM_KEYS if k not in qr.DIM_CODE_ONLY]
        cn = "零一二三四五六七八九十"

        assert len(usable) == 9                      # 变了就得有人来改这一行和提示词
        assert "%s选一" % cn[len(usable)] in qr._SYSTEM

    def test_维度名不许一个是另一个的前缀(self):
        """**这条是给命名地雷设的。**

        本文原本把 G3 叫 `claimed`，而断言轴上早就有个 `claim`（断的不是认领的
        那件事）。两个都跟"认领"有关、一字之差、**而且都是脚本侧** ——
        `DIM_SIDE` 那套白名单一条都拦不住，写错了就静默落进另一个大维度。
        所以改名 `unmet`，并把这条不变量钉在这儿：任何两个维度键，
        谁都不许是谁的前缀。
        """
        ks = list(qr.DIM_KEYS) + [qr.DIM_OTHER[0]]
        bad = [(a, b) for a in ks for b in ks if a != b and b.startswith(a)]
        assert not bad, bad


class TestHowItWorksDisclosure:
    """「你到底怎么评的、靠得住吗、为什么不跑」—— 报告里得答，别等人问。"""

    def _md(self):
        r = TestPartialBatchFailure()._rec({"scriptsRead": 47, "batches": 5})
        return qr.to_markdown(r)

    def test_结论词要说清主语是谁(self):
        assert "不是说我读了多少" in self._md()

    def test_为什么不跑要给出两条理由而不是一句没跑(self):
        md = self._md()
        assert "为什么一份都没跑" in md
        assert "不该靠跑" in md and "零信息量" in md
        # 不跑的代价也要认：跑不跑得起来这份结论判不了
        assert "跑不跑得起来，这份结论判不了" in md

    def test_可信边界要写成能被否掉而不是自夸(self):
        md = self._md()
        assert "grep" in md                    # 每条都能十秒内被否掉
        assert "没有第二意见" in md            # 单趟单模型
        assert "漏判是看不见的" in md


class TestBatchCompleteness:
    """「模型有没有把话说完」是**三态**，不是布尔。

    压成布尔的后果不是"少个字段"，是**主路一律显示成写完了**：
    2026-08-28 实测那一趟网关额度耗尽（`no upstream tokens available`），
    12 次调用全走 claude-proxy 那条 CLI 降级通道，而那条通道
    `usage` 恒 0、`finish_reason` 恒 `"stop"`、连 `max_tokens` 都不理会。
    也就是说：**默认值长得跟"正常写完了"一模一样**，而它其实是"没人知道"。
    把"没人知道"渲染成"写完了"，跟这套评审要抓的「跑绿了但没验到」是同一个病。
    """

    ALL = frozenset({"finish_reason", "prompt_tokens", "completion_tokens"})

    def _resp(self, **kw):
        from app.services.ai.llm_client import LLMResponse
        kw.setdefault("reported", self.ALL)
        kw.setdefault("prompt_tokens", 12_000)
        return LLMResponse(**kw)

    # ── #4 ──
    def test_满额那批标truncated(self):
        r = self._resp(finish_reason="length", completion_tokens=qr.MAX_OUTPUT_TOKENS)
        assert qr.batch_completeness(r) == "truncated"

    def test_anthropic协议那个词也认(self):
        # 同一件事两个协议两个词：openai 叫 length，anthropic 叫 max_tokens
        assert qr.batch_completeness(self._resp(finish_reason="max_tokens")) == "truncated"

    # ── #5 ──
    def test_结束原因说正常但token顶格也算没写完(self):
        # 只信 finish_reason 会漏：有的通道顶格了照样回 stop
        r = self._resp(finish_reason="stop", completion_tokens=qr.MAX_OUTPUT_TOKENS)
        assert qr.batch_completeness(r) == "truncated"

    def test_没顶格且结束原因正常才算写完(self):
        r = self._resp(finish_reason="stop", completion_tokens=800)
        assert qr.batch_completeness(r) == "complete"

    # ── ★#6：三态被压成布尔时唯一会红的那条 ──
    def test_两个凭据都拿不到时是说不清不是写完了(self):
        r = self._resp(reported=frozenset(), finish_reason="stop",
                       prompt_tokens=0, completion_tokens=0)
        got = qr.batch_completeness(r)
        assert got == "unknown"
        assert got != "complete"        # ← 压成布尔就红在这一行

    def test_只报了一项就用那一项判(self):
        # 通道只报 token 不报结束原因：能判，别退化成 unknown
        r = self._resp(reported=frozenset({"completion_tokens"}), finish_reason="stop",
                       completion_tokens=qr.MAX_OUTPUT_TOKENS)
        assert qr.batch_completeness(r) == "truncated"

    def test_默认构造的响应是说不清(self):
        from app.services.ai.llm_client import LLMResponse
        # LLMResponse() 的默认值是 finish_reason="stop" / completion_tokens=0，
        # 长得跟"正常写完了"一样。默认 reported 是空集就是为了不让它冒充事实。
        assert qr.batch_completeness(LLMResponse(content="x")) == "unknown"


class TestMetaReported:
    """通道报的元数据算不算数 —— 判据**不是"键在不在"**。"""

    def test_CLI通道那种恒0的usage一项都不算(self):
        from app.services.ai import llm_client as lc
        # claude-proxy 实测回的就是这个形状：键全在、值全是编的
        got = lc._meta_reported({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                                in_key="prompt_tokens", prompt_chars=73_882)
        assert got == frozenset()

    def test_网关那种真数字全算(self):
        from app.services.ai import llm_client as lc
        got = lc._meta_reported({"prompt_tokens": 20_113, "completion_tokens": 6_386},
                                in_key="prompt_tokens", prompt_chars=73_882)
        assert got == lc._META_ALL

    def test_压根没有usage也是一项都不算(self):
        from app.services.ai import llm_client as lc
        assert lc._meta_reported({}, in_key="prompt_tokens", prompt_chars=100) == frozenset()

    def test_prompt本身是空的就不去证伪(self):
        from app.services.ai import llm_client as lc
        # prompt 空的时候 prompt_tokens=0 是**可能为真**的，不该据此判通道在编数
        assert lc._meta_reported({"prompt_tokens": 0}, in_key="prompt_tokens",
                                 prompt_chars=0) == lc._META_ALL


class TestCompletenessRendering:
    """三态要在页面上长得不一样。`unknown` 尤其不许长成 `complete`。"""

    def _md(self, cov):
        return qr.to_markdown(TestPartialBatchFailure()._rec(cov))

    _BASE = {"scenariosTotal": 10, "scenariosShown": 10, "scriptsTotal": 3,
             "scriptsRead": 3, "scriptsBatched": 3, "scriptsTruncated": 0, "batches": 3}

    def test_截断了要点名第几批(self):
        md = self._md({**self._BASE, "completeness": {"1": "complete", "2": "truncated",
                                                      "3": "complete"}})
        assert "第 2 批" in md and "撞上输出上限" in md
        # 「没抓到」和「没写完」不是一回事，必须写出来
        assert "没写完" in md

    def test_说不清和写完了长得不一样(self):
        unk = self._md({**self._BASE, "completeness": {"1": "unknown", "2": "unknown",
                                                       "3": "unknown"}})
        ok = self._md({**self._BASE, "completeness": {"1": "complete", "2": "complete",
                                                      "3": "complete"}})
        assert "说不清有没有写完" in unk
        assert "说不清 ≠ 写完了" in unk
        assert "每一批都写完了" in ok
        assert "每一批都写完了" not in unk       # ← 三态压成布尔就红在这一行
        assert "说不清有没有写完" not in ok
        assert "说不清 ≠ 写完了" not in ok

    def test_旧口径没记这件事就说没记(self):
        # S1.3 的 AC：存量结论（没有 completeness 字段）不许渲染成 complete
        md = self._md(dict(self._BASE))          # 无 completeness 键
        assert "没记" in md
        assert "别把它当成写完了" in md
        assert "每一批都写完了" not in md


class TestScriptsBatchedRendering:
    """洞四：**「从 git 读到」和「真进了模型」是两件事**，别用前者的数说后者。

    机制是真的（`split_batches` 那个 break 会静默丢，`scriptsRead` 数的是读到的份数，
    页面照样写「N 份全读了」）；但 2026-08-28 实测它**现在触发不了** ——
    38 个域一个都没超，而且按常量算够不着（见 test_批数封顶够不着所以不会静默丢）。
    所以这里的输入是**构造的**，不能指望真域触发。
    """

    def _md(self, cov):
        return qr.to_markdown(TestPartialBatchFailure()._rec(cov))

    _LOST = {"scenariosTotal": 10, "scenariosShown": 10, "scriptsTotal": 20,
             "scriptsRead": 20, "scriptsBatched": 14, "scriptsTruncated": 0, "batches": 8}

    def test_丢了就不许说全读了并且要写清差几份(self):
        md = self._md(dict(self._LOST))
        assert "全进了模型" not in md and "全读了" not in md
        assert "只有 14 份真进了模型" in md      # 开头第一个数字就得说实话
        assert "差 6 份" in md                    # 差几份要写出来，不能只说"有丢失"

    def test_旧口径没记就不敢说全读了(self):
        # 没有 scriptsBatched 的存量结论：不许沿用那句无条件断言
        cov = {k: v for k, v in self._LOST.items() if k != "scriptsBatched"}
        md = self._md(cov)
        assert "全读了" not in md and "全进了模型" not in md
        assert "这一版口径没记" in md


class TestLooseGapKey:
    """S2.2：`scriptGaps` 的跨批去重键不再截 60 字。

    截 60 字防不住什么 —— 每份脚本只出现在**一个**批里，跨批撞车本来就不成立；
    它能干的只有一件事：把同一份脚本上**两条不同的毛病**合成一条，
    因为技术描述的开头 60 字很容易一样（"断言只检查了 HTTP 状态码，没有…"）。
    合掉的那条在页面上不留任何痕迹 —— 又是一次「少了看起来像没有」。
    """

    # 得比 60 个字符长，否则两条的键在截断后就不相同了，这条测试等于什么都没测。
    # （第一版写了 35 个字符，下面那句前提断言当场拦住 —— 断言前提这件事就是干这个用的。）
    HEAD = ("断言只检查了返回码是不是 200，没有读回创建出来的那条记录去核对字段，"
            "也没有在失败分支上做任何检查，等于只验了这个接口没有崩掉")

    def _gap(self, tail):
        return {"id": "MCP-01", "path": "t.sh", "scenario": "MCP-01",
                "problem": self.HEAD + tail, "why": "", "severity": "major",
                "blame": "script"}

    def test_前六十字相同的两条scriptGaps都要在(self):
        assert len(self.HEAD) >= 60, "前提：公共前缀得比截断位置长，否则这条测不到东西"
        part = {"verdict": "bad", "summary": "", "brief": {},
                "scriptGaps": [self._gap("；名称没核"), self._gap("；配额没核")],
                "catalogGaps": [], "nextUp": []}

        merged = qr.merge_results([part])

        assert len(merged["scriptGaps"]) == 2, "截 60 字的旧键会把这两条合成一条"

    def test_一模一样的两条还是只留一条(self):
        # 放宽 ≠ 不去重。模型在同一批里把同一条写两遍，那个仍然只该留一条
        part = {"verdict": "bad", "summary": "", "brief": {},
                "scriptGaps": [self._gap("；名称没核"), self._gap("；名称没核")],
                "catalogGaps": [], "nextUp": []}

        assert len(qr.merge_results([part])["scriptGaps"]) == 1

    def test_catalogGaps保持原来的严格去重(self):
        """域级的那两项每批都会各说一遍，去重是真要干活的 —— 本 story 不动它。

        ⚠ 但别把这条读成"catalogGaps 的去重是好的"：副-A 实测跨批去重
        **一条都没去掉**（键后两段是自由文本，换个措辞就是新键）。
        那是 Epic 9 的事。这条只锁住「S2.2 没有顺手把它一起放宽」。
        """
        g = {"scenario": "MCP-07", "problem": "清单说已覆盖，实际只有冒烟", "why": "同上"}
        parts = [{"verdict": "ok", "summary": "", "brief": {}, "scriptGaps": [],
                  "catalogGaps": [dict(g)], "nextUp": []} for _ in range(3)]

        assert len(qr.merge_results(parts)["catalogGaps"]) == 1


class TestEvidenceClip:
    """S2.3：`evidence` 是要拿回原文比对的，从行中间切断 = 回验必然判 partial。

    **这条必须排在 Epic 3 之前。** 否则回验一上线就冒一片假 partial，
    而第一反应会是"回验不准"然后去放松回验 —— 修错地方，
    并且放松之后真的假 evidence 也一起放过去了。
    """

    LINES = [f'assert_status {i} 200 # {"x" * 28}' for i in range(30)]

    def _parse(self, ev):
        import json
        body = json.dumps({"verdict": "bad",
                           "scriptGaps": [{"id": "A", "evidence": ev}]},
                          ensure_ascii=False)
        return qr.parse_result(body)["scriptGaps"][0]

    def test_截完仍然是完整的行(self):
        ev = "\n".join(self.LINES)
        assert len(ev) > 600, "前提：得真的超了才截"

        row = self._parse(ev)

        got = row["evidence"].split("\n")
        assert got, "不许截成空"
        assert all(g in self.LINES for g in got), "有半行 —— 那半行在原文里找不到"
        assert row["evidenceTruncated"] == "1", "截了就得说截了"

    def test_没超就不截也不标(self):
        ev = "\n".join(self.LINES[:3])

        row = self._parse(ev)

        assert row["evidence"] == ev
        assert "evidenceTruncated" not in row

    def test_第一行自己就超长时硬切但仍然标出来(self):
        """截不出行边界的唯一情况。硬切没办法，但**不许闷声硬切** ——

        标了 `evidenceTruncated`，回验才知道这条本来就不完整，
        不会把"对不上"赖到脚本头上。
        """
        row = self._parse("z" * 800)

        assert len(row["evidence"]) == 600
        assert row["evidenceTruncated"] == "1"

    def test_别的字段照旧按字符截(self):
        # 按行截只对 evidence 特殊处理，problem 这些仍是 600 字符
        import json
        body = json.dumps({"verdict": "bad",
                           "scriptGaps": [{"id": "A", "problem": "。" * 900}]},
                          ensure_ascii=False)

        row = qr.parse_result(body)["scriptGaps"][0]

        assert len(row["problem"]) == 600
        assert "evidenceTruncated" not in row


class TestTimeoutBudget:
    """④ 是这四处改动里唯一不在代码里的那处 —— 所以把它**搬进代码**。

    只做 ③（提 max_tokens）不做 ④（提超时）的后果不是慢一点：
    各批耗时相近 ⇒ 6 批一起卡在默认的 120s ⇒ `if not good: raise`
    ⇒ 整个域的评审直接没了，**比不改还差**。

    ⚠ 落法换过一次，记在这里省得下次又绕一圈：
    第一版写成开跑前查一下服务配置里的超时、配小了就报错让人去页面改。
    两个问题 ——
    ① **页面上没有那个输入框**：超时在「AI 服务配置」的服务上，
       能力位只管选模型（`ai_capability_bindings` 表里压根没有 timeout 列）。
       一句照着做不了的报错，比不报错更糟。
    ② 就算改对了地方，那个数是**全平台共用**的：为这一个功能拧到 1020，
       别处每一个卡死的 AI 请求都要多等十五分钟才报错。
    所以改成本模块**自己带超时**下去，公共那个 120 一个字不动。
    """

    def test_这一批自己带足够长的超时(self):
        """真正的判据：`complete()` 收到的 timeout 装得下一批 10000 token。

        不去断言 DB 里配了多少 —— 那个数现在跟这件事无关了。
        """
        import inspect
        src = inspect.getsource(qr.run_review)
        assert "timeout=MIN_TIMEOUT_SECONDS" in src, \
            "run_review 没把自己的超时传下去 ⇒ 又回到吃全局那个 120 的老路"

    def test_门槛得跟实测墙钟对得上(self):
        # 实测最慢单批 404s。门槛贴着实测定，不是拍的；
        # 谁把 MIN_TIMEOUT_SECONDS 调小到装不下实测，这条替他红一次。
        assert qr.MIN_TIMEOUT_SECONDS >= 404 * 2

    @staticmethod
    def _need_seconds():
        """按实测折算：写满 `MAX_OUTPUT_TOKENS` 要多少秒。"""
        rate = qr.MEASURED_MAX_OUTPUT_TOKENS / 404          # 实测出字速度 ≈16 token/s
        return qr.MAX_OUTPUT_TOKENS / rate

    def test_两个数必须一起改(self):
        """`MAX_OUTPUT_TOKENS` 和 `MIN_TIMEOUT_SECONDS` 是连体的。

        按实测的 6386 token / 404s 折算，约 16 token/s。谁把 token 上限翻倍
        却不动超时，这条会红 —— 红在"写得完吗"上，而不是等上线那天六批一起超时。
        """
        need = self._need_seconds()
        assert qr.MIN_TIMEOUT_SECONDS >= need, \
            f"准写 {qr.MAX_OUTPUT_TOKENS} token 得给 {need:.0f}s，现在只给 {qr.MIN_TIMEOUT_SECONDS}s"

    def test_限流降级那一跳也得装得下(self):
        """④ 有两条腿，主路和 429 兜底，**只放宽主路等于只做了一半**。

        实测那批 237–404s 就是**走 claude-proxy 量的**，也就是说最慢的样本恰恰
        出在兜底这条腿上。而降级只在网关限流时发生 —— 平时跑得好好的，
        一到忙时段整批挂掉，日志里还只写"超时"，最难查的那种。
        """
        from app.services.ai import llm_client

        need = self._need_seconds()
        assert llm_client._proxy_timeout(qr.MIN_TIMEOUT_SECONDS) >= need, \
            f"兜底只给 {llm_client._proxy_timeout(qr.MIN_TIMEOUT_SECONDS):.0f}s，装不下 {need:.0f}s"

    def test_没人要求就还是原来那个兜底值(self):
        """`_PROXY_TIMEOUT` 是下限：没人带超时、或带得比它短，都维持 600。"""
        from app.services.ai import llm_client

        assert llm_client._proxy_timeout() == 600.0
        assert llm_client._proxy_timeout(None) == 600.0
        assert llm_client._proxy_timeout(120) == 600.0      # 主路默认值不该把兜底缩短
        assert llm_client._proxy_timeout(1020) == 1020.0
        assert llm_client._proxy_timeout(True) == 600.0     # bool 是 int 的子类，别当秒数

    def test_没人带超时的时候别改变原来的行为(self):
        """加的是**可选**参数。别的调用方（用例生成、评审、体检）一个都不该被影响。"""
        from app.services.ai import llm_client

        class _Cfg:
            timeout_seconds = 120

        assert llm_client._get_timeout(config=_Cfg()) == 120
        assert llm_client._get_timeout(config=_Cfg(), override=None) == 120
        assert llm_client._get_timeout(config=_Cfg(), override=1020) == 1020
        # 认不出来的 override 一律当没传，别猜
        assert llm_client._get_timeout(config=_Cfg(), override=0) == 120
        assert llm_client._get_timeout(config=_Cfg(), override="1020") == 120
        assert llm_client._get_timeout(config=_Cfg(), override=True) == 120


class TestEvidenceDisclosure:
    """截了就得在页面上说 —— 存了 `evidenceTruncated` 却不渲染，等于没存。

    markdown 里有**两处**会让 evidence 变短：入库时的 600 字符（S2.3 已改成按行截），
    和渲染时的 `splitlines()[:6]`。后者按本文是**该留**的（那是显示长度，不是额度），
    但留着不等于可以不说 —— 读的人拿这段去 grep 发现"就这么点"，
    会把「我只给你看了一部分」读成「它就只有这么多」。
    """

    def _md(self, gap):
        from datetime import datetime, timezone

        from app.models.qa_catalog_review import QaCatalogReview
        r = QaCatalogReview(
            domain="MCP", domain_name="MCP 能力", status="done",
            environment_name="uag-138:3000", commit_sha="dae9b4fc4150",
            branch="main", actor="admin", scenario_count=1, script_count=1,
            result={"verdict": "bad", "summary": "s", "brief": {},
                    "scriptGaps": [gap], "catalogGaps": [], "nextUp": [],
                    "envMissing": [], "reviewedScripts": [], "scenarioCount": 1},
        )
        r.created_at = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
        return qr.to_markdown(r)

    def test_原文超过六行要说还有多少行(self):
        md = self._md({"id": "A", "scenario": "MCP-01", "problem": "p",
                       "evidence": "\n".join(f"line{i}" for i in range(10))})

        assert "还有 4 行" in md

    def test_正好六行不说废话(self):
        md = self._md({"id": "A", "scenario": "MCP-01", "problem": "p",
                       "evidence": "\n".join(f"line{i}" for i in range(6))})

        assert "还有" not in md

    def test_入库截过要单独说一句(self):
        md = self._md({"id": "A", "scenario": "MCP-01", "problem": "p",
                       "evidence": "line0", "evidenceTruncated": "1"})

        assert "入库时被截过" in md

    def test_没截过就一句都不多说(self):
        md = self._md({"id": "A", "scenario": "MCP-01", "problem": "p",
                       "evidence": "line0"})

        assert "入库时被截过" not in md


class TestNextUpRetired:
    """Epic 9：`nextUp` 停产。

    删它的理由不是"没用"，是**分批模式下它算错**：每批只看得到一部分脚本，
    却要给全域排序，各批各排一份再拼起来。实测同一个域六批产出 18 行、
    去重后只有 3 件事，`MCP-76` 占了第 1/4/7/10/13/16 位 ——
    "先做哪条"那一栏六分之一的信息量都不到。
    而且排序代码能确定性地做，撞本模块自己的规矩「数和排序不许问模型」。
    """

    def test_解析层就不再收(self):
        out = qr.parse_result(
            '{"verdict":"ok","summary":"s","scriptGaps":[],"catalogGaps":[],'
            '"nextUp":[{"id":"MCP-76","why":"P0"}]}')

        assert "nextUp" not in out

    def test_合批结果里也没有(self):
        merged = qr.merge_results([
            {"verdict": "ok", "scriptGaps": [], "catalogGaps": [],
             "nextUp": [{"id": "MCP-76", "why": "P0"}]},
        ])

        assert "nextUp" not in merged

    def test_提示词说几件就得列几件(self):
        """删第 3 项时差点留下「你只做三件判断」+ 只列两条 —— 提示词自己前后矛盾。

        这种错模型不会报，它会自己找补：要么把两条硬拆成三条，要么补一个
        我们已经不要的键。**改提示词的条目数就必须同时改这个数**，
        所以把它锁成一条测试，而不是靠下一个人读到那一行。
        """
        import re
        n = len(re.findall(r"^\d+\. \*\*", qr._SYSTEM, re.M))
        cn = {2: "两", 3: "三", 4: "四"}[n]
        assert f"你只做{cn}件判断" in qr._SYSTEM, f"正文列了 {n} 条"

    def test_提示词不再要模型排序(self):
        # 停产要停在**问都不问**这一层：提示词还留着第 3 项的话，
        # 模型照样花 token 去算，只是算完被丢掉 —— 白花钱还拖慢每一批。
        assert "nextUp" not in qr._SYSTEM
        assert "先做哪条" not in qr._SYSTEM

    def test_旧结论里带nextUp时页面不崩(self):
        """存量结论的 result JSON 里还有这个键 —— 渲染要**不显示**，不是炸掉。"""
        from datetime import datetime, timezone

        from app.models.qa_catalog_review import QaCatalogReview
        r = QaCatalogReview(
            domain="MCP", domain_name="MCP 能力", status="done",
            environment_name="uag-138:3000", commit_sha="dae9b4fc4150",
            branch="main", actor="admin", scenario_count=1, script_count=1,
            result={"verdict": "risky", "summary": "s", "brief": {},
                    "scriptGaps": [], "catalogGaps": [], "envMissing": [],
                    "nextUp": [{"id": "MCP-76", "why": "P0 R=9"}],
                    "reviewedScripts": [], "scenarioCount": 1},
        )
        r.created_at = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)

        md = qr.to_markdown(r)

        assert "MCP-76" not in md
        assert "先做哪条" not in md


class TestCatalogGapsKey:
    """副-C / 副-D：`catalogGaps` 的跨批去重换成结构键。

    它是**域级**的 —— 每批都拿到全量场景清单，所以同一条会被各批各说一遍，
    措辞还都不一样。旧键是 `id|path|scenario|problem|why` 各截 60 字的自由文本，
    实测跨批**一条都没去掉**。身份在 `scenario`，不在后面那段解释文字。
    """

    def test_同一个场景换个措辞也算同一条(self):
        merged = qr.merge_results([
            {"verdict": "ok", "scriptGaps": [], "catalogGaps": [
                {"scenario": "MCP-76", "why": "只有创建和查询，没有删除后的越权访问"}]},
            {"verdict": "ok", "scriptGaps": [], "catalogGaps": [
                {"scenario": "MCP-76", "why": "缺一条删除之后再访问的用例"}]},
        ])

        assert len(merged["catalogGaps"]) == 1

    def test_合掉几条要说出来(self):
        """修好键之后页面上会**少掉一大截行** —— 不说清楚就像这一趟少发现了东西。"""
        merged = qr.merge_results([
            {"verdict": "ok", "scriptGaps": [], "catalogGaps": [
                {"scenario": "MCP-76", "why": "措辞一"}]},
            {"verdict": "ok", "scriptGaps": [], "catalogGaps": [
                {"scenario": "MCP-76", "why": "措辞二"}]},
            {"verdict": "ok", "scriptGaps": [], "catalogGaps": [
                {"scenario": "MCP-76", "why": "措辞三"}]},
        ])

        assert merged["catalogGaps"][0]["mergedFrom"] == 3
        assert "3 批都提到" in qr.to_markdown(_review_with(merged["catalogGaps"]))

    def test_只提到一次的不加那个数(self):
        merged = qr.merge_results([
            {"verdict": "ok", "scriptGaps": [], "catalogGaps": [
                {"scenario": "MCP-76", "why": "只有这一批说了"}]},
        ])

        assert "mergedFrom" not in merged["catalogGaps"][0]
        assert "批都提到" not in qr.to_markdown(_review_with(merged["catalogGaps"]))

    def test_不同场景不许合(self):
        merged = qr.merge_results([
            {"verdict": "ok", "scriptGaps": [], "catalogGaps": [
                {"scenario": "MCP-76", "why": "同一句话"}]},
            {"verdict": "ok", "scriptGaps": [], "catalogGaps": [
                {"scenario": "MCP-77", "why": "同一句话"}]},
        ])

        assert len(merged["catalogGaps"]) == 2

    def test_模型没填scenario时退回去不掉而不是误合(self):
        """退化的方向要选对：宁可留两条重复，也不能把两个不同的发现合成一条。"""
        merged = qr.merge_results([
            {"verdict": "ok", "scriptGaps": [], "catalogGaps": [
                {"why": "缺删除后的越权访问"}]},
            {"verdict": "ok", "scriptGaps": [], "catalogGaps": [
                {"why": "缺并发写的冲突处理"}]},
        ])

        assert len(merged["catalogGaps"]) == 2

    def test_scriptGaps的键里不留那段永远为空的scenario(self):
        """副-D：实测 72/72 条 `scenario` 全是空的，模型从来不填。

        留着会让这个键看起来考虑了三个维度、实际只有两个 —— 而"看起来考虑了"
        正是本模块在抓的那类错。
        """
        import inspect
        src = inspect.getsource(qr._gap_key)
        head, _, tail = src.partition('if kind == "catalogGaps"')
        assert '"scenario"' not in tail.split('return "|".join')[1]


def _review_with(catalog_gaps):
    from datetime import datetime, timezone

    from app.models.qa_catalog_review import QaCatalogReview
    r = QaCatalogReview(
        domain="MCP", domain_name="MCP 能力", status="done",
        environment_name="uag-138:3000", commit_sha="dae9b4fc4150",
        branch="main", actor="admin", scenario_count=1, script_count=1,
        result={"verdict": "risky", "summary": "s", "brief": {}, "scriptGaps": [],
                "catalogGaps": catalog_gaps, "envMissing": [],
                "reviewedScripts": [], "scenarioCount": 1},
    )
    r.created_at = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    return r


class TestDimsOnTheWire:
    """Epic 10：维度口径由后端发，前端不再存副本。

    前端原来抄了三份常量（`AXES` / `DIM_KEYS` / `DIM_SINCE`），注释里写着
    「跟后端必须一字不差」—— 那是一句**没有任何东西在执行**的话。漂了之后错得极安静：
    后端加一条子项、前端 `DIM_SINCE` 没跟上，新子项在存量结论上不会标「这一趟没查」，
    而是渲染成一个漂亮的 0。假的 0 正是这整套表最该堵掉的东西。
    """

    def _r(self, result=None):
        from datetime import datetime, timezone

        from app.models.qa_catalog_review import QaCatalogReview
        r = QaCatalogReview(
            domain="MCP", domain_name="MCP 能力", status="done",
            environment_name="uag-138:3000", commit_sha="dae9b4fc4150",
            branch="main", actor="admin", scenario_count=1, script_count=1,
            result=result,
        )
        r.created_at = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
        return r

    def test_详情才带列表不带(self):
        """列表一次出几十行，每行挂一份口径就是同一段常量发几十遍。

        默认关的方向是**故意选的**：忘了传是"详情少个字段"（页面上明说画不出来），
        传反了是"列表接口悄悄胖十倍"（没人会发现）。两个方向的代价不对称。
        """
        r = self._r({"verdict": "ok", "scriptGaps": [], "catalogGaps": [], "dimSpec": 2})

        assert "dims" not in qr.to_dict(r)
        assert "dimSpec" not in qr.to_dict(r)
        assert qr.to_dict(r, with_dims=True)["dims"] == qr.dim_rollup(r.result)

    def test_发的是当前口径版本不是结论那一版(self):
        """两个数一比才知道「这条结论落后了几版」—— 只发一个数说明不了这件事。"""
        r = self._r({"verdict": "ok", "scriptGaps": [], "catalogGaps": [], "dimSpec": 1})

        out = qr.to_dict(r, with_dims=True)

        assert out["dimSpec"] == qr.DIM_SPEC
        assert out["result"]["dimSpec"] == 1

    def test_还在跑的那条没有result也不炸(self):
        # 详情接口拿 running 的那条也走 with_dims=True，result 是 None。
        out = qr.to_dict(self._r(None), with_dims=True)

        assert [d["count"] for d in out["dims"]] == [0, 0, 0]

    def test_旧结论里新子项标着没查(self):
        r = self._r({"verdict": "ok", "scriptGaps": [], "catalogGaps": [], "dimSpec": 1})

        items = {i["key"]: i for d in qr.to_dict(r, with_dims=True)["dims"]
                 for i in d["items"]}

        assert items["expect"]["unavailable"] is True
        assert items["assert"]["unavailable"] is False


class TestFrontendKeepsNoCopy:
    """前端那三份副本必须是**删掉**，不是"留着当兜底"。

    留着兜底等于这个 Epic 什么都没做：副本还在，还会漂，只是平时看不见它在用。
    """

    FE = (pathlib.Path(__file__).resolve().parents[2]
          / "frontend/src/pages/qa/QaCatalog.jsx")

    def test_前端不再抄一份口径(self):
        src = self.FE.read_text(encoding="utf-8")

        for dead in ("const AXES", "DIM_KEYS", "DIM_SINCE", "DIM_OTHER", "dimRollup"):
            assert dead not in src, f"{dead} 还留在前端"

    def test_拿不到dims时不许显示问号(self):
        """`?` 在正常路径上专指「这一趟没查」。

        降级时也画个 `?`，读的人会以为"后端查过、这几条没抓到"——
        两个意思撞在一起就是一条假信息，比一片空白坏得多。
        """
        src = self.FE.read_text(encoding="utf-8")
        body = src.split("function DimUnavailable")[1].split("function DimTable")[0]

        assert "'?'" not in body and '"?"' not in body
        assert "后端没给出维度口径" in body


class TestEnvTiers:
    """Epic 5：变量缺口分三档 `absent` / `ambiguous` / `satisfied`。

    危害不在那条假阳本身，在于**它跟 `UAG_APIKEY`/`PSQL_DSN` 两个真缺口并排、
    用同样的置信度显示** —— 一条响亮的假阳会让人把整列当噪音，两个真阳跟着被无视。
    实测 `uag-138:3000` 配了 7 组带角色前缀的账号，而这一列照样报「缺 PASSWORD」。
    """

    def _scan(self, content, env_keys):
        return {g["name"]: g for g in
                qr.scan_env_vars([{"path": "a.sh", "content": content}], set(env_keys))}

    def test_角色前缀的同名变量不算真缺(self):
        env = ["ADMIN_PASSWORD", "PLATADMIN_PASSWORD", "TENANT_PASSWORD", "OPS_PASSWORD",
               "AUDIT_PASSWORD", "GUEST_PASSWORD", "SVC_PASSWORD"]

        g = self._scan('echo "$PASSWORD"\n', env)["PASSWORD"]

        assert g["state"] == "ambiguous"
        assert g["family"] == sorted(env)

    def test_两个真缺口不许被家族匹配吃掉(self):
        """★ **整组里最重要的一条。**

        修误报最容易的翻车方式就是把真阳一起修掉 —— 而且修掉之后页面变干净，
        看着像修好了。`UAG_APIKEY` 和 `PSQL_DSN` 是这一列唯一有价值的东西，
        它们俩没了，这一列就只剩装饰。
        """
        env = ["ADMIN_PASSWORD", "PLATADMIN_PASSWORD", "TENANT_PASSWORD", "BASE_URL",
               "ADMIN_TOKEN", "MAIN_DSN", "GW_APIKEY"]

        got = self._scan('echo "$UAG_APIKEY $PSQL_DSN"\n', env)

        assert got["UAG_APIKEY"]["state"] == "absent"
        assert got["PSQL_DSN"]["state"] == "absent"

    def test_短尾段不参与家族匹配(self):
        """`DSN`(3) / `URL`(3) / `ID`(2) 这种尾段谁都带一个，放进家族匹配就整族降级。"""
        got = self._scan('echo "$DSN $URL"\n', ["PSQL_DSN", "BASE_URL"])

        assert got["DSN"]["state"] == "absent"
        assert got["URL"]["state"] == "absent"

    def test_家族匹配按下划线分段不按结尾子串(self):
        """`"SERVICE_TOKEN".endswith("VICE_TOKEN")` 是**真的**。

        裸子串匹配会让一个毫不相干的键把真缺口洗白。方向要摆对：危险的是
        **环境键**在段中间套住了候选名（下面两组都是），不是反过来 ——
        写反了这条测试就恒绿，`endswith` 的实现照样能过。
        `PSQL_DSN` 那组尤其要盯：它正是这个域两个真缺口之一。
        """
        got = self._scan('echo "$VICE_TOKEN $SQL_DSN"\n', ["SERVICE_TOKEN", "PSQL_DSN"])

        assert got["VICE_TOKEN"]["state"] == "absent"
        assert "family" not in got["VICE_TOKEN"]
        assert got["SQL_DSN"]["state"] == "absent"

    def test_家族匹配是单向的(self):
        """只认「候选名 == 某个环境键的尾巴」，不认反过来。

        环境有 `APIKEY`、脚本要 `UAG_APIKEY`：算不算覆盖判不了，
        而判错的方向是**把真缺口洗白**。这种时候宁可留着那条响的。
        """
        g = self._scan('echo "$UAG_APIKEY"\n', ["APIKEY"])["UAG_APIKEY"]

        assert g["state"] == "absent"

    def test_家族里列出来的都是环境键名(self):
        """降级要连**凭什么降**一起写出来，否则它就是一句无从复核的断言。

        列的是**键名** —— 它们直接进提示词和导出的 Markdown，跟值有关的东西
        一个字节都不许跟着走。
        """
        env = {"ADMIN_PASSWORD", "TENANT_PASSWORD"}

        g = self._scan('echo "$PASSWORD"\n', env)["PASSWORD"]

        assert set(g["family"]) <= env
        assert set(g) == {"name", "scripts", "state", "family"}

    def test_环境里就有的落第三档(self):
        got = self._scan('echo "$ADMIN_TOKEN"\n', ["ADMIN_TOKEN"])

        assert got["ADMIN_TOKEN"]["state"] == "satisfied"

    def test_env_gaps不装第三档(self):
        """它的返回值在四处被 `len()` 当成「缺 N 个」渲染。

        掺进环境里**有**的那些，那个数当场变成一个不报错的错数 ——
        而不报错的错数正是这整套评审在抓的东西。
        """
        gaps = qr.env_gaps([{"path": "a.sh", "content": 'echo "$ADMIN_TOKEN"\n'}],
                           {"ADMIN_TOKEN"})

        assert gaps == []

    def test_动态后缀在声明分支也要放过(self):
        """豁免要豁在**两个分支**上。

        同一个名字改用 `export X="${X:-}"` 声明一次，就绕过了只写在引用分支里的
        那句豁免，从声明这边原样冒出来。
        """
        lib = 'printf -v "${p}_TOKEN" "%s" "x"\nexport MB_TOKEN="${MB_TOKEN:-}"\n'

        gaps = qr.env_gaps([{"path": "a.sh", "content": "echo hi\n"}], set(), [lib])

        assert [g["name"] for g in gaps] == []


class TestEnvTiersRendering:
    """三档在导出的 Markdown 里长什么样。"""

    def _md(self, missing, satisfied):
        from datetime import datetime, timezone

        from app.models.qa_catalog_review import QaCatalogReview
        r = QaCatalogReview(
            domain="MCP", domain_name="MCP 能力", status="done",
            environment_name="uag-138:3000", commit_sha="dae9b4fc4150", branch="main",
            actor="admin", scenario_count=1, script_count=1,
            result={"verdict": "risky", "summary": "s", "brief": {}, "scriptGaps": [],
                    "catalogGaps": [], "envMissing": missing, "envSatisfied": satisfied,
                    "reviewedScripts": [], "scenarioCount": 1},
        )
        r.created_at = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
        return qr.to_markdown(r)

    def test_降级那一行写清凭什么降(self):
        md = self._md([{"name": "PASSWORD", "scripts": ["a.sh"], "state": "ambiguous",
                        "family": ["ADMIN_PASSWORD", "TENANT_PASSWORD"]}], [])

        line = [x for x in md.splitlines() if x.startswith("- `PASSWORD`")][0]
        assert "不是真缺" in line
        assert "ADMIN_PASSWORD" in line and "TENANT_PASSWORD" in line

    def test_缺几个要有分母(self):
        """「缺 2 个」既可能是 2/3 也可能是 2/40 —— 没分母读的人判不了这一列有多严重。"""
        md = self._md([{"name": "UAG_APIKEY", "scripts": ["a.sh"], "state": "absent"}],
                      ["BASE_URL", "ADMIN_TOKEN", "PSQL_DSN"])

        assert "要从外面拿 4 个变量，其中 3 个这个环境里有" in md

    def test_一个都不缺时不画分母(self):
        md = self._md([], ["BASE_URL"])

        assert "脚本要的变量这个环境都有" in md
        assert "要从外面拿" not in md

    def test_前端摘要那个数也只数真缺的(self):
        """页面顶上还有一句「我们这条环境记录里缺 N 个变量名」。

        列表里分了档、摘要里没分，那条误报只是**从列表挪进了摘要** —— 而摘要更醒目。
        这个数在前端算，后端测不到它的行为，只能扫源码把过滤这件事钉住。
        """
        src = (pathlib.Path(__file__).resolve().parents[2]
               / "frontend/src/pages/qa/QaCatalog.jsx").read_text(encoding="utf-8")
        line = [x for x in src.splitlines() if "const nEnvVar" in x][0]

        assert "ambiguous" in line or "absent" in line, line

    def test_真缺那一档照旧一行一个不加料(self):
        md = self._md([{"name": "UAG_APIKEY", "scripts": ["config/env.sh"],
                        "state": "absent"}], [])

        line = [x for x in md.splitlines() if x.startswith("- `UAG_APIKEY`")][0]
        assert line == "- `UAG_APIKEY` — config/env.sh"


# ── Epic 3：evidence 回验 ────────────────────────────────────────────────────

#: 回验用的样本脚本。**行与行之间要拉开距离** —— ★#11 要的是"两条非相邻真实行"，
#: 相邻两行拼起来在归一化之后还是连续的，那测的是 `reflowed`，不是 `stitched`。
EV_SCRIPT = """\
#!/usr/bin/env bash
# @scenario AGT-11
set -euo pipefail
out=$(mktemp)
curl -s -o "$out" -H "Authorization: Bearer $ADMIN_TOKEN" "$API_BASE/agents"
echo "fetched"
if [ ! -s "$out" ]; then
  echo "empty"
fi
grep -q '"status":"ok"' "$out"
rm -f "$out"
"""

_CURL = 'curl -s -o "$out" -H "Authorization: Bearer $ADMIN_TOKEN" "$API_BASE/agents"'
_GREP = """grep -q '"status":"ok"' "$out\""""
_RM = 'rm -f "$out"'


class TestEvidenceCheck:
    """`evidence` 到底是不是从脚本正文里抄的 —— 拿回去搜一遍。

    导出的 Markdown 里有一句「每条都能十秒内被否掉：`evidence` 是从脚本正文原样抄的」。
    那句话此前**没有任何东西在验证它**。而这个模块的全部意义就是抓
    「结论看起来有据、依据其实没验过」—— 在自己身上留一句没验过的承诺，
    比不写这句话坏得多：它把"可复核"从一个可检查的性质，变成一句需要相信的话。
    """

    @staticmethod
    def _check(evidence, path="a.sh", scripts=None):
        g = {"id": "X-01", "severity": "major", "path": path, "problem": "p",
             "evidence": evidence}
        ec.check_evidence([g], scripts or [{"path": "a.sh", "content": EV_SCRIPT}])
        return g

    def test_原样抄的认得出来(self):
        """#10。一字不差、连缩进都没动的那一档。"""
        assert self._check(_GREP)["evidenceCheck"] == "verbatim"

    def test_跨行拼接的判据也算数(self):
        """★#11。**整组里最重要的一条 —— 防的是 27% 的假阳。**

        真实的判据经常是「第 5 行的请求 + 第 11 行的清理」拼起来的，中间隔着好几行。
        任何"整块 exact match"的实现会把这类**真判据**判成编造，
        然后页面上一片"判据搜不到"，人看两眼就再也不信这一列了。

        注意 Epic 5 那边的翻车方向是**把真阳修没了**，这里正好反过来：
        **放松得不够，真判据会被打成编造。** 两个方向都得有哨兵。
        """
        g = self._check(_CURL + "\n" + _RM)

        assert g["evidenceCheck"] == "stitched"
        assert g["evidenceCheck"] in ec.PASS_STATES

    def test_换行重排也算数(self):
        """#12。模型是在写 JSON 字符串，缩进和换行几乎必然会变。

        拿原始文本做 exact match 等于**要求模型逐字节复刻缩进** ——
        那种实现报出来的"搜不到"里绝大多数是排版差异，不是编造。
        """
        g = self._check('  echo "fetched"\n      if [ ! -s "$out" ]; then')

        assert g["evidenceCheck"] == "reflowed"
        assert g["evidenceCheck"] in ec.PASS_STATES

    def test_编出来的要标出来(self):
        """#13。脚本里根本没有这一句。"""
        assert self._check('assert_status 403 "$resp"')["evidenceCheck"] == "unmatched"

    def test_判据真但路径写错跟编造要分得开(self):
        """#14。两件事，处置也不同：前者改一个字段就能用，后者整条不能信。

        混成一档等于把前者当废品扔了 —— 而它其实是这份结论里最容易兑现的那部分。
        """
        g = self._check(_GREP, path="other.sh",
                        scripts=[{"path": "other.sh", "content": "echo hi\n"},
                                 {"path": "a.sh", "content": EV_SCRIPT}])

        assert g["evidenceCheck"] == "wrong-path"
        assert g["evidenceFoundIn"] == "a.sh"

    def test_太短的不算验过(self):
        """#15。`fi` / `done` / `set -e` 在任何一份 shell 脚本里都命中。

        算通过等于把这道检查变成橡皮图章：**通过率 100%，信息量 0**。
        """
        for tiny in ("fi", "done", "set -e", "  }  "):
            assert self._check(tiny)["evidenceCheck"] == "too_short", tiny

    def test_没给判据标empty不标unmatched(self):
        """#18。「他没给判据」和「他给的判据是编的」是两个问题，别合并。

        合并之后，"提示词该逼它给判据"这条改进方向就再也看不见了。
        """
        for blank in (None, "", "   \n  \n"):
            assert self._check(blank)["evidenceCheck"] == "empty"

    def test_搜不到的也留在输出里只是带标记(self):
        """#16（S3.4）。**只打标记，不删、不降 severity。**

        删 ⇒ 丢了多少不可知，「一条没删」和「删了 8 条」在页面上长得一模一样 ——
        正是本模块要禁的那个形状。
        """
        gaps = [{"id": "A", "severity": "blocker", "path": "a.sh", "evidence": _GREP},
                {"id": "B", "severity": "blocker", "path": "a.sh",
                 "evidence": 'assert_forbidden 403 "$resp"'},
                {"id": "C", "severity": "minor", "path": "a.sh", "evidence": _RM}]

        out = ec.check_evidence(gaps, [{"path": "a.sh", "content": EV_SCRIPT}])

        assert [g["id"] for g in out] == ["A", "B", "C"]
        # severity 说的是「对仓库有多糟」，回验说的是「我有多确信」—— 两个正交的轴，
        # 合成一个还会污染 `_SEV_RANK` 的排序
        assert [g["severity"] for g in out] == ["blocker", "blocker", "minor"]
        assert out[1]["evidenceCheck"] == "unmatched"

    def test_截断过的判据不额外放水(self):
        """`_clip_lines` 切在行边界上，或首行超长时硬切 —— 两种都还是正文的子串。

        所以这里**故意不加**"截过就放松一档"的兜底：那种兜底会把真编造的短判据
        一起放过去，而它看起来像是在修一个假阳。
        """
        clipped, cut = qr._clip_lines(_CURL + "\n" + _GREP + "\n" + _RM, limit=len(_CURL) + 20)

        assert cut
        assert self._check(clipped)["evidenceCheck"] in ec.PASS_STATES


class TestEvidenceStats:
    """摘要那个数**从行本身数**，不从回验那一步的返回值攒。"""

    def test_没标记的算unchecked不算验过(self):
        """存量结论里没有这个键。**不许当成"验过了"** —— 那正好是这一版要装的东西。"""
        st = ec.evidence_stats([{"id": "A"}, {"id": "B", "evidenceCheck": "verbatim"}])

        assert st["total"] == 2
        assert st["unchecked"] == 1
        assert st["verified"] == 1

    def test_三档都算验过(self):
        rows = [{"evidenceCheck": s} for s in ("verbatim", "reflowed", "stitched",
                                               "unmatched", "empty")]

        assert ec.evidence_stats(rows)["verified"] == 3


class TestEvidenceCheckWiring:
    """回验落在哪一步 —— 这决定了它查不查得出跨批的编造。"""

    class _Resp:
        def __init__(self, content):
            self.content = content
            self.reported = frozenset()

    B_ONLY = 'assert_forbidden 403 "$resp_b"'

    def _scripts(self):
        # 两份都撑到 60KB：BATCH_SCRIPT_BYTES 是 90KB，装不进一批 ⇒ 必然分两批
        pad = "\n".join(f"# pad {i} " + "x" * 50 for i in range(1100))
        return [{"path": "a.sh", "content": "echo a\n" + pad, "truncated": False},
                {"path": "b.sh", "content": self.B_ONLY + "\n" + pad, "truncated": False}]

    @pytest.mark.asyncio
    async def test_回验必须在合并之前(self, monkeypatch):
        """★#17。A 批的结论引用了一句**只存在于 B 批脚本**里的正文。

        回验在 `_one` 里做 ⇒ A 批手上只有 a.sh，那句话搜不到 ⇒ `unmatched`。
        回验一旦被挪到 merge 之后 ⇒ 手上是全域脚本，那句话在 b.sh 里找得到
        ⇒ 变成 `wrong-path`，**跨批编造当场就查不出来了**。

        而挪过去之后类型、形状、其余单测全都过得去 —— 只有这一条会红，
        红起来还像是"测试写得太严"。所以这段话写在这里。
        """
        def _fence(gap_id, path, sev):
            # 判据正文里有引号，手拼 JSON 会拼出一个模型永远不会发的畸形串 ——
            # 那时红的是解析层，测不到回验
            return "```json\n" + json.dumps(
                {"verdict": "risky", "summary": "s",
                 "scriptGaps": [{"id": gap_id, "path": path, "severity": sev,
                                 "problem": "p", "evidence": self.B_ONLY}]}) + "\n```"

        a_json, b_json = _fence("X-A", "a.sh", "major"), _fence("X-B", "b.sh", "minor")
        merge_json = ('```json\n{"brief":{"headline":"h","points":["p"],'
                      '"nextStep":"n","solid":["s"]},"summary":"合并"}\n```')

        async def fake(messages, **kw):
            user = messages[-1]["content"]
            if "## 合并后的结论" in user:
                return self._Resp(merge_json)
            return self._Resp(a_json if "### a.sh" in user else b_json)

        monkeypatch.setattr(qr.llm_client, "complete", fake)

        out = await qr.run_review(
            domain={"code": "AGT", "name": "智能体"}, scenarios=[],
            scripts=self._scripts(), env_name="e", env_keys=[], lib_texts=[])

        rows = {g["id"]: g for g in out["scriptGaps"]}
        assert rows["X-A"]["evidenceCheck"] == "unmatched"   # ← 挪到 merge 之后这里会变 wrong-path
        assert "evidenceFoundIn" not in rows["X-A"]
        assert rows["X-B"]["evidenceCheck"] == "verbatim"

    @pytest.mark.asyncio
    async def test_计数进覆盖率块且跟列出来的行同源(self, monkeypatch):
        """#19。摘要的数和页面列的条数**不能打架**。

        各批各回一份统计再相加，那两个数就有了两条独立路径 ——
        哪天分歧了没人看得出来是哪边错。所以两边都扫同一批行。
        """
        async def fake(messages, **kw):
            user = messages[-1]["content"]
            if "## 合并后的结论" in user:
                return self._Resp('```json\n{"brief":{"headline":"h"},"summary":"合并"}\n```')
            return self._Resp(
                '```json\n{"verdict":"risky","summary":"s","scriptGaps":[{"id":"G",'
                '"path":"a.sh","severity":"major","problem":"p","evidence":"编的一句话"}]}\n```')

        monkeypatch.setattr(qr.llm_client, "complete", fake)

        out = await qr.run_review(
            domain={"code": "AGT", "name": "智能体"}, scenarios=[],
            scripts=[{"path": "a.sh", "content": EV_SCRIPT, "truncated": False}],
            env_name="e", env_keys=[], lib_texts=[])

        ev = out["coverage"]["evidence"]
        assert ev == ec.evidence_stats(out["scriptGaps"])
        assert ev["total"] == len(out["scriptGaps"])
        assert ev["verified"] == 0 and ev["unchecked"] == 0

    async def _run(self, monkeypatch, merge, scripts=None):
        """跑一趟 run_review。`merge` 给 None 表示收口那一趟抛异常。"""
        async def fake(messages, **kw):
            user = messages[-1]["content"]
            if "## 合并后的结论" in user:
                if merge is None:
                    raise ValueError("模型没按 JSON 回：Expecting value")
                return self._Resp(merge)
            return self._Resp('```json\n{"verdict":"risky","summary":"分批的话",'
                              '"scriptGaps":[{"id":"G","path":"a.sh","severity":"major",'
                              '"problem":"p","evidence":"e"}]}\n```')

        monkeypatch.setattr(qr.llm_client, "complete", fake)
        return await qr.run_review(
            domain={"code": "AGT", "name": "智能体"}, scenarios=[],
            scripts=self._scripts() if scripts is None else scripts,
            env_name="e", env_keys=[], lib_texts=[])

    @pytest.mark.asyncio
    async def test_收口跑成了盖merged(self, monkeypatch):
        out = await self._run(monkeypatch,
                              '```json\n{"brief":{"headline":"h","points":["p"]},'
                              '"summary":"合并"}\n```')

        assert out["briefSource"] == "merged"

    @pytest.mark.asyncio
    async def test_收口挂了退回拼接必须盖戳(self, monkeypatch):
        """**2026-08-29 验收跑 TEM 时的活体缺陷。**

        网关限流把收口那一趟打成空响应，代码按设计退回拼接版 —— 明细一条没少，
        可 `brief` 的 headline 变成概述的前 120 字、重点/下一步/撑得住的部分全空。
        页面上那一页于是长成「这个域没什么重点」，和「总结那一趟根本没跑成」
        一模一样。**退回本身没错，不盖戳才是错。**

        这条同时钉住两件事：戳要盖上，**而且明细不许跟着丢** ——
        把收口失败升级成评审失败就是另一种错法（14 条真发现陪葬）。
        """
        out = await self._run(monkeypatch, None)

        assert out["briefSource"] == "stitched"
        assert len(out["scriptGaps"]) >= 1          # 明细一条不许少
        assert out["summary"]                       # 拼接版的概述还在

    @pytest.mark.asyncio
    async def test_只有一批盖single不算收口跑成了(self, monkeypatch):
        """单批本来就没有收口这一步。**并进 `merged` 就是把"没跑过"说成"跑成了"。**"""
        out = await self._run(monkeypatch, None,
                              scripts=[{"path": "a.sh", "content": "echo a\n",
                                        "truncated": False}])

        assert out["briefSource"] == "single"

    def test_收口的提示词把存疑的标出来(self):
        """#19 的另一半：喂给收口那一趟的清单里，存疑的要带记号。

        不标的话，模型会把一条判据搜不到的发现挑进 brief 当重点 ——
        页面上那条底下写着「判据没验上」，brief 里却拿它当结论，两屏打架。
        """
        merged = {"verdict": "risky", "catalogGaps": [],
                  "scriptGaps": [{"id": "A", "blame": "script", "severity": "major",
                                  "oneLine": "真的", "evidenceCheck": "stitched"},
                                 {"id": "B", "blame": "script", "severity": "major",
                                  "oneLine": "存疑的", "evidenceCheck": "unmatched"}]}

        txt = qr._merge_payload({"code": "AGT"}, merged, 2, 5)

        assert "⚠判据存疑 [script][major] B" in txt
        assert "⚠判据存疑 [script][major] A" not in txt
        # 数照抄别自己减 —— 页面列的是全部两条
        assert "- 脚本要改：2 条" in txt
        assert "有 1 条的判据在脚本正文里搜不到" in txt


class TestEvidenceRendering:
    """S3.5②：那句「十秒内被否掉」从**无条件承诺**改成**实测陈述**。"""

    @staticmethod
    def _md(gaps):
        from datetime import datetime, timezone

        from app.models.qa_catalog_review import QaCatalogReview
        r = QaCatalogReview(
            domain="AGT", domain_name="智能体", status="done", environment_name="e",
            commit_sha="abc1234567", branch="main", actor="cc",
            scenario_count=3, script_count=1,
            result={"verdict": "risky", "summary": "s", "brief": {}, "scriptGaps": gaps,
                    "catalogGaps": [], "envMissing": [], "envSatisfied": [],
                    "reviewedScripts": [], "scenarioCount": 3},
        )
        r.created_at = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
        return qr.to_markdown(r)

    def test_页面那句承诺跟着核验结果走(self):
        """★#21。**这个模块最不该有的，就是一句自己没验过的承诺。**"""
        md = self._md([
            {"id": "A", "severity": "major", "blame": "script", "path": "a.sh",
             "problem": "p", "evidence": _GREP, "evidenceCheck": "verbatim"},
            {"id": "B", "severity": "major", "blame": "script", "path": "a.sh",
             "problem": "p", "evidence": "编的", "evidenceCheck": "unmatched"},
        ])

        assert "2 条判据平台已经拿回脚本正文搜过一遍，1 条 grep 得到" in md
        assert "**1 条搜不到**" in md
        assert "✅ **判据回验过了**" not in md          # 有搜不到的就不许挂 ✅

    def test_全都搜到了才挂对勾(self):
        md = self._md([{"id": "A", "severity": "major", "blame": "script", "path": "a.sh",
                        "problem": "p", "evidence": _GREP, "evidenceCheck": "verbatim"}])

        assert "✅ **判据回验过了**" in md
        assert "一条不落" in md

    def test_存量结论不许套用那句对勾(self):
        """回验上线之前评的那些，一条都没搜过。

        套用 ✅ 等于拿一句没验过的话去担保另一句没验过的话 —— 而这恰好就是
        这个 Epic 要修的那个形状，修的时候自己再犯一次就太难看了。
        """
        md = self._md([{"id": "A", "severity": "major", "blame": "script", "path": "a.sh",
                        "problem": "p", "evidence": _GREP}])

        assert "判据没回验过" in md
        assert "✅ **判据回验过了**" not in md

    def test_没有脚本级发现时照旧是那句原话(self):
        md = self._md([])

        assert "每条都能十秒内被否掉" in md
        assert "没有可回验的判据" in md

    def test_搜不到的那条要逐条标出来(self):
        """#16 的渲染面：标记要落在**那条发现底下**，不是只在摘要里报个总数。

        只报总数的话，读的人知道"有 1 条不能信"却不知道是哪一条 ——
        于是要么全信，要么全不信。
        """
        md = self._md([{"id": "B", "severity": "major", "blame": "script", "path": "a.sh",
                        "problem": "p", "evidence": "编的一句话",
                        "evidenceCheck": "unmatched"}])

        assert "这条的判据平台没验上" in md
        assert "在这一批脚本里搜不到" in md

    def test_路径写错的要说清在哪儿找得到(self):
        md = self._md([{"id": "B", "severity": "major", "blame": "script", "path": "a.sh",
                        "problem": "p", "evidence": _GREP,
                        "evidenceCheck": "wrong-path", "evidenceFoundIn": "b.sh"}])

        assert "`b.sh` 里搜到了" in md

    def test_验上的那几条不加噪音(self):
        md = self._md([{"id": "A", "severity": "major", "blame": "script", "path": "a.sh",
                        "problem": "p", "evidence": _GREP, "evidenceCheck": "stitched"}])

        assert "这条的判据平台没验上" not in md


class TestEvidenceOnTheWire:
    """S3.5③：QA 那边的 Claude Code 是照 `evidence` 去 grep 的。"""

    def test_MCP的json每行都带回验结论(self):
        """#20。不给这个键，"搜不到"会被读成"脚本改过了" —— 然后去改脚本。"""
        from datetime import datetime, timezone

        from app.mcp.tools import qa_catalog as qc
        from app.models.qa_catalog_review import QaCatalogReview
        r = QaCatalogReview(
            domain="AGT", domain_name="智能体", status="done", environment_name="e",
            commit_sha="abc1234567", branch="main", actor="cc",
            scenario_count=1, script_count=1,
            result={"verdict": "risky", "summary": "s", "scriptGaps": [
                {"id": "A", "path": "a.sh", "evidence": _GREP, "evidenceCheck": "verbatim"},
                {"id": "B", "path": "a.sh", "evidence": "编的", "evidenceCheck": "unmatched"}],
                "envMissing": [], "catalogGaps": [], "reviewedScripts": []},
        )
        r.created_at = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)

        out = qc._one(r, "json")

        assert [g["evidenceCheck"] for g in out["scriptGaps"]] == ["verbatim", "unmatched"]
        # 汇总跟行同源，取用方不用自己数
        assert out["evidenceCheck"]["verified"] == 1
        assert out["evidenceCheck"]["total"] == 2

    def test_工具说明里要教怎么用这个键(self):
        """键发出去了、没人知道该怎么读，等于没发。"""
        from app.mcp.tools import qa_catalog as qc

        assert "evidenceCheck" in (qc.__doc__ or "")
        assert "wrong-path" in (qc.__doc__ or "")



class TestCatalogAnchorGate:
    """S8.1 · 清单侧拿不出源文锚点的结论 —— 丢掉，但**丢得看得见**。

    这道闸门跟 `TestEvidenceCheck` 那套问的**不是同一件事**：
    那边问「你抄的这段在正文里搜得到吗」（质量），这边问「你到底抄了没有」
    （可证伪性）。差别落在处置上 —— `unmatched` 那条读的人还能拿着那段字
    自己去仓库搜一遍；**一条没有锚点的结论，读的人连从哪儿查起都不知道**。

    只落在 `catalogGaps`，是因为 S8.3 之后那是模型唯一还能写覆盖面的地方，
    而且**是这个模块唯一一处没被任何东西验过的输出**。
    """

    def _one(self, **kw):
        import json
        row = {"scenario": "删除后越权", "why": "清单里没有", "dim": "coverage"}
        row.update(kw)
        return qr.parse_result(json.dumps(
            {"verdict": "bad", "summary": "x", "catalogGaps": [row]}))

    def test_没锚点的清单结论不进正文(self):
        out = self._one()
        assert out["catalogGaps"] == []

    def test_丢掉的整条都在别处摆着(self):
        """**丢弃要留桶。** `qa_evidence_check` 开头那句「删了多少不可知」
        反对的是**静默地删** —— 摊出来它就不成立了。

        存的是整行不是一个数：只有整行还在，读的人才判得出
        「丢的这几条里有没有真发现」。
        """
        out = self._one()
        assert len(out["droppedNoAnchor"]) == 1
        assert out["droppedNoAnchor"][0]["scenario"] == "删除后越权"
        assert out["droppedNoAnchor"][0]["why"] == "清单里没有"

    def test_有锚点的照旧进正文(self):
        out = self._one(evidence='assert_status 200 "$resp"')
        assert out["catalogGaps"][0]["scenario"] == "删除后越权"
        assert out["droppedNoAnchor"] == []

    def test_锚点太短等于没给(self):
        """`fi` / `}` 这种给了等于没给 —— 沿用 `MIN_EVIDENCE_CHARS`。
        不卡长度的话，闸门一行 `}` 就能绕过去，而绕过去之后它长得跟真锚点一样。
        """
        from app.services.qa_evidence_check import MIN_EVIDENCE_CHARS
        assert MIN_EVIDENCE_CHARS > 2                      # 前提变了这条就该重想
        assert self._one(evidence="fi")["catalogGaps"] == []
        assert self._one(evidence="fi")["droppedNoAnchor"][0]["evidence"] == "fi"

    def test_脚本侧维持只标不删(self):
        """**故意的不对称，不是漏了。**

        `scriptGaps` 每行都渲染 `evidenceCheck`、还有 `evidence_stats` 给分母，
        可证伪性已经由「标」兑现了；再删一遍只会丢掉真发现 ——
        没抄到原文不等于那条断言没问题，人还能自己打开那个文件。
        清单侧没有那一列，所以才需要闸门。
        """
        out = qr.parse_result('{"verdict":"bad","scriptGaps":[{"problem":"断言太松"}]}')
        assert out["scriptGaps"][0]["problem"] == "断言太松"
        assert out["droppedNoAnchor"] == []

    def test_提示词把这条写成硬要求(self):
        """闸门不写进提示词就是纯扣分：模型不知道有这回事，
        照旧不抄原文，然后每一趟都丢掉一半 —— 而它本来抄得出来。
        """
        assert "catalogGaps" in qr._SYSTEM
        assert "丢掉" in qr._SYSTEM or "丢弃" in qr._SYSTEM

        # 光有那句白话不够：schema 里得真给它一格。只写要求不给字段，
        # 模型没地方放 —— 于是每一条都被丢，而日志上看起来是"模型不听话"。
        schema = qr._SYSTEM.split('"catalogGaps"', 1)[1].split("]", 1)[0]
        assert '"evidence"' in schema

    def test_合并时按行去重不是按次数累加(self):
        """一条被 5 批各丢一次，页面上得说「丢了 1 条」不是「丢了 5 条」。

        攒出来的数会把闸门的严重程度按批数放大 —— 而批数是切分算法定的，
        跟模型写得好不好一点关系都没有。

        ⚠ 夹具**故意让两批措辞不同**：每批都拿到全量场景清单，同一条会被各批
        各说一遍，说法从来不一样（`_gap_key` 那段注释里的实测数据）。
        两行写得一模一样的话，脚本侧那把「全文拼起来」的键也能去掉 ——
        测试照样绿，而「按清单口径归一」这件事一个字都没测到。
        """
        a = self._one(why="清单里没有")
        b = self._one(why="这个域的清单漏了它")
        m = qr.merge_results([a, b])
        assert len(m["droppedNoAnchor"]) == 1

    def _md(self, **result):
        from app.models.qa_catalog_review import QaCatalogReview
        r = QaCatalogReview(domain="MCP", domain_name="MCP 能力", status="done",
                            environment_name="e", commit_sha="d" * 20, branch="main",
                            actor="a", scenario_count=1, script_count=1,
                            result={"verdict": "ok", "summary": "s", **result})
        return qr.to_markdown(r)

    def test_渲染时没查和查过了一条没丢不是一回事(self):
        """**没查不是零** —— 同 `DIM_SINCE` 那套。

        存量结论没经过这道闸门，渲染成「丢了 0 条」就是替它宣布
        「这些都有出处」，而真相是这一版压根没查。
        """
        无 = self._md(catalogGaps=[])
        assert "没经过锚点检查" in 无

        空 = self._md(catalogGaps=[], droppedNoAnchor=[])
        assert "没经过锚点检查" not in 空

    def test_丢了几条摆在页面上(self):
        有 = self._md(catalogGaps=[], droppedNoAnchor=[{"scenario": "删除后越权"}])
        assert "**1 条**" in 有 and "**2 条**" not in 有
        assert "删除后越权" in 有

    def test_页面上也得说丢了几条(self):
        """**页面是更醒目的那一面。** 只写进导出的 Markdown，等于把这道闸门
        藏进了没人点开的那份里 —— 打开抽屉的人看的是页面。

        三档一档都不能并（同 `DimUnavailable` 那套）：
        `undefined` 是「这一版没查」，`[]` 是「查过了一条没丢」，有东西才摊开。
        """
        import pathlib as _p
        src = (_p.Path(__file__).resolve().parents[2]
               / "frontend/src/pages/qa/QaCatalog.jsx").read_text(encoding="utf-8")
        body = src.split("function DroppedNoAnchor")[1].split("\n}")[0]

        assert "<DroppedNoAnchor res={res} />" in src, "组件写了没挂上去"
        # 存量那一档必须单独存在：合并成 0 就是替它宣布"这些都有出处"
        assert "dn === undefined" in body, "「没查」和「零」并成一档了"
        assert "没经过锚点检查" in body
        # 计数从行本身数，不接受后端另给一个数。
        # ⚠ 断的是**渲染出来的那个数**，不是 `dn.length` 四个字 ——
        # 上面那句 `if (!dn.length) return null` 也含它，只断名字等于没断。
        assert "{dn.length} 条" in body


class Test人话那段的来路要露在页面上:
    """**页面是更醒目的那一面。** 只在导出的 Markdown 里标「这段是拼接版」，
    等于把它藏在没人拉的那份里 —— 打开抽屉的人看的是页面，而且他看到的正是
    那三行空着的重点。

    2026-08-29 验收跑 TEM 时撞到：收口被网关限流打成空响应，退回拼接版，
    明细 14+6 条都在，人看的那一页却是白的。
    """

    FE = (pathlib.Path(__file__).resolve().parents[2]
          / "frontend/src/pages/qa/QaCatalog.jsx")

    def _brief(self):
        src = self.FE.read_text(encoding="utf-8")
        return src.split("function ReviewBrief")[1].split("\nfunction ")[0]

    def test_拼接版当场说而且不许折起来(self):
        b = self._brief()

        assert "res.briefSource === 'stitched'" in b, "页面根本没看这个字段"
        assert "拼接版" in b
        # 这句话的全部作用就是拦住"没重点 = 没问题"这个念头，含糊过去等于没写
        assert "不是这个域没有重点" in b

    def test_存量那一档不许折成收口跑成了(self):
        """老记录没这个键。当成「跑成了」就是把「不知道」渲染成「跑成了」。"""
        b = self._brief()

        assert "!res.briefSource" in b, "「没记」这一档在页面上不存在"
        assert "旧口径" in b


class TestEvidenceOnThePage:
    """页面上也得跟着回验结果走 —— 而且这是**更醒目**的那一面。

    导出的 Markdown 标了、页面没标，等于把「这条判据搜不到」藏在没人点开的那份里。
    打开抽屉的人看的是页面，照着 evidence 动手的人也是在页面上一条条看的。
    """

    FE = (pathlib.Path(__file__).resolve().parents[2]
          / "frontend/src/pages/qa/QaCatalog.jsx")

    def _src(self):
        return self.FE.read_text(encoding="utf-8")

    def test_那句承诺不再是无条件写死的(self):
        """✅「每条都能十秒内被否掉」曾经是无条件打印的。

        一句自己没验过的承诺 —— 正是这个模块存在的意义要抓的那个形状。
        """
        src = self._src()
        body = src.split("function HowIRead")[1].split("function DimUnavailable")[0]

        assert "evidenceStats(res.scriptGaps)" in body, "页面没算回验结果"
        assert "ev.unchecked > 0 ?" in body, "存量结论没有单独一档"
        # 那句 ✅ 只许出现在「没有可回验的判据」那一档里
        head = body.split("ev.unchecked > 0 ?")[0]
        assert "每条都能十秒内被否掉" not in head, "那句 ✅ 还在无条件路径上"

    def test_逐条标在引文旁边而不是只写在汇总里(self):
        """照着 evidence 动手的人是一条一条看的，他不会先回头读页面顶上那句汇总。"""
        src = self._src()
        body = src.split("function ReviewBody")[1]

        assert "g.evidenceCheck && !EV_PASS.includes(g.evidenceCheck)" in body
        assert "先回原文确认再动手" in body
        # 路径写错的要说清在哪儿找得到，否则等于把一条能用的判据当废品扔了
        assert "g.evidenceFoundIn" in body

    def test_存量结论按没验过算不按验过算(self):
        """旧后端 + 新前端也落在这一档（本仓后端故意不带 --reload）。

        少一个对勾没人受伤；多一个假对勾，这一列就再也不能信了。
        """
        src = self._src()
        fn = src.split("function evidenceStats")[1].split("\n}")[0]

        assert "rows.length - known.length" in fn, "没有把「没这个键」算进 unchecked"

    def test_页面的数从行本身来不读后端那份汇总(self):
        """一屏里两个数打架，读的人只会得出「这页的数不能信」。

        后端那份 `coverage.evidence` 是给 MCP / 导出用的，页面列的是这些行，
        就从这些行数 —— 同一个来源就不可能分歧。
        """
        # 只扫**代码行**：注释里写着"不读 coverage.evidence"是说明为什么这么做，
        # 连它一起禁掉，等于逼着后来的人把理由删了才能过测试。
        code = [x for x in self._src().splitlines() if not x.strip().startswith("//")]

        for ln in code:
            assert "coverage.evidence" not in ln and "c.evidence" not in ln, ln

    def test_三档都算搜到不许收紧成一档(self):
        """收紧到只认 `verbatim`，实测 27% 的**真判据**会被打成编造。"""
        src = self._src()
        line = [x for x in src.splitlines() if x.startswith("const EV_PASS")][0]

        for st in ec.PASS_STATES:
            assert st in line, f"{st} 不在前端的通过档里"

    def test_后端加了状态前端不能露出英文键(self):
        """跨语言的那道缝：Python 那边加一档，JSX 这边不加就渲染成 `too_short`。

        这条测试的作用是**在加状态的那一刻就红**，而不是等谁在页面上看见英文键。
        """
        src = self._src()
        block = src.split("const EV_CN = {")[1].split("}")[0]

        for st in ec.STATES:
            assert st in block, f"前端 EV_CN 里没有 {st} 的中文说法"
