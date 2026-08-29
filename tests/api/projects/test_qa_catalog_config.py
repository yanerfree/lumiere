"""QA 仓配置（只读）—— 「QA 场景清单」页里那条保存接口。

用一个本地临时 git 仓当"QA 仓"：`clone --bare <本地路径>` 不需要网络，
所以整条链路能真跑一遍——保存配置 → 立刻按新配置读一次 → 分支/清单/脚本全自动识别。

Test ID: qa-catalog-API-001
Priority: P0
"""
import shutil
import subprocess

import pytest

from app.models.project import ProjectMember
from app.services import qa_catalog
from tests.conftest import create_test_user, make_auth_headers

CATALOG = """\
# 验收场景清单

| 域码 | 域名 |
|---|---|
| `SMK` | 冒烟 |
| `AUT` | 权限 |

| ID | 场景 | P | R | 层 | 状 |
|---|---|---|---|---|---|
| SMK-01 | 登录成功 | P0 | 6 | smoke | ✅ |
| SMK-02 | 登录失败锁定 | P0 | 5 | smoke | ⬜ |
| AUT-01 | 越权访问被拒 | P1 | 7 | api | ✅ |
| AUT-02 | 过期 token | P2 | 3 | api | ❌ |
| AUT-03 | 刷新 token | P1 | 4 | api | ⬜ |
"""


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def fake_qa_repo(tmp_path, monkeypatch) -> str:
    """一个够真的 QA 仓：清单 + 两个带 @scenario 的脚本 + 一个模板干扰项。"""
    work = tmp_path / "qa"
    (work / "docs").mkdir(parents=True)
    (work / "api").mkdir()
    (work / "templates").mkdir()
    (work / "docs" / "scenarios.md").write_text(CATALOG, encoding="utf-8")
    (work / "docs" / "README.md").write_text(
        "清单长这样：\n\n| SMK-01 | 举个例子 | P0 | 6 | smoke | ✅ |\n", encoding="utf-8")
    (work / "api" / "smoke.sh").write_text(
        "#!/usr/bin/env bash\n# @scenario SMK-01\n# @tier smoke\n", encoding="utf-8")
    (work / "api" / "authz.sh").write_text(
        "#!/usr/bin/env bash\n# @scenario AUT-01\n# @known-bug GL#530\n", encoding="utf-8")
    (work / "templates" / "case.sh.tmpl").write_text(
        "# @scenario XXX-01  ← 覆盖哪些场景\n", encoding="utf-8")
    _git(work, "init", "-q")
    _git(work, "add", "-A")
    _git(work, "-c", "user.email=qa@test", "-c", "user.name=qa", "commit", "-q", "-m", "init")

    # 缓存挪到 tmp，别污染 backend/.qa-repos
    monkeypatch.setattr(qa_catalog.settings, "qa_repo_cache_dir", str(tmp_path / "cache"))
    qa_catalog._CACHE.clear()
    yield str(work)
    qa_catalog._CACHE.clear()
    shutil.rmtree(tmp_path / "cache", ignore_errors=True)


async def _project_with_pa(client, db_session, name: str):
    """建项目 + 一个项目管理员 + 一个普通成员 + 一个系统游客。

    2026-08-29：原来这里的"只读主体"是项目角色 `guest`，那一档已退役。
    现在拆成两个主体，因为它们被**不同的东西**拦住，混成一个就分不清哪条腿断了：
    - 普通成员：项目角色守卫拦（配置是 TIER_ADMIN，只有 manager 能改）→ PROJECT_ROLE_DENIED
    - 系统游客：账号级封顶拦（非 GET 闸门）→ GUEST_READONLY，且它在项目里挂的就是 member
    """
    admin = await create_test_user(db_session, username=f"{name}_admin", role="admin")
    admin_headers, _ = make_auth_headers(admin)
    r = await client.post("/api/projects", headers=admin_headers, json={"name": name})
    assert r.status_code == 201, r.text
    project_id = r.json()["data"]["id"]

    pa = await create_test_user(db_session, username=f"{name}_pa", role="user")
    member = await create_test_user(db_session, username=f"{name}_member", role="user")
    guest = await create_test_user(db_session, username=f"{name}_guest", role="guest")
    db_session.add(ProjectMember(project_id=project_id, user_id=pa.id, role="manager"))
    db_session.add(ProjectMember(project_id=project_id, user_id=member.id, role="member"))
    db_session.add(ProjectMember(project_id=project_id, user_id=guest.id, role="member"))
    await db_session.flush()
    return (project_id, make_auth_headers(pa)[0],
            make_auth_headers(member)[0], make_auth_headers(guest)[0])


