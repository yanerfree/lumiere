"""模块名门禁 —— 「这个模块命名要规范一下，还有这明显是二级他却写在一级上」。

真实现场（UAG 项目导航栏）：
    LLM PROVIDERS (1)
    监控-请求日志 (1)      ← 明显是两级，被建成了一级
第二个的问题不只是难看：`监控` 下之后的用例找不到家，只能再开一个一级目录，
导航栏很快变成一屏长名字的平铺列表 —— 而模块本来是用来收拢用例的。

判得死的硬拒，判不死的只提示：
· 名字里有层级分隔符（-/_:：·|→）→ 硬拒，直接把该传的两个参数给它
· 同一个模块换写法（大小写/分隔符不同）→ 硬拒，把现成的名字给它
· 新开一级模块 → 只提示（该不该挂在某个已有模块下，平台判不了）
"""
from __future__ import annotations

import inspect

from app.services.intake_gate import check_module_name


def test_看着是两级的给提示并说清该传什么():
    """**不硬拦**（判据规范 ①③）：拼成一级顶多是导航难看，不影响任何正确性，
    而合法写法不少 —— 见 test_本来就是一个词的不该被拆。"""
    errors, warns = check_module_name("监控-请求日志", ["订阅管理"])
    assert errors == [], "命名风格不配硬拦"
    assert warns and 'module="监控"' in warns[0] and 'submodule="请求日志"' in warns[0]
    assert "忽略这条" in warns[0], "没有忽略出口的提示等于逼人照做"


def test_本来就是一个词的不该被拆():
    """反例：这些名字里有分隔符，但它们是一个词。硬拦会让人没法起这些名字。"""
    for name in ("A/B 测试", "CI/CD", "OAuth2.0-登录", "读写分离-主从"):
        assert check_module_name(name, [])[0] == [], f"{name} 被硬拦了"


def test_各种分隔符都提示():
    for name in ("监控/请求日志", "监控_请求日志", "监控：请求日志", "监控·请求日志",
                 "监控|请求日志", "监控 - 请求日志", "监控→请求日志"):
        assert check_module_name(name, [])[1], f"{name} 连提示都没有"


def test_名字里的空格不算分隔符():
    """「LLM PROVIDERS」「用户 管理」是一个名字，不是两级 —— 按空格拆是滥报。"""
    assert check_module_name("LLM PROVIDERS", [])[0] == []


def test_单词里的连字符不误伤():
    """左右两边得都有内容才算两级 —— 「-监控」「监控-」是笔误，不该按两级拆。"""
    assert check_module_name("-监控", [])[1] == []
    assert check_module_name("监控-", [])[1] == []


def test_重名写法比两级判据优先():
    """`llm_providers` 既像两级、又是已有「LLM PROVIDERS」的另一种写法。
    这时候该说的是"用现成那个名字" —— 回一句"拆成 llm + providers"是把人带歪。"""
    errors, _ = check_module_name("llm_providers", ["LLM PROVIDERS"])
    assert "LLM PROVIDERS" in errors[0] and "两级" not in errors[0]


def test_同一个模块换写法硬拒():
    for variant in ("LLM Providers", "llm_providers", "LLM-PROVIDERS", "llmproviders"):
        errors, _ = check_module_name(variant, ["LLM PROVIDERS"])
        assert errors, f"{variant} 该被判成同一个模块"
        assert "LLM PROVIDERS" in errors[0], "要把现成的名字给它，否则它只能猜"


def test_真的不同的模块不误拒():
    assert check_module_name("服务管理", ["订阅管理", "监控"])[0] == []


def test_子模块里带分隔符是允许的():
    """「审批-二级」确实是一个名字。三级目录很少，硬拆反而添乱。"""
    assert check_module_name("审批-二级", [], is_top_level=False)[0] == []


def test_新开一级模块只提示不拦():
    errors, warns = check_module_name("监控", ["订阅管理", "服务管理"])
    assert errors == []
    assert warns and "新建的一级模块" in warns[0]
    assert "订阅管理" in warns[0], "得把现有一级模块列出来，否则它没法判断该不该挂进去"


def test_第一个模块不提示():
    """一个模块都没有的时候提示"确认不该是别人的子模块"是废话。"""
    assert check_module_name("订阅管理", [])[1] == []


def test_门禁接在建用例和改用例两条路上():
    """只接建用例的话，CC 用 tb_update_case 搬目录时照样能建出歪目录。"""
    from app.mcp.tools import test_cases
    for fn in (test_cases.create_case, test_cases.update_case):
        assert "_check_module" in inspect.getsource(fn)


