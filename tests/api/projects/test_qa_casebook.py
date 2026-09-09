"""QA 仓「用例册」（只读）—— 「QA 对账」页里那块「对账表 / 用例册」切到用例册看的接口。

和对账清单同住一个 QA 仓、共用同一次「拉取最新」的 fetch，所以这条接口 refresh=False。
用一个本地临时 git 仓当"QA 仓"：`clone --bare <本地路径>` 不需要网络，整条链路能真跑一遍。

Test ID: qa-casebook-API-001
Priority: P1
"""
import json
import shutil
import subprocess

import pytest

from app.models.project import ProjectMember
from app.services import qa_catalog
from tests.conftest import create_test_user, make_auth_headers

# 两个已知域（AUT/TEM）+ 一个 CASEBOOK_MOD 里没有的域码（ZZZ），验未知域不崩、排在后面。
# 故意混一条 `**加粗**` 和一个草稿（_draft），外加一步 k:true 的关键步骤。
AUT_CASES = [
    {
        "id": "AUT-02", "p": "P1", "r": 5, "tier": "ui", "st": "done",
        "t": "登录失败要**锁定**账号", "o": "连错几次就锁，别让人无限试",
        "planes": ["控制台", "认证服务"],
        "why": ["防撞库"],
        "steps": [
            {"do": "连续输错 5 次密码", "see": "第 6 次直接提示已锁定", "k": True},
            {"do": "等 15 分钟再试", "see": "能正常登录"},
        ],
        "notes": [{"k": "阈值", "v": "5 次 / 15 分钟"}],
        "src": "docs/auth.md",
    },
    {
        "id": "AUT-01", "p": "P0", "r": 8, "tier": "scenario", "st": "todo",
        "t": "越权访问被拒", "o": "别的团队的资源看不到",
        "planes": ["数据面"], "why": ["还没写脚本"], "steps": [], "notes": [],
        "src": "", "_draft": True,
    },
]
TEM_CASES = [
    {"id": "TEM-01", "p": "P2", "r": 3, "tier": "ui", "st": "done",
     "t": "改成员昵称", "o": "", "planes": [], "why": [], "steps": [], "notes": [], "src": ""},
]
ZZZ_CASES = [
    {"id": "ZZZ-01", "p": "P3", "r": 1, "tier": "ui", "st": "dead",
     "t": "已经不做的场景", "o": "", "planes": [], "why": [], "steps": [], "notes": [], "src": ""},
]


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def fake_qa_repo_with_casebook(tmp_path, monkeypatch) -> str:
    work = tmp_path / "qa"
    data_dir = work / "docs" / "qa" / "casebook" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "aut.json").write_text(json.dumps(AUT_CASES, ensure_ascii=False), encoding="utf-8")
    (data_dir / "tem.json").write_text(json.dumps(TEM_CASES, ensure_ascii=False), encoding="utf-8")
    (data_dir / "zzz.json").write_text(json.dumps(ZZZ_CASES, ensure_ascii=False), encoding="utf-8")
    # 一个坏文件：进 parseErrors，但不该拖垮其余的域
    (data_dir / "broken.json").write_text("{ not valid json", encoding="utf-8")
    _git(work, "init", "-q")
    _git(work, "add", "-A")
    _git(work, "-c", "user.email=qa@test", "-c", "user.name=qa", "commit", "-q", "-m", "init")

    monkeypatch.setattr(qa_catalog.settings, "qa_repo_cache_dir", str(tmp_path / "cache"))
    qa_catalog._CACHE.clear()
    qa_catalog._CASEBOOK_CACHE.clear()
    yield str(work)
    qa_catalog._CACHE.clear()
    qa_catalog._CASEBOOK_CACHE.clear()
    shutil.rmtree(tmp_path / "cache", ignore_errors=True)


@pytest.fixture
def fake_qa_repo_no_casebook(tmp_path, monkeypatch) -> str:
    """配了 QA 仓，但仓里没有用例册目录 —— 该是 available=False，不是报错。"""
    work = tmp_path / "qa2"
    (work / "docs").mkdir(parents=True)
    (work / "docs" / "scenarios.md").write_text("# 空清单\n", encoding="utf-8")
    _git(work, "init", "-q")
    _git(work, "add", "-A")
    _git(work, "-c", "user.email=qa@test", "-c", "user.name=qa", "commit", "-q", "-m", "init")
    monkeypatch.setattr(qa_catalog.settings, "qa_repo_cache_dir", str(tmp_path / "cache"))
    qa_catalog._CACHE.clear()
    qa_catalog._CASEBOOK_CACHE.clear()
    yield str(work)
    qa_catalog._CACHE.clear()
    qa_catalog._CASEBOOK_CACHE.clear()
    shutil.rmtree(tmp_path / "cache", ignore_errors=True)