class TestQaCatalogConfig:
    """PUT /api/projects/{id}/qa-catalog/config"""

    @pytest.mark.asyncio
    async def test_未配置时只回表头不报错(self, client, db_session):
        project_id, pa_headers, _, _ = await _project_with_pa(client, db_session, "qacfg1")

        r = await client.get(f"/api/projects/{project_id}/qa-catalog", headers=pa_headers)

        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["configured"] is False
        assert data["error"] is None
        assert data["scenarios"] == []
        # 配置原样回给弹窗做回填：没配就是一份空表单
        assert data["config"] == {"url": "", "branch": "", "catalogPath": "", "caseGlobs": []}

    @pytest.mark.asyncio
    async def test_只填仓库地址就能读出来(self, client, db_session, fake_qa_repo):
        project_id, pa_headers, _, _ = await _project_with_pa(client, db_session, "qacfg2")

        r = await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                             headers=pa_headers, json={"url": fake_qa_repo})

        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["configured"] is True
        assert data["error"] is None, data["error"]

        repo = data["repo"]
        assert repo["branchAuto"] is True                       # 分支没填 → 跟仓库自己的默认分支
        assert repo["catalogAuto"] is True
        assert repo["catalogPath"] == "docs/scenarios.md"       # README 里那两行例子不该被选中
        assert repo["caseDiscovery"] == "grep"

        s = data["summary"]
        assert s["total"] == 4                                  # 分母不含已废弃（❌ 那条）
        assert s["covered"] == 2 and s["gap"] == 2 and s["deprecated"] == 1
        assert s["scripts"] == 2                                # 模板不算用例
        assert s["orphanScripts"] == 0                          # 模板里的 XXX-01 不该冒出来
        assert s["claimedButUncovered"] == 0
        assert s["coveredWithBugs"] == 1                        # AUT-01 有脚本，但脚本头挂着 GL#530
        assert s["riskMismatch"] == 0
        assert data["orphanScriptList"] == []

        # 页面靠这两个字段找「黑洞域」：SMK-02 是 P0 且待补
        smk = next(d for d in data["domains"] if d["code"] == "SMK")
        assert (smk["gap"], smk["p0Gap"]) == (1, 1)

        smk01 = next(x for x in data["scenarios"] if x["id"] == "SMK-01")
        assert [c["path"] for c in smk01["scripts"]] == ["api/smoke.sh"]
        assert smk01["domainName"] == "冒烟"                     # 域码表也读出来了
        aut01 = next(x for x in data["scenarios"] if x["id"] == "AUT-01")
        assert aut01["knownBugs"] == ["GL#530"]

    @pytest.mark.asyncio
    async def test_清空url等于取消配置(self, client, db_session, fake_qa_repo):
        project_id, pa_headers, _, _ = await _project_with_pa(client, db_session, "qacfg3")
        await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                         headers=pa_headers, json={"url": fake_qa_repo})

        r = await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                             headers=pa_headers, json={"url": ""})

        assert r.status_code == 200, r.text
        assert r.json()["data"]["configured"] is False
        # 页面刷新后也得是"没配"，不是"配了但读不出来"
        r2 = await client.get(f"/api/projects/{project_id}/qa-catalog", headers=pa_headers)
        assert r2.json()["data"]["configured"] is False

    @pytest.mark.asyncio
    async def test_配错了报错而不是静默空清单(self, client, db_session, fake_qa_repo):
        project_id, pa_headers, _, _ = await _project_with_pa(client, db_session, "qacfg4")

        r = await client.put(f"/api/projects/{project_id}/qa-catalog/config", headers=pa_headers,
                             json={"url": fake_qa_repo, "branch": "no-such-branch"})

        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["configured"] is True
        assert data["error"] and "no-such-branch" in data["error"]
        assert data["scenarios"] == []

    @pytest.mark.asyncio
    async def test_普通成员改不了配置(self, client, db_session, fake_qa_repo):
        """QA 仓地址是项目设置，只有 manager 能改 —— 成员点得到也改不动。"""
        project_id, _, member_headers, _ = await _project_with_pa(client, db_session, "qacfg5")

        r = await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                             headers=member_headers, json={"url": fake_qa_repo})

        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "PROJECT_ROLE_DENIED"

    @pytest.mark.asyncio
    async def test_游客改不了配置_而且是另一条腿拦的(self, client, db_session, fake_qa_repo):
        """同样 403，但**原因不同** —— 这条分得清才有意义。

        游客在项目里挂的是 member，可 manager 这一档它本来就到不了；
        真正先拦下它的是账号级的非 GET 闸门。断言 error.code 就是在钉「哪条腿在起作用」：
        哪天封顶退化成只在权限响应里自报，这里会变成 PROJECT_ROLE_DENIED —— 状态码还是 403，
        只看状态码就完全看不出来。
        """
        project_id, _, _, guest_headers = await _project_with_pa(client, db_session, "qacfg6")

        r = await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                             headers=guest_headers, json={"url": fake_qa_repo})

        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "GUEST_READONLY"
