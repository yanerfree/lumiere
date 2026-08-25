"""模块体检的第三块：覆盖分布（`intake_gate.check_coverage`）。

这套判据 2026-08-08 就写好了，但**从出生起没有调用点** —— 它当初想在"整批入库"
时拦一次，而回推是一条一条来的（`tb_create_case` 一次一条），`n=1` 时两道闸恒不
触发。判据没错，错的是家：手里真有"一批"的时刻是**模块评审**。
2026-08-25 挪到 `review/checkup.py`，这个文件钉住"挪过去了、还在算、门槛没变"。
"""
import pytest

from app.services.intake_gate import P0_QUOTA, check_coverage


def _cases(n, priority="P2", title="创建服务成功"):
    return [{"title": f"{title}{i}", "priority": priority, "expected_result": ""}
            for i in range(n)]


def test_一条一条进的时候什么都不报():
    """**这就是它原来没用的原因**，得先钉住这个事实。

    入库门禁一次只拿到一条，`n=1`：P0 门槛是 5、倾斜门槛是 8，两道都过不去。
    所以把这套判据挂在入库口上，等于挂了个永远不响的铃。
    """
    out = check_coverage(_cases(1, priority="P0"))
    assert out["notes"] == []
    assert out["p0"]["count"] == 1          # 数还是数了，只是不出声
    assert "ops" not in out                 # 倾斜连算都不算


def test_P0_超配额在够多条的时候才报():
    """门槛 10 条是防误报：小模块里配额只有 1，第 2 条 P0 就报纯属噪音。"""
    # 只看 P0 那条 —— 9 条同名"创建"当然还会报覆盖倾斜，那是另一道判据
    assert not [n for n in check_coverage(_cases(9, priority="P0"))["notes"] if "P0" in n]

    out = check_coverage(_cases(10, priority="P0"))
    assert len(out["notes"]) >= 1
    assert "P0" in out["notes"][0]
    assert out["p0"]["ratio"] == 1.0
    assert out["p0"]["cap"] == P0_QUOTA


def test_十三条里两条P0不算超():
    """**实测抓到的误报**：`p0 / n > 0.15` 判 2/13 = 15.4% 超标。

    就那 0.4 个百分点，把一个完全正常的分级报成"什么都 P0 等于没分级"。
    配额要**向上取整再比个数**：13 条按 15% 算配额 2，放它过；第 3 条才报。
    这类差一点点的误报比漏报更伤 —— 报告上每次都挂着一条假警告，
    看的人很快就学会整块跳过。
    """
    items = _cases(11, priority="P2") + _cases(2, priority="P0")
    out = check_coverage(items)
    assert out["p0"] == {"count": 2, "ratio": 0.154, "cap": P0_QUOTA, "quota": 2}
    assert not [n for n in out["notes"] if "P0" in n], out["notes"]

    # 第 3 条 P0 才越线
    out3 = check_coverage(_cases(10, priority="P2") + _cases(3, priority="P0"))
    assert [n for n in out3["notes"] if "P0" in n]


def test_登录这种模块不该被要求测删除():
    """**实测抓到的误报**：「登录」13 条被报「一条删除都没有」。

    登录模块本来就没有"删除"这回事。无条件点名缺删除/缺权限，跟体检自己
    禁止 LLM 说的"建议补充异常场景"是同一个毛病 —— 放到哪个模块都成立，
    说了等于没说。判据改成"有前半截、缺后半截"：建了不删才是缺口。
    """
    items = [
        {"title": "输入正确账号密码登录成功跳转首页", "priority": "P0", "expected_result": ""},
        {"title": "密码错误提示账号或密码不正确", "priority": "P1", "expected_result": ""},
        {"title": "连续失败五次锁定账号", "priority": "P1", "expected_result": ""},
        {"title": "验证码为空时无法提交", "priority": "P2", "expected_result": ""},
        {"title": "验证码错误提示重新输入", "priority": "P2", "expected_result": ""},
        {"title": "会话超时后跳回登录页", "priority": "P2", "expected_result": ""},
        {"title": "记住我勾选后下次免密", "priority": "P2", "expected_result": ""},
        {"title": "退出登录后回退不能进首页", "priority": "P1", "expected_result": ""},
    ]
    out = check_coverage(items)
    assert out["ops"]["创建"] == 0
    assert not [n for n in out["notes"] if "删除" in n], out["notes"]


