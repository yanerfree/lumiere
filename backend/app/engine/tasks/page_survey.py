"""页面枚举的 arq 任务入口。**很薄，故意的。**

判断全在 `app/services/qa_survey_guard.py`（纯函数、可测），编排在
`app/engine/surveys/qa_page_survey_crawl.py`。这一层只做三件事：置状态、调编排、
把异常翻成人看得懂的一句话。

**任务函数必须登记进 `app/engine/worker.py` 的 `functions` 白名单**（架构 AD-4）——
arq 只跑白名单里的函数，没登记的 enqueue 之后既不执行也不报错，就一直躺在 redis 里。
页面上看到的是「一直在跑」，查不出任何原因。
"""
import logging

from app.engine.task_status import set_task_status

logger = logging.getLogger(__name__)


async def run_page_survey(ctx: dict, task_id: str, project_id: str,
                          roles: list[str], page_paths: list[str],
                          base_url: str | None = None) -> dict:
    """跑一趟页面枚举。

    终态四选一：`done` / `partial` / `failed` / `dirty` —— 比 task_status 文档里那条
    `completed/failed` 多两个，是有意的：
    - `partial`：爬到一半有页面没打开。**它不是"基本成功"**，缺的那部分信号在对账
      那边必须算「没验证」；混进 `done` 就会让一批没爬到的页面被报成「功能没了」。
    - `dirty`：只读爬完，环境里的数却变了。这时候最该看的不是结果而是**我们动了什么**，
      所以它压过 `failed`（判定在 `resolve_terminal_status`）。
    """
    from app.engine.surveys.qa_page_survey_crawl import run_survey

    await set_task_status(task_id, "running", message="正在枚举页面可操作项…")
    try:
        payload = await run_survey(base_url=base_url, roles=roles,
                                   page_paths=page_paths)
    except ValueError as e:
        # 配置不全（没有 BASE_URL、没有只读账号）——**不开爬**，并且把原因原样带出去。
        await set_task_status(task_id, "failed", message=str(e))
        return {"status": "failed", "error": str(e)}
    except Exception as e:                                   # noqa: BLE001
        logger.exception("页面枚举失败 task_id=%s project_id=%s", task_id, project_id)
        await set_task_status(task_id, "failed",
                              message=f"页面枚举失败：{type(e).__name__}: {e}")
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"}

    ledger = payload["ledger"]
    result = {
        "status": payload["status"],
        "projectId": project_id,
        "ledger": ledger,
        "itemCount": len(payload["items"]),
        "items": payload["items"],
    }
    await set_task_status(task_id, payload["status"],
                          message=(f"枚举完成：{len(payload['items'])} 个可操作项，"
                                   f"拦下 {ledger.get('writesBlocked', 0)} 个写请求"),
                          result={k: v for k, v in result.items() if k != "items"})
    return result