def test_不同父模块下的同名子模块要放行():
    """「订阅管理/审批配置」和「服务管理/审批配置」确实是两回事，不能硬拦。

    这条原来是拿源码文本钉「只跟同级比」的。§11 规则 1 把那个决定推翻了 ——
    只给一级列表正是「同一个东西摆两处」那个事故的根因。改成钉**行为**：
    全树照查，但跨父同名只提示、不拒绝。
    """
    from app.services.intake_gate import check_module_placement
    tree = [{"name": "订阅管理", "parent": None}, {"name": "审批配置", "parent": "订阅管理"},
            {"name": "服务管理", "parent": None}]
    errors, warns = check_module_placement("审批配置", tree, "服务管理")
    assert errors == [], "跨父同名被硬拦了 —— 它们是两个正常的子模块"
    assert warns, "跨父同名至少要说一句，免得下次有人以为它俩是一个"


def test_同一位置已有就硬拒():
    from app.services.intake_gate import check_module_placement
    tree = [{"name": "订阅管理", "parent": None}, {"name": "审批配置", "parent": "订阅管理"}]
    errors, _ = check_module_placement("审批配置", tree, "订阅管理")
    assert errors, "同一处再建一个，等于把用例劈成两半"


def test_顶层和子模块下各一个要硬拒():
    """**事故现场**：网关那边顶层有「跨租户订阅(1)」，「订阅管理」下也有
    「跨租户订阅(7)」—— 同一个东西摆两处，顶层那个还是空的。"""
    from app.services.intake_gate import check_module_placement
    tree = [{"name": "跨租户订阅", "parent": None}, {"name": "订阅管理", "parent": None}]
    errors, _ = check_module_placement("跨租户订阅", tree, "订阅管理")
    assert errors, "顶层已有同名，还允许挂到模块下 —— 这就是那个事故"

    tree2 = [{"name": "订阅管理", "parent": None}, {"name": "跨租户订阅", "parent": "订阅管理"}]
    errors2, _ = check_module_placement("跨租户订阅", tree2, None)
    assert errors2, "模块下已有同名，还允许建到顶层 —— 同一个事故的反方向"


def test_范围词硬拒():
    """「平台自身」不是模块名。放行的代价是洗不掉的 case_code 前缀。"""
    for w in ("平台自身", "其他", "通用", "系统", "未分类"):
        assert check_module_name(w, [])[0], f"{w} 居然放行了"


def test_范围词只整名匹配_不能误伤正经模块():
    """「系统」是范围词，「系统管理」「系统设置」是正经模块 ——
    用 substring 匹配的话这两个会被一起拦掉。"""
    for ok in ("系统管理", "系统设置", "通用权限", "公共服务管理", "平台配置"):
        assert check_module_name(ok, [])[0] == [], f"{ok} 被范围词规则误伤了"


def test_平台自己找得出存量的裂口():
    from app.services.intake_gate import find_split_modules
    tree = [{"name": "跨租户订阅", "parent": None}, {"name": "订阅管理", "parent": None},
            {"name": "跨租户订阅", "parent": "订阅管理"}]
    found = find_split_modules(tree)
    assert found and found[0]["name"] == "跨租户订阅"
    assert "订阅管理" in found[0]["under"]


def test_规范里前置了命名口径():
    """回推被拒之后才知道规则，等于每个新模块都要撞一次墙。"""
    from app.mcp.tools.sync import _SPEC_CASE as spec
    assert "看着是两级就别拼成一级" in spec
    assert "同一个模块只能有一个写法" in spec
    assert "功能域" in spec, "没说清一级模块按什么分，CC 会按「这轮在测什么」开新模块"


# ── 回推建目录时的归一（门禁之外的第二道）────────────────────────────

def test_建目录先strip():
    """真实数据里躺着一条：`平台自身 `（尾空格）和 `平台自身` 是两个一级模块，
    页面上两行长得一模一样，用例分散在两边，谁都看不出为什么。
    门禁能拦住 CC 传的名字，但 Excel 导入、复制这些路径也走这个函数。
    """
    import inspect

    from app.services.import_service import _get_or_create_folder
    src = inspect.getsource(_get_or_create_folder)
    assert "module = module.strip()" in src and "submodule = submodule.strip()" in src


def test_建目录不再把显示名存成大写():
    """name 是显示名、path 是匹配键 —— 存大写会让中英混排的模块名一律 SHOUTING，
    而且跟页面上建的目录（folder_service）不一致。"""
    import inspect

    from app.services.import_service import _get_or_create_folder
    src = inspect.getsource(_get_or_create_folder)
    assert "name=module," in src and "name=submodule," in src
