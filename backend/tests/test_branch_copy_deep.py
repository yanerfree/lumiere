"""「从分支复制」按钮上写着"深拷贝，含步骤和场景"，实测只拷了用例那一行。

复制出来的是个空壳：接口场景、场景变量、UI 脚本一个都没跟过去。
新分支上还得让 CC 把脚本全部重推一遍 —— 那这个功能等于没有。
另一半同样要钉住：**执行状态不许跟着走**。在新分支上一次都没跑过，
把 api_status=passed 拷过去就是凭空一条"验过了"。
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest

from app.services.case_service import (_copy_case_assets, copy_case_side_assets,
                                       copy_cases_from_branch)


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    """按顺序喂查询结果：场景变量 → 接口场景 → 每条场景的步骤 → UI 脚本。"""

    def __init__(self, *batches):
        self._queue = list(batches)
        self.added = []

    async def execute(self, _stmt):
        return _Res(self._queue.pop(0) if self._queue else [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


def _kind(session, name):
    return [o for o in session.added if type(o).__name__ == name]


_SRC = SimpleNamespace(id=uuid.uuid4(), case_code="TC-OLD-00007")
_NEW = SimpleNamespace(id=uuid.uuid4(), case_code="TC-NEW-00001")


@pytest.mark.asyncio
async def test_场景变量步骤脚本三样都跟着走():
    var = SimpleNamespace(name="svcName", kind="fixed", value_template="svc-${ts}",
                          var_type="string", description=None)
    sc = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), branch_id=uuid.uuid4(),
                         code="TC-OLD-00007", title="订阅审批", priority="P1", source="ai",
                         status="passed", description=None, created_by=None, env_variables=None)
    st = SimpleNamespace(sort_order=1, group_name=None, name="登录", method="POST",
                         url="${BASE_URL}${LOGIN_URL}", headers=None, body={"u": "a"},
                         assertions=[{"type": "status", "value": 200}],
                         variables_extract=None, enabled=True, wait_ms=0,
                         retry_timeout_ms=0, retry_interval_ms=300)
    script = SimpleNamespace(script_type="ui", language="python", file_name="test_ui.py",
                             func_name="test_x", content="# ...", status="active",
                             version=3, source="ai")
    s = _Session([var], [script], [sc], [st])
    await _copy_case_assets(s, _SRC, _NEW, uuid.uuid4())

    assert [v.name for v in _kind(s, "ScenarioVariable")] == ["svcName"]
    assert [x.name for x in _kind(s, "ApiTestStep")] == ["登录"], "步骤没拷 = 场景是空的"
    assert [x.file_name for x in _kind(s, "Script")] == ["test_ui.py"]


@pytest.mark.asyncio
async def test_场景编号跟新用例走且回落草稿():
    """一个用例一条场景，编号沿用旧的会跟源分支撞车；status 沿用等于凭空一条「验过了」。"""
    sc = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), branch_id=uuid.uuid4(),
                         code="TC-OLD-00007", title="t", priority="P1", source="ai",
                         status="passed", description=None, created_by=None, env_variables=None)
    s = _Session([], [], [sc], [])
    await _copy_case_assets(s, _SRC, _NEW, uuid.uuid4())
    new_sc = _kind(s, "ApiTestScenario")[0]
    assert new_sc.code == "TC-NEW-00001" and new_sc.status == "draft"
    assert new_sc.source_case_id == _NEW.id, "没挂到新用例上，新分支上点进去看不到"


def test_只拷活跃脚本():
    """历史版本跟着过去，新分支上一个用例挂好几份脚本，跑哪份说不清。"""
    assert 'Script.status == "active"' in inspect.getsource(copy_case_side_assets)


def test_承诺和落款跟着走():
    """不带过去，复制出来的用例「没人确认过预期、也不知道要做到哪一维」，
    在新分支上第一次跑就会被门禁挡住，人还得把几百字依据重填一遍。"""
    src = inspect.getsource(copy_cases_from_branch)
    for f in ("target_level=", "target_level_reason=", "expected_confirmed_note=",
              "expected_confirmed_actor="):
        assert f in src, f"{f} 没跟着复制"


def test_执行状态不许跟着走():
    """三维状态由执行事实推进，复制不是执行。"""
    src = inspect.getsource(copy_cases_from_branch)
    for f in ("api_status=", "ui_status=", "last_run_"):
        assert f not in src, f"{f} 被拷了 —— 新分支上凭空多一条「验过了」"
    assert "sync_manual_status(new_case)" in src, "手工维状态要按步骤/预期重算"


# ── 建分支时的整分支拷贝：同一个坑，别只修一半 ────────────────────────

def test_建分支拷贝也带走脚本和场景变量():
    """两条复制路径：用例级「从分支复制」、建分支时勾「用例」。
    只修前者，建出来的新分支仍然是一批空壳 —— 而后者才是常用的那条。
    `script_ref_file` 一直跟着拷，脚本正文不拷就是个指向不存在文件的空指针。
    """
    from app.services import branch_copy_service
    src = inspect.getsource(branch_copy_service._copy_cases)
    assert "copy_case_side_assets" in src, "建分支拷贝没带 UI 脚本和场景变量"


def test_建分支拷贝也带走承诺和落款():
    from app.services import branch_copy_service
    src = inspect.getsource(branch_copy_service._copy_cases)
    for f in ("target_level=", "expected_confirmed_note="):
        assert f in src, f"{f} 没跟着复制"


def test_两条路径共用一个搬运工():
    """各写一份必然分叉：修了一边、另一边继续漏。"""
    from app.services import branch_copy_service, case_service
    for mod in (branch_copy_service._copy_cases, case_service._copy_case_assets):
        assert "copy_case_side_assets" in inspect.getsource(mod)
