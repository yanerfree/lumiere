"""改名 testBench → Lumiere：把库里存着的工具名/skill 名跟着改

Revision ID: zzv0lumren
Revises: zzu0qarev

代码里的 `tb_*` 工具名和 `tb-*` 预置 skill 目录同一批改成了 `lum_*` / `lum-*`。
库里有四处存着这些名字，**sed 够不着**，漏了的表现都不像改名引起的：

1. `projects.mcp_allowed_tools`（2 行 / 107 个名字）—— Key 的工具范围。
   漏了 = 范围里全是不存在的工具，客户端 `tools/list` 直接空，
   报出来像「工具没注册」。
2. `ai_capability_bindings`（1 行）—— key `cap-tb-quality-review`
   和 `module_keys=['tb-quality-review']`。漏了 = 「AI 能力→模型」页
   多一个绑不上模型的空档位（CLAUDE.md 点名过这个症状）。
3. `knowledge_entries`（3 行）—— 写给未来 CC 看的指引，正文点名了工具。
   漏了 = 把它指向不存在的工具，而它本来就是「照着做」的东西。
4. `projects.description` 一行 —— 项目列表卡片上直接看得见的旧名。

**刻意不动**（另有理由，见 docs/rename-to-lumiere.md §2 C）：
- `mcp_api_keys.key_prefix` 那 31 行 `tb_xxxxx` —— 已经发出去的 Key 字面量，
  改 = 吊销别人的 Key。新发的 Key 从这一版起是 `lum_` 前缀，两种前缀共存是对的。
- `audit_logs.trace_id` / `ai_usage_logs.skill_name` / `case_review_rounds.findings`
  / `cases.reflections` —— 它们记的是「当时调了 tb_update_case」，改了是篡改历史。
- 被测系统的数据：`tb-fwgl` / `tb-zcgl-`（UAG 模块域码）、`tb-shared-*`（mock 上游
  主机名）、`tb-dyapp` / `tb-lead` / `tb-probe-model`、`script_runs.stdout` 那 253 行。
  **所以这里全部是带 where 的定点替换，没有一句全表 replace()** ——
  全表跑会把用户数据改坏，而且不报错、不留痕。
"""
from alembic import op

revision = "zzv0lumren"
down_revision = "zzu0qarev"
branch_labels = None
depends_on = None

# 平台侧预置 skill：目录名同一批从 tb-* 改成了 lum-*
PRESET_SKILLS = [
    "api-case-generate", "case-generate", "diagnose", "doc-generate",
    "quality-review", "scenario-expand", "scenario-extract",
    "scenario-model", "scenario-self-review",
]
_ALT = "|".join(PRESET_SKILLS)


def _swap(old: str, new: str) -> None:
    """old→new 单向替换。upgrade 传 ('tb','lum')，downgrade 传反的。

    `cut` = 前缀连分隔符的长度 + 1。upgrade 时 old='tb' → 'tb_' 3 个字符、从第 4 位截；
    downgrade 时 old='lum' → 'lum_' 4 个字符、从第 5 位。写死 4 的话 downgrade
    会截出 '_list_cases' 这种，而且不报错。
    """
    cut = len(old) + 2
    # 1) Key 的工具范围：只动 array 行（有 Key 的范围是 JSON 标量 null，展开会报
    #    "cannot extract elements from a scalar"），且只动以 old_ 打头的元素
    op.execute(f"""
        update projects set mcp_allowed_tools = (
            select jsonb_agg(
                case when t like '{old}\\_%' then '{new}_' || substring(t from {cut})
                     else t end
            )
            from jsonb_array_elements_text(projects.mcp_allowed_tools) t
        )
        where jsonb_typeof(mcp_allowed_tools) = 'array'
          and mcp_allowed_tools::text like '%{old}\\_%'
    """)

    # 2) AI 能力档位：key 是 cap-{{module_key}}，两边一起改
    op.execute(f"""
        update ai_capability_bindings
           set key = replace(key, 'cap-{old}-', 'cap-{new}-'),
               module_keys = (
                   select jsonb_agg(
                       case when m ~ '^{old}-({_ALT})$'
                            then '{new}-' || substring(m from {cut}) else m end)
                   from jsonb_array_elements_text(module_keys) m
               )
         where key like 'cap-{old}-%'
           and jsonb_typeof(module_keys) = 'array'
    """)

    # 3) 给未来 CC 看的指引：只换工具名（\\m 是词首），以及那 9 个预置 skill 名
    op.execute(f"""
        update knowledge_entries
           set title   = regexp_replace(title,   '\\m{old}_', '{new}_', 'g'),
               content = regexp_replace(content, '\\m{old}_', '{new}_', 'g')
         where title ~ '\\m{old}_' or content ~ '\\m{old}_'
    """)
    op.execute(f"""
        update knowledge_entries
           set title   = regexp_replace(title,   '\\m{old}-({_ALT})\\M', '{new}-\\1', 'g'),
               content = regexp_replace(content, '\\m{old}-({_ALT})\\M', '{new}-\\1', 'g')
         where title ~ '\\m{old}-({_ALT})\\M' or content ~ '\\m{old}-({_ALT})\\M'
    """)


def upgrade() -> None:
    _swap("tb", "lum")
    # 项目列表卡片上看得见的那条旧名（项目名 tb-self-shared-project 是标识，不动）
    op.execute("""
        update projects set description = replace(description, 'testBench', 'Lumiere')
         where description like '%testBench%'
    """)
    # 新发 Key 的前缀这一版起是 lum_（代码侧改的）；存量 tb_ 前缀故意保留


def downgrade() -> None:
    _swap("lum", "tb")
    op.execute("""
        update projects set description = replace(description, 'Lumiere', 'testBench')
         where description like '%Lumiere%'
    """)
