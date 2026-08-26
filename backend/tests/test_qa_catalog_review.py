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
        assert out["nextUp"][0]["id"] == "AGT-12"

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

    def test_每一项最多留六条(self):
        gaps = ",".join(f'{{"id":"A-{i}"}}' for i in range(20))
        out = qr.parse_result('{"verdict":"bad","scriptGaps":[' + gaps + ']}')

        assert len(out["scriptGaps"]) == 6


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
