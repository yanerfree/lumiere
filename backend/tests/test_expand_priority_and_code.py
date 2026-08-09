"""场景生成展开这一步的两条纪律，封样钉住。

都是真跑一次生成链摸出来的：
1. 优先级必须沿用人在「确认场景模型」那一步确认过的值 —— 实测模型里 5 个 P0，
   展开完 P0 归零全成 P1，人的确认被悄悄改掉。
2. 取号必须走 _next_case_code（MAX+1），不能用 count(*) —— 删过任何一条用例，
   count 就小于真实 MAX，下一条直接撞 uq_case_branch_code。
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "app/services/scenario_gen/expander.py"
TEXT = SRC.read_text()


def test_优先级以人确认过的为准():
    """展开后必须用 point_snapshot 的 priority 覆盖模型输出。"""
    assert 'confirmed_priority = point_snapshot.get("priority")' in TEXT
    assert 'case_dict["priority"] = confirmed_priority' in TEXT
    # 覆盖必须发生在建 Case 之前
    assert TEXT.index('case_dict["priority"] = confirmed_priority') < TEXT.index("case = Case(")


def test_prompt_不再让模型自己判优先级():
    """prompt 里不能再有"根据业务重要性真实区分"这类让它重判的话。"""
    assert "不要全部标 P1" not in TEXT
    assert "原样沿用输入里给定的优先级" in TEXT


def test_取号走统一实现而不是count():
    """count(*) 当最大序号用会撞唯一约束。"""
    assert "_next_case_code(session, task.branch_id, module)" in TEXT
    # 不允许再出现 count(Case.id) 拼编号
    assert not re.search(r"func\.count\(Case\.id\)[\s\S]{0,400}?case_code\s*=\s*f\"TC-", TEXT)


def test_不再自己拼TC前缀():
    """中文模块名 upper() 拼出来的前缀和 _next_case_code 的拼音规则对不上。"""
    assert 'f"TC-{module.upper()}-{' not in TEXT