async def _project_with_manager(client, db_session, name: str):
    admin = await create_test_user(db_session, username=f"{name}_admin", role="admin")
    admin_headers, _ = make_auth_headers(admin)
    r = await client.post("/api/projects", headers=admin_headers, json={"name": name})
    assert r.status_code == 201, r.text
    project_id = r.json()["data"]["id"]
    pa = await create_test_user(db_session, username=f"{name}_pa", role="user")
    db_session.add(ProjectMember(project_id=project_id, user_id=pa.id, role="manager"))
    await db_session.flush()
    return project_id, make_auth_headers(pa)[0]


class TestQaCasebook:
    """GET /api/projects/{id}/qa-catalog/casebook"""

    @pytest.mark.asyncio
    async def test_未配置时回configured_false不报错(self, client, db_session):
        project_id, headers = await _project_with_manager(client, db_session, "cbk1")

        r = await client.get(f"/api/projects/{project_id}/qa-catalog/casebook", headers=headers)

        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["configured"] is False
        assert data["available"] is False
        assert data["error"] is None
        assert data["domains"] == []

    @pytest.mark.asyncio
    async def test_配了仓但没用例册回available_false(self, client, db_session, fake_qa_repo_no_casebook):
        project_id, headers = await _project_with_manager(client, db_session, "cbk2")
        await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                         headers=headers, json={"url": fake_qa_repo_no_casebook})

        r = await client.get(f"/api/projects/{project_id}/qa-catalog/casebook", headers=headers)

        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["configured"] is True   # 仓配了
        assert data["available"] is False   # 只是这个仓没写用例册
        assert data["error"] is None        # 不是读失败

    @pytest.mark.asyncio
    async def test_读出用例册_分域_计数_加粗保留(self, client, db_session, fake_qa_repo_with_casebook):
        project_id, headers = await _project_with_manager(client, db_session, "cbk3")
        await client.put(f"/api/projects/{project_id}/qa-catalog/config",
                         headers=headers, json={"url": fake_qa_repo_with_casebook})

        r = await client.get(f"/api/projects/{project_id}/qa-catalog/casebook", headers=headers)

        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["configured"] is True and data["available"] is True

        # 计数：4 条（坏文件那条不算），按状态/层/草稿分桶
        counts = data["counts"]
        assert counts["total"] == 4
        assert counts["byStatus"] == {"done": 2, "todo": 1, "dead": 1}
        assert counts["byTier"] == {"ui": 3, "scenario": 1}
        assert counts["draft"] == 1

        # 坏文件进 parseErrors，其余域照读
        assert len(data["parseErrors"]) == 1
        assert "broken.json" in data["parseErrors"][0]["path"]

        # 域排序：已知域按 CASEBOOK_MOD 顺序（AUT 在 TEM 前），未知域 ZZZ 垫底
        codes = [d["code"] for d in data["domains"]]
        assert codes == ["AUT", "TEM", "ZZZ"]
        aut = data["domains"][0]
        assert aut["short"] == "认证账号" and aut["name"] == "认证与账号"
        zzz = data["domains"][2]
        assert zzz["short"] == "ZZZ"  # 未知域码用自身兜底

        # 模块内按编号数字段排序：AUT-01 在 AUT-02 前
        assert [c["id"] for c in aut["cases"]] == ["AUT-01", "AUT-02"]

        # **加粗** 标记原样透传，绝不在后端拼成 HTML（XSS 由前端 <Rich> 转义兜底）
        aut02 = aut["cases"][1]
        assert aut02["t"] == "登录失败要**锁定**账号"
        assert "<b>" not in json.dumps(data) and "<strong>" not in json.dumps(data)

        # 关键步骤 k:true 透传给前端标红
        assert aut02["steps"][0]["k"] is True
        assert aut02["steps"][1]["k"] is False
        # 草稿标记归一化成 draft
        assert aut["cases"][0]["draft"] is True
