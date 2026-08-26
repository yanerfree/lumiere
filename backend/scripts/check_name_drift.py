#!/usr/bin/env python
"""对账「库里存的平台自有名字」和「代码里真实存在的名字」。

    cd backend && .venv/bin/python scripts/check_name_drift.py
    cd backend && .venv/bin/python scripts/check_name_drift.py --strict   # 有漂移就非零退出

为什么需要它：MCP 工具名和平台 skill 名有**两份**。一份在代码里（`TOOL_CATALOG`、
`app/skills/preset/` 的目录名），单测盯着（`tests/test_mcp_profiles.py`）；另一份
**落在库里** —— `projects.mcp_allowed_tools`、`ai_capability_bindings`。库里那份没有
任何测试能覆盖：测试跑的是每次重建的空库，生产数据根本不在里面。

对不上的后果全是安静的：

- 工具范围里有个不存在的名字 → `tools/list` 就少一个，外部 CC 用到才发现调不动；
- 能力绑定的 module_keys 对不上 → 「AI 能力→模型」页上那一档变成绑不上模型的空档位。

所以这是「改名窗口」的验收判据（迁移跑完必须一条不剩），平时也该定期跑 ——
删工具、手工改库、从别的环境导数据，都会把它弄漂。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                                      # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine           # noqa: E402

from app.config import settings                                  # noqa: E402
from app.mcp import TOOL_CATALOG                                 # noqa: E402

PRESET_DIR = Path(__file__).resolve().parents[1] / "app" / "skills" / "preset"


async def scan() -> list[str]:
    tools = {t["name"] for t in TOOL_CATALOG}
    presets = {p.name for p in PRESET_DIR.iterdir() if p.is_dir()}
    bad: list[str] = []
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        rows = await conn.execute(text(
            "select name, mcp_allowed_tools from projects "
            "where jsonb_typeof(mcp_allowed_tools) = 'array'"))
        for name, scope in rows:
            for t in sorted(set(scope) - tools):
                bad.append(f"projects「{name}」.mcp_allowed_tools: 「{t}」不在注册表里")

        rows = await conn.execute(text(
            "select key, module_keys from ai_capability_bindings"))
        for key, modules in rows:
            for m in sorted(set(modules or []) - presets):
                bad.append(f"ai_capability_bindings「{key}」.module_keys: 「{m}」"
                           f"在 app/skills/preset/ 下没有对应目录")
    await engine.dispose()
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="有漂移就返回 1（CI / 迁移验收用）")
    args = ap.parse_args()

    bad = asyncio.run(scan())
    if not bad:
        print(f"名字对账通过：{len(TOOL_CATALOG)} 个工具、"
              f"{len(list(PRESET_DIR.iterdir()))} 个预置 skill，库里引用的都存在。")
        return 0
    print(f"发现 {len(bad)} 处漂移 —— 库里引用了代码里不存在的名字：\n")
    for line in bad:
        print(f"  {line}")
    print("\n改名窗口里看到这个，说明迁移漏了；平时看到，说明有人手工改过库或删过工具。")
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
