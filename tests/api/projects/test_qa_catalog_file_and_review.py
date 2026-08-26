"""QA 场景清单的两个新接口：打开脚本看内容、对一个域发起 AI 评审。

用本地临时 git 仓当"QA 仓"（`clone --bare <本地路径>` 不需要网络），
所以「读文件」这条链路是真跑的：配置 → 解析 → git show → 回内容。

评审那条只验**发起**这一半（建记录、环境跟着落库、越权拦住、重复点不重复跑）。
后台那趟自己开 session，看不见这里未提交的测试数据，跑不完是预期的 ——
真正评得准不准由 `backend/tests/test_qa_catalog_review.py` 逐块盯。

Test ID: qa-catalog-API-002
Priority: P0
"""
import shutil
import subprocess

import pytest

from app.models.environment import Environment
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
# 自动识别清单要求一份 .md 里至少有 5 行场景（qa_catalog.py:242）——
# 少于这个数的表格在真仓库里多半是文档里的举例，不是清单本身

SMOKE_SH = """\
#!/usr/bin/env bash
# @scenario SMK-01
# @tier smoke
set -euo pipefail
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "$BASE_URL/login"
"""


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def fake_qa_repo(tmp_path, monkeypatch) -> str:
    work = tmp_path / "qa"
    (work / "docs").mkdir(parents=True)
    (work / "api").mkdir()
    (work / "docs" / "scenarios.md").write_text(CATALOG, encoding="utf-8")
    (work / "api" / "smoke.sh").write_text(SMOKE_SH, encoding="utf-8")
    (work / "api" / "authz.sh").write_text(
        "#!/usr/bin/env bash\n# @scenario AUT-01\n", encoding="utf-8")
    # 这份**不该**能通过接口读到：清单没引用它
    (work / "secrets.env").write_text("GITLAB_TOKEN=glpat-real-secret\n", encoding="utf-8")
    _git(work, "init", "-q")
    _git(work, "add", "-A")
    _git(work, "-c", "user.email=qa@test", "-c", "user.name=qa", "commit", "-q", "-m", "init")

    monkeypatch.setattr(qa_catalog.settings, "qa_repo_cache_dir", str(tmp_path / "cache"))
    qa_catalog._CACHE.clear()
    yield str(work)
    qa_catalog._CACHE.clear()
    shutil.rmtree(tmp_path / "cache", ignore_errors=True)


async def _project(client, db_session, name: str, *, with_env=True):
    admin = await create_test_user(db_session, username=f"{name}_admin", role="admin")
    admin_headers, _ = make_auth_headers(admin)
    r = await client.post("/api/projects", headers=admin_headers, json={"name": name})
    assert r.status_code == 201, r.text
    project_id = r.json()["data"]["id"]

    pa = await create_test_user(db_session, username=f"{name}_pa", role="user")
    guest = await create_test_user(db_session, username=f"{name}_guest", role="user")
    db_session.add(ProjectMember(project_id=project_id, user_id=pa.id, role="project_admin"))
    db_session.add(ProjectMember(project_id=project_id, user_id=guest.id, role="guest"))
    await db_session.flush()

    env_id = None
    if with_env:
        # 新建项目会自动铺 4 个环境（project_defaults），取排第一个那个
        e = (await db_session.execute(
            Environment.__table__.select().where(Environment.__table__.c.project_id == project_id)
            .order_by(Environment.__table__.c.sort_order).limit(1))).first()
        env_id = str(e.id) if e else None
    return project_id, make_auth_headers(pa)[0], make_auth_headers(guest)[0], env_id


class TestReadQaFile:
    """GET /api/projects/{id}/qa-catalog/file"""

    @pytest.mark.asyncio
    async def test_点开脚本能看到原文和头部声明(self, client, db_session, fake_qa_repo):
        project_id, pa, _, _ = await _project(client, db_session, "qafile1")
        await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                         headers=pa, json={"url": fake_qa_repo})

        r = await client.get(f"/api/projects/{project_id}/qa-catalog/file",
                             headers=pa, params={"path": "api/smoke.sh"})

        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert "@scenario SMK-01" in d["content"]
        assert d["header"]["ids"] == ["SMK-01"]      # 抽屉上那排徽标
        assert d["header"]["tier"] == "smoke"
        assert d["truncated"] is False and d["lines"] > 1
        assert len(d["commitSha"]) >= 7

    @pytest.mark.asyncio
    async def test_清单文件本身也能点开(self, client, db_session, fake_qa_repo):
        project_id, pa, _, _ = await _project(client, db_session, "qafile2")
        await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                         headers=pa, json={"url": fake_qa_repo})

        r = await client.get(f"/api/projects/{project_id}/qa-catalog/file",
                             headers=pa, params={"path": "docs/scenarios.md"})

        assert r.status_code == 200, r.text
        assert "SMK-01" in r.json()["data"]["content"]

    @pytest.mark.asyncio
    async def test_清单没引用的文件读不到(self, client, db_session, fake_qa_repo):
        """仓库是别人的。白名单之外的一律不给 —— 哪怕文件真实存在。"""
        project_id, pa, _, _ = await _project(client, db_session, "qafile3")
        await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                         headers=pa, json={"url": fake_qa_repo})

        for sneaky in ("secrets.env", "../../etc/passwd", "docs/../secrets.env"):
            r = await client.get(f"/api/projects/{project_id}/qa-catalog/file",
                                 headers=pa, params={"path": sneaky})
            assert r.status_code == 404, f"{sneaky} 竟然读得到：{r.text}"
            assert "glpat-real-secret" not in r.text

    @pytest.mark.asyncio
    async def test_没配QA仓时明说而不是404(self, client, db_session):
        project_id, pa, _, _ = await _project(client, db_session, "qafile4")

        r = await client.get(f"/api/projects/{project_id}/qa-catalog/file",
                             headers=pa, params={"path": "api/smoke.sh"})

        assert r.status_code == 400, r.text
        assert "还没配" in r.text


