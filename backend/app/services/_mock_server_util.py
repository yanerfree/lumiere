"""Mock 服务启动的健壮性工具。

背景：各 Mock 服务用 `asyncio.create_task(uvicorn_server.serve())` 在主事件循环里
运行。uvicorn 绑端口失败时会调用 `sys.exit(1)` 抛出 **SystemExit**。SystemExit 属
BaseException，不被 `_restore_mock_services` 的 `except Exception` 捕获，且 asyncio 会把
它从 task 传播到主事件循环，直接把整个后端 uvicorn 进程干掉。

`guarded_serve` 把 serve() 包一层，SystemExit / 普通异常都只留在本 task 内并记日志，
绝不外泄；`await_started` 负责判定"到底起来没有"，供 start() 决定是否抛错。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

logger = logging.getLogger(__name__)


async def guarded_serve(server, label: str) -> None:
    """运行 uvicorn Server.serve()，但把 SystemExit（绑端口失败）关在本 task 内，
    避免它传播到主事件循环导致整个后端崩溃。CancelledError 正常向上传播（stop 时需要）。"""
    try:
        await server.serve()
    except asyncio.CancelledError:
        raise
    except SystemExit as e:
        port = getattr(getattr(server, "config", None), "port", "?")
        logger.error("%s 启动失败（端口 %s 可能被占用）：SystemExit %s", label, port, e)
    except Exception as e:  # 防御性：任何异常都不该让一个 mock 拖垮后端
        logger.error("%s 运行异常：%s", label, e)


def unlatch_sse_shutdown(dead_server=None) -> None:
    """把 sse_starlette 的「进程要退出了」全局开关掰回去。

    **不掰回来的后果**：mock 服务停一次之后，这个进程里**所有**基于
    SSE 的响应都会立刻被掐断 —— 包括平台自己挂在 :18800 上的 MCP。
    表现是 HTTP 200 已经发出去了、响应体一个字没有，uvicorn 只在日志里留一句
    `ASGI callable returned without completing response.`，看着像对面客户端的毛病。
    实测：同一个进程里起第二次 uvicorn，MCP 的 initialize 必挂。

    原因在 `sse_starlette.sse.AppStatus.should_exit` —— 那是个**进程级**的类属性，
    表示「uvicorn 要退出了，SSE 流赶紧收尾」。它有两条被置位的路：
      ① SIGTERM（它给 `uvicorn.Server.handle_exit` 打了猴子补丁）；
      ② 一个后台轮询协程，每 0.5s 去看 `signal.getsignal(SIGTERM).__self__.should_exit`
         —— 也就是**当前装着信号处理器的那个 uvicorn Server**。
    而我们停 mock 就是把自己那个 server 的 `should_exit` 置 True，于是 ② 把全局开关
    拨上去了，**而且没人负责拨回来**（库里默认一个进程只起一次 uvicorn）。

    所以两件事都要做：把已经死掉的那个 server 的标志位改回去（轮询看的是它），
    再把全局开关清掉（可能已经被拨上去了）。真正的进程退出不受影响 —— 那条路走 ①。
    """
    if dead_server is not None:
        with contextlib.suppress(Exception):
            dead_server.should_exit = False
    try:
        from sse_starlette.sse import AppStatus
    except Exception:  # noqa: BLE001 — 没装就没这个坑
        return
    with contextlib.suppress(Exception):
        AppStatus.should_exit = False
