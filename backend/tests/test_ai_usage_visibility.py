"""「哪些 AI 入口真被用过」必须能从页面上看出来。

用户看着旧版的 AI 配置页得出的结论是：
「目前系统中使用到 AI 的好像只有 AI 审核吧，其他的目前都没用到」——
而库里 `scenario-*` 有 111 条调用记录（8-09 那几天在跑场景生成）。

页面只回答"配了什么"、回答不了"用了什么"，人就只能猜；
**照着猜的结论会去砍功能**，所以这件事不是"缺个统计"，是会造成误删。

记账原来只有三处（lum-case-generate / lum-quality-review / scenario-*）。
剩下四条链路（文档生成、带截图生成、文档优化、探索 Charter、正则生成、接口场景编排）
一次都没记过 —— 它们不是"没被用"，是"没被数"。这两件事在界面上长得一模一样，
所以 `METERED_SINCE` 必须把"从什么时候开始记"标出来。
"""
import inspect
from pathlib import Path

from app.services import ai_capabilities
from app.services.ai import usage


def test_有统一的记账入口():
    assert hasattr(usage, "log_ai_call")
    src = inspect.getsource(usage.log_ai_call)
    assert "except Exception" in src, "记账失败不能弄挂业务"


def test_全局调用也能记账():
    """工具箱的正则生成不属于任何项目（resolve_ai_config(None, ...)）。
    project_id 不放开就只能"编一个项目 ID"或者"不记" —— 后者会让页面说
    "正则生成从没被调用过"，而它一直是通的。"""
    from app.models.case_file import AIUsageLog
    assert AIUsageLog.__table__.c.project_id.nullable, "全局 AI 调用记不进去"
    mig = (Path(__file__).resolve().parents[1]
           / "alembic/versions/zzr0aiusage_ai_usage_global.py")
    assert mig.exists(), "改了模型没写迁移，新库和老库会不一样"


def test_原来没记账的链路都补上了():
    """一条一条对：谁在调 AI，谁就得留下调用记录。"""
    root = Path(__file__).resolve().parents[1] / "app"
    checks = {
        # api/documents.py 2026-08-27 随「文档管理」整个删掉，三条 doc-* 能力标 deprecated。
        "api/exploratory.py": ["exploratory-charter"],
        "api/toolbox.py": ["toolbox-regex"],
        "services/ai/api_scenario_gen_service.py": ["api-test-generate"],
    }
    for rel, caps in checks.items():
        src = (root / rel).read_text(encoding="utf-8")
        assert "log_ai_call" in src, f"{rel} 调了 AI 但不记账"
        for cap in caps:
            assert f'capability="{cap}"' in src, f"{rel} 少记 {cap}"


def test_场景生成的四个阶段归到一个入口():
    """场景生成是 extract/model/expand/health-check 各记一条，
    不归并的话页面会冒出四个不在能力清单里的名字，而清单里那一项显示"从没被调用"。"""
    assert ai_capabilities.normalize_usage_key("scenario-expand") == "scenario-gen"
    assert ai_capabilities.normalize_usage_key("scenario-health-check") == "scenario-gen"
    # 不认识的名字原样返回，别静默吞掉
    assert ai_capabilities.normalize_usage_key("who-knows") == "who-knows"


def test_没被数和没被用要分开说():
    assert ai_capabilities.METERED_SINCE, "没有『从什么时候开始记账』这份信息"
    from app.api import ai_capabilities as api_caps
    src = inspect.getsource(api_caps.get_capability_usage)
    assert "meteredSince" in src, "接口没把这件事给前端"
    assert "orphans" in src, "记了账但对不上能力 key 的调用要露出来，否则两本账永远差着数"

    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/AICapabilities.jsx").read_text(encoding="utf-8")
    assert "从未调用" in jsx and "暂无记录" in jsx, \
        "页面把『0 次』和『没记账』写成同一句话了 —— 这会让人以为功能没用过"


def test_模型和连接名对不上时页面要说破():
    """连接名是人随手起的（「公司网关-Opus」），它自己的默认模型又可能是另一个。
    首屏出现过「claude-sonnet-5 经 公司网关-Opus」，用户当场问"自相矛盾"。

    第一版是在 AICapabilityBindings 首屏解释三层关系，用户的回应是
    「我希望我配什么就是什么」—— 于是那版首屏整个删掉了（2026-08-24 页面重排：
    去掉大横幅，能力→模型改成一张表）。名字对不上的提示挪到了它真正该在的地方：
    连接表格本身，跟连接名字长在一起，而不是在下面某处替它解释。"""
    from app.api import ai_capabilities as api_caps
    assert '"model": sysdef.model' in inspect.getsource(api_caps.get_overview), \
        "接口没给连接自带的模型，页面就没法说破名字对不上这件事"

    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/AIProviderConfig.jsx").read_text(encoding="utf-8")
    assert "mismatch" in jsx and "WarningOutlined" in jsx, "连接表没有『名字对不上』这一支"
    assert "测试连接用的模型" in jsx, "连接层的模型字段没有正名，看着还是像能决定实际调用的地方"


