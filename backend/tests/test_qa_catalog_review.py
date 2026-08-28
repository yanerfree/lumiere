"""QA 域级 AI 评审：环境变量对账、prompt 组装、结果解析。

这一层不碰数据库也不打模型 —— 真正值得盯的是三件**能算错**的事：
① 「环境缺这个变量」是不是真缺（误报一次，人就再也不信这一列）；
② prompt 里有没有把变量**值**带出去（带出去就是泄密）；
③ 模型胡说时会不会退化成一份"没发现问题"的空壳（那比报错难查得多）。

Test ID: qa-catalog-review-UT-001
Priority: P0
"""
import pytest

from app.services import qa_catalog_review as qr

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
        out = qr.parse_result('{"verdict":"ok","catalogGaps":["缺删除后越权"]}')

        assert out["catalogGaps"][0]["problem"] == "缺删除后越权"

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