def test_建了不删才算缺口():
    """反过来这条必须报：模块里明明在建对象，一条删除都没有。"""
    items = _cases(6, title="新建服务") + [
        {"title": f"查询服务列表第{i}页", "priority": "P2", "expected_result": ""}
        for i in range(4)
    ]
    out = check_coverage(items)
    assert [n for n in out["notes"] if "删除" in n], out["notes"]


def test_全压在创建上会被点出来():
    """暗面的淹法：每条单看都合理，20 条都在测创建的参数组合。"""
    out = check_coverage(_cases(12, title="新建服务参数校验"))
    joined = " ".join(out["notes"])
    assert "覆盖倾斜" in joined
    assert out["ops"]["创建"] == 12
    # 缺删除、缺权限要**单独点名**，不能只说一句"倾斜了" ——
    # 「删除 0」是可执行的，「倾斜了」不是
    assert "「删除」" in joined and "「权限」" in joined


def test_分布均匀就不出声():
    items = [
        {"title": "新建服务成功后出现在列表", "priority": "P1", "expected_result": ""},
        {"title": "服务详情页展示配置项", "priority": "P2", "expected_result": ""},
        {"title": "编辑服务名称后列表同步更新", "priority": "P2", "expected_result": ""},
        {"title": "删除服务后调用返回 404", "priority": "P1", "expected_result": ""},
        {"title": "创建服务时名称为空应报错", "priority": "P2", "expected_result": ""},
        {"title": "租户越权访问他人服务返回 403", "priority": "P0", "expected_result": ""},
        {"title": "查询服务列表分页正确", "priority": "P2", "expected_result": ""},
        {"title": "修改服务超时配置后生效", "priority": "P2", "expected_result": ""},
    ]
    out = check_coverage(items)
    assert out["notes"] == [], out["notes"]
    assert out["ops"]["删除"] >= 1 and out["ops"]["权限"] >= 1


def test_它不再返回硬拒():
    """原来 P0 超配额是**硬拒**（"整批打回重新分级"）。

    到模块评审那一刻用例早就入库了，"打回"无处可打；而体检的定位是情报 ——
    覆盖缺口都不参与一条用例过不过，P0 分布更没理由比它硬。
    拦不住的东西写成硬拒，只会让人学会忽略它。
    """
    out = check_coverage(_cases(20, priority="P0"))
    assert isinstance(out, dict)
    assert "errors" not in out and "blocking" not in out


def test_模块体检把它算进报告():
    """接线本身也要钉：判据在不在不等于体检返回它。"""
    import inspect

    from app.services.review import checkup

    src = inspect.getsource(checkup.run)
    assert "check_coverage" in src
    assert '"coverageSkew"' in src


def test_审核报告存的时候不许把它丢掉():
    """报告是**审完算一次存下来**的（每次打开重算的话 LLM 措辞每轮不同）。

    存的时候是按白名单挑 key 的 —— 漏一个 key，页面上那一块就永远空着，
    而且看起来像"这个模块没有分布问题"，不像"这块没存"。
    """
    import inspect

    from app.services.review import queue

    assert "coverageSkew" in inspect.getsource(queue._build_report)


def test_老名字没人用了():
    """`check_batch` 改名成 `check_coverage`（返回值从 (errors, warns) 变成 dict）。

    留个残留名字最容易出的事：有人照着旧签名 `e, w = check_batch(...)` 调，
    拿到 dict 也能解包（解出两个 key 名），静默错。
    """
    from app.services import intake_gate

    assert not hasattr(intake_gate, "check_batch")
