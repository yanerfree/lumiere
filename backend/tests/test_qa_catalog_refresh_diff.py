"""「拉取最新」提示语背后的增/改/移除条数。

页面上那句提示是用户唯一能看到的反馈，所以这里卡三件事：
① 数字算得对；② 第一次读（没有上一份快照）**不报 0**，而是不报；
③ diff 不许写回缓存 —— 否则之后每次 GET 都带着上一次拉取的数字。
"""
from app.services import qa_catalog
from app.services.qa_catalog import _diff_snapshots, _sig_by_id


def _snap(scenarios, sha="aaaaaaa"):
    return {"repo": {"commitSha": sha}, "scenarios": scenarios}


def _sc(sid, title="标题", state="", scripts=()):
    return {
        "id": sid, "title": title, "priority": "P0", "risk": "high",
        "tier": "L2", "state": state,
        "scripts": [{"path": p} for p in scripts],
    }


def test_没有上一份快照时不报数():
    # 进程刚起来缓存是空的。这时候报「新增 0 条更新 0 条」会被当成"仓库没动"，
    # 而事实是"没得比" —— 这两件事必须说得不一样，所以这里必须是 None。
    assert _diff_snapshots(None, _snap([_sc("S-1")])) is None


def test_新增和移除按场景号算():
    prev = ("old", _snap([_sc("S-1"), _sc("S-2")]))
    cur = _snap([_sc("S-2"), _sc("S-3"), _sc("S-4")], sha="bbbbbbb")
    d = _diff_snapshots(prev, cur)
    assert (d["added"], d["removed"], d["updated"]) == (2, 1, 0)
    assert d["commitChanged"] is True


def test_标题或状态变了算更新():
    prev = ("old", _snap([_sc("S-1", title="旧标题"), _sc("S-2", state="")]))
    cur = _snap([_sc("S-1", title="新标题"), _sc("S-2", state="@known-bug")], sha="old")
    d = _diff_snapshots(prev, cur)
    assert (d["added"], d["removed"], d["updated"]) == (0, 0, 2)
    # commit 没变也可能有变化（脚本范围改了会重新解析），所以这两个信号是独立的
    assert d["commitChanged"] is False


def test_覆盖脚本变了算更新_顺序不算():
    prev = ("old", _snap([_sc("S-1", scripts=("a.sh", "b.sh"))]))
    same = _snap([_sc("S-1", scripts=("b.sh", "a.sh"))])
    assert _diff_snapshots(prev, same)["updated"] == 0
    more = _snap([_sc("S-1", scripts=("a.sh", "b.sh", "c.sh"))])
    assert _diff_snapshots(prev, more)["updated"] == 1


def test_内容一样就一条不报():
    prev = ("old", _snap([_sc("S-1"), _sc("S-2")]))
    d = _diff_snapshots(prev, _snap([_sc("S-1"), _sc("S-2")], sha="old"))
    assert (d["added"], d["removed"], d["updated"]) == (0, 0, 0)


def test_指纹不含时间戳():
    # updatedAt 这类字段跟着 commit 走，放进指纹会让每次 fetch 都报"全部更新"
    a = _sig_by_id(_snap([_sc("S-1")]))
    with_time = _sc("S-1")
    with_time.update({"updatedAt": "2026-09-04T00:00:00", "rowUpdatedAt": "2026-09-04"})
    assert a == _sig_by_id(_snap([with_time]))


def test_diff不落缓存(monkeypatch, tmp_path):
    """cached_read 返回的 diff 是浅拷贝上挂的，_CACHE 里那份必须干净。"""
    cfg = {"url": "git@x:y.git", "branch": "main", "catalogPath": "c.md", "caseGlobs": ["*.sh"]}
    calls = {"n": 0}

    def fake_sync(project_id, cfg_, do_fetch):
        calls["n"] += 1
        n = calls["n"]
        return _snap([_sc(f"S-{i}") for i in range(n)], sha=f"sha{n}")

    monkeypatch.setattr(qa_catalog, "sync_and_read", fake_sync)
    monkeypatch.setattr(qa_catalog, "_repo_dir", lambda pid: tmp_path / "nope")
    qa_catalog._CACHE.clear()

    first = qa_catalog.cached_read("p1", cfg, refresh=True)
    assert "refreshDiff" not in first          # 第一次没得比

    second = qa_catalog.cached_read("p1", cfg, refresh=True)
    assert second["refreshDiff"]["added"] == 1
    _sha, cached = list(qa_catalog._CACHE.values())[0]
    assert "refreshDiff" not in cached         # 缓存里那份不许带数字
    qa_catalog._CACHE.clear()