def test_入口已经不在的能力要标成下线():
    """页面上写着「用例管理 → 从接口生成」，而那个按钮 2026-08-19 就摘了；
    场景生成连路由和菜单都没有（MCP 工具也摘了）。**照着这一列去找是找不到的**，
    而用户会因此认为"平台在骗我"。入口不在了就标 deprecated，挪到「已下线」那一段
    并写清为什么 —— 不是从清单里删掉（删了 category_of 会静默降档）。
    """
    from app.services.ai_capabilities import CAPABILITY_REGISTRY
    gone = {c["key"]: c for c in CAPABILITY_REGISTRY if c.get("deprecated")}
    for key in ("lum-case-generate", "scenario-gen"):
        assert key in gone, f"{key} 的入口已经不在了，清单里还写着在用"
        assert gone[key].get("deprecatedNote"), f"{key} 没写为什么下线 —— 过阵子又会被加回来"


def test_活着的入口都写得出点得到的路径():
    """「文档管理」这种半截路径没用 —— 人要的是"点哪个按钮"。"""
    from app.services.ai_capabilities import CAPABILITY_REGISTRY
    for c in CAPABILITY_REGISTRY:
        if c.get("deprecated"):
            continue
        where = c.get("where") or ""
        assert where and where != "已下线", f"{c['key']} 没写入口"
        assert "→" in where or "「" in where, f"{c['key']} 的入口写得太糊：{where}"


def test_每个入口都能单独配模型():
    """原来只能按档位配（文本 / UI 脚本两档）。想让文档生成用便宜的、评审用强的，
    得去「新增自定义档位」建档再勾模块 —— 三步操作、两个新概念，而人要的只是
    "这一行换个模型"。用户的原话是「我要看到在用什么 AI，都可以配置」。"""
    from app.api import ai_capabilities as api_caps
    assert hasattr(api_caps, "set_capability_model")
    src = inspect.getsource(api_caps.set_capability_model)
    # 仍然落在自定义档位上，不新造第二套优先级 —— 否则页面显示和实际调用早晚漂开
    assert "AICapabilityBinding" in src and "module_keys=[body.key]" in src
    assert "session.delete(own)" in src, "取消不了单独指定就成了单行道"

    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/AICapabilities.jsx").read_text(encoding="utf-8")
    assert "capability-model" in jsx and "跟着档位" in jsx


def test_用量按key解析而不是按档位缓存():
    """单个入口有专用档时，按 category 缓存会把它显示成档位的模型 —— 页面又开始骗人。"""
    from app.api import ai_capabilities as api_caps
    src = inspect.getsource(api_caps.get_capability_usage)
    assert "ownModel" in src, "页面分不出这一行是跟着档位还是单独指定的"


def test_模型只有一个地方能配_另一层要正名():
    """用户看着首屏那段解释问「这是什么意思，我希望我配什么就是什么」。

    根因不是文案，是**两层都长得像模型配置**：
    连接上有「默认模型」，档位上也有「模型」，而解析顺序是
    入口专用档 → 内置档位 → 连接（`_resolve_model`）。内置档位永远有值，
    所以连接那一层在全局兜底路径上**永远用不上** —— 人在那儿改完什么都没发生。

    修法不是解释得更清楚，是让"能配的地方"只剩一个：
    连接那层正名成「测试连接用的模型」，并给一个开关把它一键同步到档位。
    """
    from pathlib import Path
    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/AIProviderConfig.jsx").read_text(encoding="utf-8")
    assert "测试连接用的模型" in jsx, "连接那层还叫「模型名称」，看着就是能配模型的地方"
    assert "测试用模型" in jsx, "列表那一列还叫「默认模型」"
    assert "同时把" in jsx and "ai-capabilities/bindings" in jsx, \
        "没给「让它真的生效」的路 —— 那就还是改了不生效"
    assert "effectiveTextModel" in jsx, "表单里没显示真正在跑的是哪个模型"

    # 2026-08-24 页面重排：AICapabilityBindings 的大横幅整个删掉了 ——
    # "只有一个地方能配"现在不是靠一句指路文案做到的，是靠**能力→模型只剩一张表**
    # 做到的：档位默认模型是表格上方唯一的输入，每一行的模型就是表格本身的一个格子，
    # 不需要另一段文字去告诉人"要不要相信这个数字"。
    body = (Path(__file__).resolve().parents[2]
            / "frontend/src/pages/settings/AICapabilityBindings.jsx").read_text(encoding="utf-8")
    assert "平台当前在用" not in body, "大横幅还在 —— 应该已经被表格取代了"
    assert "档位优先，真正在跑的是后者" not in body, \
        "不该再靠一段解释两层优先级的文案，应该让能力表本身说清楚"
    assert "跟随" in body, "表格里每一行要能看出'跟随档位'还是'单独指定'，不能只靠外部文案"


