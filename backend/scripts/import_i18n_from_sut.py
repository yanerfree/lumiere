"""从被测系统的 locale 文件导入译文，填进项目 i18n 词典。

**为什么需要它。** `project_i18n_messages` 里的词是采集器从 UI 脚本里抽的中文
字面量，`translations` 一直是空的 —— 于是脚本侧的 `t()` 运行时（见
docs/cc-platform-loop-spec.md §2.9）没法上：上线当天所有 t() 都查不到译文、
全部退回中文，看不出到底生效没有。**先有译文，再接 t()。**

译文不用人工填：被测系统自己就有 zh-CN / en-US 两套 locale 文件，key 路径一一对应，
按 key 路径把两边的值配对，就得到「中文 → 英文」的映射。

用法：
    python scripts/import_i18n_from_sut.py \
        --project <uuid> --base http://192.168.51.108:5176 \
        [--ns common,services,...] [--dry-run]

只补 translations，不覆盖已有的非空译文（人工改过的优先）。
词典里没有的中文文案也会一并建进来 —— 采集器只抽脚本里用到的，
提前把整套文案备着，写新脚本时 t() 就查得到。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid as _uuid
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.i18n_message import ProjectI18nMessage  # noqa: E402
from app.models.project import Project  # noqa: E402,F401  外键指向它，不导入解析不了

DEFAULT_NS = ["common", "services", "subscription", "apps", "gateway",
              "upstream", "auth", "menu", "tenant", "application", "dashboard"]


def _fetch(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _flatten(d, prefix="") -> dict[str, str]:
    """{"a": {"b": "保存"}} → {"a.b": "保存"}。只收字符串叶子。"""
    out: dict[str, str] = {}
    for k, v in (d or {}).items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        elif isinstance(v, str):
            out[key] = v
    return out


def build_pairs(base: str, namespaces: list[str]) -> dict[str, str]:
    """返回 {中文文案: 英文文案}。

    **按 key 路径配对，不按顺序配对** —— 两个文件的键序不保证一致，
    按顺序配会把「保存」映射成「Cancel」，而且错得悄无声息。
    """
    pairs: dict[str, str] = {}
    for ns in namespaces:
        zh = _fetch(f"{base}/src/i18n/locales/zh-CN/{ns}.json")
        en = _fetch(f"{base}/src/i18n/locales/en-US/{ns}.json")
        if not zh or not en:
            print(f"  跳过 {ns}（取不到）")
            continue
        fz, fe = _flatten(zh), _flatten(en)
        hit = 0
        for key, zh_text in fz.items():
            en_text = fe.get(key)
            # 没有对应英文、或两边一样（多半是没翻译的占位）→ 不收
            #
            # 长文本也不收：locale 里混着多行 YAML/代码示例（一条能到 1.4KB），
            # 那不是测试会去断言的 UI 文案，而且 key_text 列只有 500 字。
            # 判据用「有换行」而不是只看长度 —— 单行的长提示语仍然有用。
            if len(zh_text) > 200 or "\n" in zh_text:
                continue
            if en_text and en_text != zh_text:
                pairs.setdefault(zh_text, en_text)
                hit += 1
        print(f"  {ns}: {hit}/{len(fz)} 条可配对")
    return pairs


async def run(project_id: str, base: str, namespaces: list[str], dry: bool) -> None:
    pid = _uuid.UUID(project_id)   # 列是 UUID 型，传字符串会 operator does not exist
    pairs = build_pairs(base, namespaces)
    print(f"\n配对总数（去重后）：{len(pairs)}")
    if not pairs:
        return

    engine = create_async_engine(settings.database_url)
    S = async_sessionmaker(engine, expire_on_commit=False)
    async with S() as s:
        rows = (await s.execute(
            select(ProjectI18nMessage).where(ProjectI18nMessage.project_id == pid)
        )).scalars().all()
        existing = {r.key_text: r for r in rows}

        filled = kept = added = 0
        for zh_text, en_text in pairs.items():
            row = existing.get(zh_text)
            if row is None:
                added += 1
                if not dry:
                    s.add(ProjectI18nMessage(
                        project_id=pid, key_text=zh_text,
                        translations={"en-US": en_text},
                        category="text", source="sut_locale",
                        description="从被测系统 locale 文件导入"))
                continue
            cur = row.translations or {}
            if (cur.get("en-US") or "").strip():
                kept += 1          # 人工改过的优先，不覆盖
                continue
            filled += 1
            if not dry:
                row.translations = {**cur, "en-US": en_text}
        if not dry:
            await s.commit()

        print(f"补上译文 {filled} 条 | 新建 {added} 条 | 已有译文跳过 {kept} 条"
              f"{'（dry-run，未写库）' if dry else ''}")

        if not dry:
            left = [r.key_text for r in rows
                    if not ((r.translations or {}).get("en-US") or "").strip()
                    and r.key_text not in pairs]
            if left:
                print(f"\n⚠ 还有 {len(left)} 条查不到译文，要人工填（被测系统 locale 里没有）：")
                for t in left[:10]:
                    print(f"    {t}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--base", required=True, help="被测系统前端地址，如 http://host:5176")
    ap.add_argument("--ns", default=",".join(DEFAULT_NS))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.project, a.base, [x for x in a.ns.split(",") if x], a.dry_run))