class TestStartQaReview:
    """POST /api/projects/{id}/qa-catalog/reviews —— 只做域级。"""

    @pytest.mark.asyncio
    async def test_发起后环境和commit跟着落库(self, client, db_session, fake_qa_repo):
        """环境是结论的一部分：同一批脚本换个环境结论就可能不一样。"""
        project_id, pa, _, env_id = await _project(client, db_session, "qarev1")
        await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                         headers=pa, json={"url": fake_qa_repo})

        r = await client.post(f"/api/projects/{project_id}/qa-catalog/reviews",
                              headers=pa, json={"domain": "SMK", "envId": env_id})

        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["domain"] == "SMK" and d["domainName"] == "冒烟"
        assert d["status"] in ("queued", "running")
        assert d["environmentId"] == env_id and d["environmentName"]
        assert d["commitSha"] and d["scenarioCount"] == 2      # SMK-01 + SMK-02
        assert d["scriptCount"] == 1

        got = await client.get(f"/api/projects/{project_id}/qa-catalog/reviews/{d['id']}",
                               headers=pa)
        assert got.status_code == 200 and got.json()["data"]["domain"] == "SMK"

    @pytest.mark.asyncio
    async def test_没填环境就挑项目默认那个(self, client, db_session, fake_qa_repo):
        project_id, pa, _, env_id = await _project(client, db_session, "qarev2")
        await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                         headers=pa, json={"url": fake_qa_repo})

        r = await client.post(f"/api/projects/{project_id}/qa-catalog/reviews",
                              headers=pa, json={"domain": "AUT"})

        assert r.status_code == 200, r.text
        assert r.json()["data"]["environmentId"] == env_id

    @pytest.mark.asyncio
    async def test_同一个域连点两下不重复跑(self, client, db_session, fake_qa_repo):
        """不拦的话就是两次模型调用，而且后回来的会盖掉先回来的。"""
        project_id, pa, _, _ = await _project(client, db_session, "qarev3")
        await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                         headers=pa, json={"url": fake_qa_repo})

        first = await client.post(f"/api/projects/{project_id}/qa-catalog/reviews",
                                  headers=pa, json={"domain": "SMK"})
        second = await client.post(f"/api/projects/{project_id}/qa-catalog/reviews",
                                   headers=pa, json={"domain": "SMK"})

        assert first.status_code == second.status_code == 200
        assert first.json()["data"]["id"] == second.json()["data"]["id"]

    @pytest.mark.asyncio
    async def test_清单里没有的域直接拦住(self, client, db_session, fake_qa_repo):
        project_id, pa, _, _ = await _project(client, db_session, "qarev4")
        await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                         headers=pa, json={"url": fake_qa_repo})

        r = await client.post(f"/api/projects/{project_id}/qa-catalog/reviews",
                              headers=pa, json={"domain": "ZZZ"})

        assert r.status_code == 400, r.text
        assert "ZZZ" in r.text

    @pytest.mark.asyncio
    async def test_别的项目的环境用不了(self, client, db_session, fake_qa_repo):
        project_id, pa, _, _ = await _project(client, db_session, "qarev5")
        _other, _, _, other_env = await _project(client, db_session, "qarev5b")
        await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                         headers=pa, json={"url": fake_qa_repo})

        r = await client.post(f"/api/projects/{project_id}/qa-catalog/reviews",
                              headers=pa, json={"domain": "SMK", "envId": other_env})

        assert r.status_code == 400, r.text

    @pytest.mark.asyncio
    async def test_guest发起不了评审(self, client, db_session, fake_qa_repo):
        project_id, pa, guest, _ = await _project(client, db_session, "qarev6")
        await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                         headers=pa, json={"url": fake_qa_repo})

        r = await client.post(f"/api/projects/{project_id}/qa-catalog/reviews",
                              headers=guest, json={"domain": "SMK"})

        assert r.status_code == 403, r.text

    @pytest.mark.asyncio
    async def test_列表每个域只回最近一次(self, client, db_session, fake_qa_repo):
        """页面上域那一行只有一个徽标位，回多了前端还得自己挑。"""
        project_id, pa, guest, _ = await _project(client, db_session, "qarev7")
        await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                         headers=pa, json={"url": fake_qa_repo})
        await client.post(f"/api/projects/{project_id}/qa-catalog/reviews",
                          headers=pa, json={"domain": "SMK"})
        await client.post(f"/api/projects/{project_id}/qa-catalog/reviews",
                          headers=pa, json={"domain": "AUT"})

        r = await client.get(f"/api/projects/{project_id}/qa-catalog/reviews", headers=guest)

        assert r.status_code == 200, r.text
        rows = r.json()["data"]["reviews"]
        assert sorted(x["domain"] for x in rows) == ["AUT", "SMK"]
        assert len({x["domain"] for x in rows}) == len(rows)
