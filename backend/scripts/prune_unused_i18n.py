"""清掉没有任何测试引用的文案词条。

**为什么要清。** 词典一开始是从被测系统 locale 全量导进来的（2416 条），
而脚本真正用到的只有十几处 —— 剩下 2400 条是那份 locale 的**镜像副本**：
一份会过期的重复数据，没有任何测试引用，只让页面翻不完。

词典的定位不是「翻译资产」，是**「测试引用到的文案清单」**。所以判据是：
这条键的文案，在 UI 脚本或接口场景里出现过吗？

「出现过」有两种形态，都算：
  · 脚本里 `t("services.detail.btn.enable")` / 断言里 `${T:...}` —— 直接按键引用
  · 脚本里还写死着中文（`name="启用服务"`）—— 那是**待改成 t() 的**，
    键先留着，不然 CC 一改就发现译文没了

用法：
    python scripts/prune_unused_i18n.py --project <uuid> [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid as _uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.api_test import ApiTestScenario, ApiTestStep  # noqa: E402
from app.models.case import Case  # noqa: E402
from app.models.i18n_message import ProjectI18nMessage  # noqa: E402
from app.models.project import Branch, Project  # noqa: E402,F401
from app.models.script import Script  # noqa: E402
from app.services.i18n_harvest_service import extract_copy_literals  # noqa: E402

# t("key") / t('key') / ${T:key}
_REF_RE = re.compile(r"""\bt\(\s*['"]([^'"]+)['"]|\$\{T:([^}]+)\}""")


async def collect_used(session, pid) -> tuple[set[str], set[str]]:
    """返回 (按键引用的键, 脚本/断言里写死着的中文文案)。

    **中文那一半必须只看「定位/断言的文案位置」，不能在整段正文里搜。**
    用整段搜的后果实测过：2416 条里"留"了 344 条，全是「取消」「管理」「服务」
    「节点」这种两三个字的词 —— 它们在脚本注释、变量名、别的文案里到处都是，
    假命中率极高，等于没清。
    UI 脚本用采集器那套抽取规则（name= / has_text= / get_by_text( / to_contain_text(），
    接口场景只看断言的期望值。
    """
    refs: set[str] = set()
    literals: set[str] = set()

    for (c,) in (await session.execute(
        select(Script.content).join(Case, Script.case_id == Case.id)
        .join(Branch, Case.branch_id == Branch.id)
        .where(Branch.project_id == pid, Script.script_type == "ui")
    )).all():
        if not c:
            continue
        refs |= {(m.group(1) or m.group(2)).strip() for m in _REF_RE.finditer(c)}
        literals |= {i["text"] for i in extract_copy_literals(c)}

    for (asserts,) in (await session.execute(
        select(ApiTestStep.assertions)
        .join(ApiTestScenario, ApiTestScenario.id == ApiTestStep.scenario_id)
        .join(Case, Case.id == ApiTestScenario.source_case_id)
        .join(Branch, Case.branch_id == Branch.id)
        .where(Branch.project_id == pid)
    )).all():
        for a in (asserts or []):
            if not isinstance(a, dict):
                continue
            for v in (a.get("expected"), a.get("value")):
                if not isinstance(v, str):
                    continue
                refs |= {(m.group(1) or m.group(2)).strip() for m in _REF_RE.finditer(v)}
                if re.search(r"[\u4e00-\u9fff]", v):
                    literals.add(v)

    return refs, literals


async def run(project_id: str, dry: bool) -> None:
    pid = _uuid.UUID(project_id)
    engine = create_async_engine(settings.database_url)
    S = async_sessionmaker(engine, expire_on_commit=False)
    async with S() as s:
        refs, literals = await collect_used(s, pid)
        rows = (await s.execute(
            select(ProjectI18nMessage).where(ProjectI18nMessage.project_id == pid)
        )).scalars().all()

        keep, drop = [], []
        for r in rows:
            zh = (r.translations or {}).get("zh-CN") or ""
            by_key = r.key_text in refs
            by_text = bool(zh) and zh in literals
            (keep if (by_key or by_text or r.source == "manual") else drop).append(
                (r, "按键引用" if by_key else ("脚本里还写死着中文" if by_text else "手工录入")))

        print(f"词典 {len(rows)} 条：留 {len(keep)}，清 {len(drop)}")
        for r, why in keep:
            zh = (r.translations or {}).get("zh-CN") or ""
            print(f"  留 {r.key_text:52} {zh:14} ← {why}")
        if not dry:
            for r, _ in drop:
                await s.delete(r)
            await s.commit()
            print(f"\n已清掉 {len(drop)} 条没有任何测试引用的。")
        else:
            print(f"\n（dry-run，未写库）")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.project, a.dry_run))
