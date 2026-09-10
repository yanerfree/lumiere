"""改用例落款后必须回显 —— 只写不回，CC 分不出「确认落库了」和「参数被默默吞了」。

外部 CC 反馈（#6/#40/#49 一类）：调 lum_update_case 写了 expected_confirmed_note，
返回里既不进 `changed`、也没有回显块，于是没有任何办法确认「预期已确认」这一步到底
落没落。看着像成功，也可能是参数名写错被静默忽略 —— 两者返回长得一模一样。

修法：① 写了落款就把 "expectedConfirmed" 记进 `changed`；② 结果里回显
expectedConfirmed 块（by/note/at）和 blockedExternal。改了步骤/预期时
case_service 会把落款清成 None，那时正好不回显 —— 对上「标记已失效」的提醒。

用源码盯住接线，回退任意一处，CC 又会回到"确认了个寂寞"的状态。
"""
from __future__ import annotations

import inspect

from app.mcp.tools import test_cases


def _src() -> str:
    return inspect.getsource(test_cases.update_case)


def test_写落款要记进changed():
    """set_target_level / create_case 都记，update_case 也得记，一个口径。"""
    src = _src()
    assert 'changed.append("expectedConfirmed")' in src, "落款没进 changed，CC 看不出改动落库了"


def test_结果回显落款块():
    src = _src()
    assert 'result["expectedConfirmed"]' in src, "落款没回显，没法确认这一步落没落"
    assert "if case.expected_confirmed_at:" in src, "回显没挂在落款时间上"
    # by/note/at 三样都得带出来
    for k in ('"by"', '"note"', '"at"'):
        assert k in src, f"回显块缺 {k}"


def test_落款被清掉时不回显():
    """改步骤/预期会让 case_service 把落款清成 None，这时不该再回显 ——
    回显挂在 `if case.expected_confirmed_at` 上正好实现这点，对上"已失效"提醒。"""
    src = _src()
    # 回显是条件式的（挂在 expected_confirmed_at 上），不是无脑塞
    idx = src.find('result["expectedConfirmed"]')
    guard = src.rfind("if case.expected_confirmed_at:", 0, idx)
    assert guard != -1, "回显没有被落款时间守住 —— 清掉后仍会回显一份过期落款"


def test_回显blockedExternal():
    """「卡在外部条件」也要回显 —— 同样是只写不回就没法确认。"""
    src = _src()
    assert 'result["blockedExternal"] = case.blocked_external' in src


def test_reconfirm不重造落款只重盖时间():
    """措辞润色沿用原依据、只更新时间 —— 逼 CC 重打几百字，重填的也不是新确认。"""
    src = _src()
    assert "reconfirm" in src, "reconfirm 路径没了"
    # 落款文本不动的意图要在注释里留着，别被后人"顺手补全"成重写
    assert "沿用" in src or "不动" in src