def test_AI审核标签和按钮同名():
    """用户点的按钮叫「AI 审核」，registry 里原来写的是「用例质量评审（单条·六维）」——
    两个名字对不上，人在这一页里找不到自己要改的那一项（原话：
    「可是我要改的是 AI 审核啊，没看到这个在哪改」）。标签必须包含按钮上的原词。
    """
    from app.services.ai_capabilities import CAPABILITY_REGISTRY
    cap = next(c for c in CAPABILITY_REGISTRY if c["key"] == "lum-quality-review")
    assert "AI 审核" in cap["label"], "标签没有用户在页面上实际点的那个词"


def test_单入口覆盖不是自定义档位():
    """PUT /capability-model 建的是"这一行换个模型"，不是用户主动建的档位。
    混进 customBindingCount 会让人去找一张并不存在的档位卡片；
    混进"这一个模型负责全部 N 项"那句话会让它变成假话。
    """
    from app.api import ai_capabilities as api_caps
    src = inspect.getsource(api_caps.get_overview)
    assert 'not (b.key or "").startswith("cap-")' in src, \
        "customBindingCount 没有把单入口覆盖过滤掉"
    assert "perCapabilityOverrides" in src, "总览没有把单入口覆盖单独列出来"


def test_单入口覆盖不会在档位卡片里另开一张卡():
    """给「AI 审核」单独指定模型后，它必须**还是能力表里原来那一行**、
    那一行的模型下拉直接显示新模型 —— 不能消失，也不能在表格外面多出一张叫
    「AI 审核（用例质量评审·六维）·专用」的独立卡片/条目（那会让人以为多了一个档位）。

    2026-08-24 页面重排后，"档位卡片网格"整个换成了一张表（每行一个能力，来自
    `registry`，不是来自 `bindings`），所以单入口覆盖天然不可能在表里多开一行——
    这里改成断言它同样不会混进表格下方的「自定义档位」收纳条。
    """
    jsx = (__import__("pathlib").Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/AICapabilityBindings.jsx").read_text(encoding="utf-8")
    assert "startsWith('cap-')" in jsx
    assert "!String(b.key || '').startsWith('cap-')" in jsx,         "单入口覆盖没有从『自定义档位』收纳条里排除"
    assert "ownModelOf" in jsx and "setCapabilityModel" in jsx, "表格里没有每行的模型下拉"
    assert "dataSource={registry}" in jsx,         "表格的行来源不是 registry —— 单入口覆盖有可能混进表格本身多出一行"


def test_有覆盖时项目总览不说假话():
    """「这一个模型负责全部 N 项」在有单入口覆盖时不成立。
    2026-08-24 之前这句汇总印在 AICapabilityBindings 的首屏横幅里；那块横幅已经
    删掉了（能力表本身逐行显示"跟随档位"还是"单独指定"，不需要再有一句汇总去
    复述）。汇总句现在只留在「项目 → AI 使用总览」的一行提示里，这里断言它没有
    因为重排而被删掉、变回一句不真实的话。
    """
    jsx = (__import__("pathlib").Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/AIProjectOverview.jsx").read_text(encoding="utf-8")
    assert "overrides.length" in jsx and "单独指定" in jsx


# ── 2026-08-24：AI 配置类页面整理（去噪音 + 消除跨页矛盾）──────────

def test_全局页不再堆一张写死的说明卡片():
    """AI 服务配置页原来顶部有一张卡片："AI 服务为以下 N 处功能提供支持…
    配置步骤：①②③④"。四步流程是常识，不产生决策价值，是"看一眼看不出是什么
    东西"的噪音来源之一。删掉后连接表上方只留一行小字说清"这里配的是连接，
    不是模型"。
    """
    from pathlib import Path
    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/AIProviderConfig.jsx").read_text(encoding="utf-8")
    assert "配置步骤" not in jsx, "写死的四步说明卡片还在"
    assert "capLabels" not in jsx, "已经删掉的手写能力清单状态还留着死代码"


def test_能力模型改成一张表而不是嵌套卡片():
    """原来是"每个档位一张卡片，卡片里再嵌一行行能力+下拉"——跟项目页
    「能力总览」的表格是两套完全不同的视觉语言看同一份数据。改成同一种形状：
    一张表，一行一个能力，模型是表格本身的一个可编辑格子。
    """
    from pathlib import Path
    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/AICapabilityBindings.jsx").read_text(encoding="utf-8")
    assert "<Table" in jsx, "能力→模型没有改成表格"
    assert "gridTemplateColumns: 'repeat(auto-fill" not in jsx, "档位卡片网格还在"
    assert "已下线的入口" in jsx and "Collapse" in jsx, \
        "已下线的入口应该收进折叠区，不占主视图空间"


def test_项目内AI配置不再手写功能清单():
    """`ProjectAIConfig.jsx` 原来写死："AI 用例生成 ✅、AI 脚本生成 ✅、
    质量评审 ⏳即将上线、失败诊断 ⏳即将上线"——事实完全反过来：前两个入口早下线了，
    质量评审（AI 审核）是全平台唯一被真实高频调用的能力。这段话从后端注册表拉，
    不再手写，跟全局页用同一句收尾（"AI 能力 → 能力总览"），两页不会各说各话。
    """
    from pathlib import Path
    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/ProjectAIConfig.jsx").read_text(encoding="utf-8")
    # 检查渲染出来的那句话,不是解释性注释里提到的历史写法(注释里为了讲清楚
    # "以前长什么样"必然会出现"即将上线"这几个字,直接裸查字符串会把注释也算进去)
    rendered = "\n".join(l for l in jsx.splitlines() if not l.strip().startswith("//"))
    assert "即将上线" not in rendered, "渲染层还在手写『即将上线』——质量评审早就是最常用的能力了"
    assert "⏳" not in rendered, "渲染层还在用旧的沙漏图标"
    assert "api.get('/ai-capabilities')" in jsx, \
        "功能清单没有从后端注册表拉，还是有可能手写走样"
    assert "AI 能力 → 能力总览" in jsx, "没有指向能力总览页——跟全局页说法不一致"


def test_SkillManage状态跟注册表对齐():
    """SkillManage.jsx 有一份更早、更详细的手写 SKILLS 数组，同样的问题更严重：
    质量评审标"规划中 Phase 2"（其实是全平台唯一高频调用的能力），
    AI 用例/脚本生成标"可用"（其实入口早下线了，脚本生成那条连 skill 文件都没有，
    编辑按钮点了会 404）。这四条状态改错了会让用户在"项目设置查能力"→
    "Skill 管理查细节"这条路径上看到自相矛盾的两套说法。
    """
    from pathlib import Path
    jsx = (Path(__file__).resolve().parents[2]
           / "frontend/src/pages/settings/SkillManage.jsx").read_text(encoding="utf-8")
    assert "Tooltip" in jsx.split("from 'antd'")[0], \
        "用了 Tooltip 组件却没在 antd 导入里加上（会整页白屏 pageerror）"

    # 逐条状态必须对：lum-case-generate/lum-script-generate 已下线，
    # lum-quality-review 可用，lum-explore 是"上线但无独立 skill 文件"的第四态
    import re
    def _status_of(name):
        m = re.search(r"name:\s*'%s'.*?status:\s*'(\w+)'" % re.escape(name), jsx, re.DOTALL)
        return m.group(1) if m else None

    assert _status_of("lum-case-generate") == "retired", \
        "AI 用例生成入口已下线，不该显示『可用』"
    assert _status_of("lum-script-generate") == "retired", \
        "AI 脚本生成没有 skill 文件，标『可用』会露出一个点了 404 的编辑按钮"
    assert _status_of("lum-quality-review") == "available", \
        "质量评审（AI 审核）是全平台唯一高频调用的能力，不该标『规划中』"
    assert _status_of("lum-doc-generate") == "retired", \
        "「文档管理」2026-08-27 整个下线（含 SKILL.md 文件本身），标『可用』" \
        "会露出一个点了就 404 的编辑按钮"
    assert _status_of("lum-explore") == "inline", \
        "探索测试的章程生成已经上线（内联 prompt），标『规划中』是说反了；" \
        "但也不能标『可用』——那会露出一个查无 skill 文件、点了 404 的编辑按钮"

    # 第四态的渲染分支必须真的存在，不能只改数据不改渲染（那样会退回默认分支
    # 显示成"undefined 规划中"）
    assert "status === 'inline'" in jsx, "没有渲染 inline 状态的分支"
    assert "已上线（无独立 Skill 文件）" in jsx
