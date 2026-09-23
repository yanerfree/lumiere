"""LLM Mock 内置套件的封样：**幂等** + **不碰别人的路由**。

这两条是它唯一危险的地方 —— 它在每次启动时都跑一遍：

1. 不幂等就会每次重启多出一批重复路由，而重复路由在运行时是「后一条被永久顶掉、
   还偶发」，查起来像 mock 服务自己抽风。
2. 覆盖已有同名路径的行为，表现是「昨天好好的用例今天红了」，而且没有任何报错
   指向这次启动 —— 那是最难查的一种。

所以这里钉的不是「建出来几条」，而是「再跑一遍不变」和「别人的配置一个字没动」。
"""
import pytest
from sqlalchemy import func, select

from app.models.llm_mock import MockRoute
from app.services.llm_mock_builtin import builtin_specs, ensure_builtin_routes

pytestmark = pytest.mark.asyncio


async def _count(session) -> int:
    return await session.scalar(select(func.count(MockRoute.id))) or 0


async def test_幂等_跑两遍不会多出路由(db_session):
    specs = builtin_specs()
    stats1 = await ensure_builtin_routes(db_session)
    await db_session.flush()
    n1 = await _count(db_session)
    assert stats1["created"] == len(specs)

    stats2 = await ensure_builtin_routes(db_session)
    await db_session.flush()
    n2 = await _count(db_session)
    assert stats2["created"] == 0, "第二次不该再建任何路由"
    assert n2 == n1


async def test_收编已有路由时行为一个字不改(db_session):
    """库里已经有一条 /v1/chat/completions（别人建的、改过配置）——
    启动时只许给它贴标签，不许把响应体/状态码/延迟改回内置定义。"""
    mine = MockRoute(
        name="我自己配的",
        method="POST",
        path="/v1/chat/completions",
        response_body="别动我",
        status_code=418,
        delay_ms=1234,
        locked=False,
    )
    db_session.add(mine)
    await db_session.flush()

    await ensure_builtin_routes(db_session)
    await db_session.flush()
    await db_session.refresh(mine)

    # 行为：一个字没动
    assert mine.response_body == "别动我"
    assert mine.status_code == 418
    assert mine.delay_ms == 1234
    # 元信息：被收编进内置分组，有了用途说明
    assert mine.builtin is True
    assert mine.category == "protocol"
    assert mine.purpose and mine.usage_hint


async def test_改过的内置路由不会被启动冲回去(db_session):
    """解锁改过之后又重启 —— 内置路由的行为也不该被冲回定义值。
    反过来做的话，「我明明改了」和「它自己变回去了」会变成没人能复现的怪事。"""
    await ensure_builtin_routes(db_session)
    await db_session.flush()

    row = (await db_session.execute(
        select(MockRoute).where(MockRoute.path == "/429/v1/chat/completions")
    )).scalars().first()
    assert row is not None
    row.status_code = 200
    row.response_body = "我把它改成正常的了"
    row.name = "改过名字"
    await db_session.flush()

    await ensure_builtin_routes(db_session)
    await db_session.flush()
    await db_session.refresh(row)

    assert row.status_code == 200, "行为不该被冲回 429"
    assert row.response_body == "我把它改成正常的了"
    assert row.name == "429 限频（带 Retry-After）", "名称属于元信息，应该刷成最新"


async def test_从不删除任何路由(db_session):
    """内置套件只增不删 —— 别人建的路由不在定义里，也必须原样留着。"""
    other = MockRoute(name="别人的", method="POST", path="/who-cares/v1/chat/completions")
    db_session.add(other)
    await db_session.flush()

    await ensure_builtin_routes(db_session)
    await ensure_builtin_routes(db_session)
    await db_session.flush()

    still = (await db_session.execute(
        select(MockRoute).where(MockRoute.path == "/who-cares/v1/chat/completions")
    )).scalars().first()
    assert still is not None
    assert still.builtin is False
