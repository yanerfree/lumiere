"""页面枚举的 arq 任务入口。**很薄，故意的。**

判断全在 `app/services/qa_survey_guard.py`（纯函数、可测），编排在
`app/engine/surveys/qa_page_survey_crawl.py`，两趟对比和落库在
`app/services/qa_page_survey.py`。这一层只做四件事：置状态、调编排、落库、
把异常翻成人看得懂的一句话。

**任务函数必须登记进 `app/engine/worker.py` 的 `functions` 白名单**（架构 AD-4）——
arq 只跑白名单里的函数，没登记的 enqueue 之后既不执行也不报错，就一直躺在 redis 里。
页面上看到的是「一直在跑」，查不出任何原因。
"""
import logging
import uuid

from app.engine.task_status import set_task_status

logger = logging.getLogger(__name__)


async def run_page_survey(ctx: dict, task_id: str, project_id: str,
                          roles: list[str], page_paths: list[str],
                          base_url: str | None = None,
                          env_id: str | None = None, env_name: str = "",
                          build_fingerprint: str = "",
                          route_table_hash: str = "",
                          qa_commit_sha: str = "") -> dict:
    """跑一趟页面枚举 —— **先判要不要跑**（架构 AD-8）。

    终态四选一：`done` / `partial` / `failed` / `dirty` —— 比 task_status 文档里那条
    `completed/failed` 多两个，是有意的：
    - `partial`：爬到一半有页面没打开。**它不是"基本成功"**，缺的那部分信号在对账
      那边必须算「没验证」；混进 `done` 就会让一批没爬到的页面被报成「功能没了」。
    - `dirty`：只读爬完，环境里的数却变了。这时候最该看的不是结果而是**我们动了什么**，
      所以它压过 `failed`（判定在 `resolve_terminal_status`）。

    **复用那一支的终态是被复用那一趟的终态**（不是 `done`，也没有第五种状态）。
    判要不要跑的是 `qa_survey_cache.plan_reuse`，**这一层不重复它的判断**，
    只负责判完照做、并且把「用的是哪一趟」原样带出去（`cacheNote` / `provenance`）。
    """
    from app.engine.surveys.qa_page_survey_crawl import run_survey
    from app.services.qa_survey_cache import (CRAWL, fresh_provenance, plan_reuse,
                                              survey_key)

    await set_task_status(task_id, "running", message="正在枚举页面可操作项…")

    # 没身份就不必去翻库：`plan_reuse` 第一支就判重爬，`previous` 那一格它根本不看。
    # **这不是第二个判官** —— 判「要不要复用」的仍然只有 `plan_reuse`（连那句理由
    # 也是它说的），这里只判「值不值得为它跑一趟库」。让这个抄近路成立的不变量
    # 是「key 为空 ⇒ 一定重爬且不读 previous」，它在 test_qa_survey_cache 里钉着。
    has_key = survey_key(project_id=project_id, env_id=env_id,
                         build_fingerprint=build_fingerprint) is not None
    try:
        prev = (await _previous(project_id=project_id, env_id=env_id,
                                qa_commit_sha=qa_commit_sha)) if has_key else None
    except Exception as e:                                   # noqa: BLE001
        # 查不到上一趟 ⇒ **照重爬走**，多爬一趟的代价看得见。
        # 但**这句话得我们自己说**：不能借 `plan_reuse` 那句「没有上一趟：首次必须
        # 整站」—— 上一趟很可能是有的，我们只是没读到。把"没读到"写成"没有"，
        # 正是这个模块存在的意义要抓的那类错（洞四同形）。
        logger.exception("查上一趟枚举失败 task_id=%s project_id=%s", task_id, project_id)
        plan = None
        cache_note = (f"没能查到上一趟枚举（{type(e).__name__}: {e}）——"
                      f"这一轮**没有做过复用判断**，按重爬处理")
    else:
        plan = plan_reuse(previous=prev, current={
            "projectId": project_id, "envId": env_id,
            "buildFingerprint": build_fingerprint,
            "routeTableHash": route_table_hash, "qaCommitSha": qa_commit_sha})
        cache_note = plan["summary"]
        if plan["action"] != CRAWL:
            return await _reuse(task_id, project_id, plan)

    try:
        payload = await run_survey(base_url=base_url, roles=roles,
                                   page_paths=page_paths)
    except ValueError as e:
        # 配置不全（没有 BASE_URL、没有只读账号）——**不开爬**，并且把原因原样带出去。
        await set_task_status(task_id, "failed", message=str(e))
        return {"status": "failed", "error": str(e), "cacheNote": cache_note}
    except Exception as e:                                   # noqa: BLE001
        logger.exception("页面枚举失败 task_id=%s project_id=%s", task_id, project_id)
        await set_task_status(task_id, "failed",
                              message=f"页面枚举失败：{type(e).__name__}: {e}")
        return {"status": "failed", "error": f"{type(e).__name__}: {e}",
                "cacheNote": cache_note}

    ledger = payload["ledger"]
    result = {
        "status": payload["status"],
        "projectId": project_id,
        "ledger": ledger,
        "itemCount": len(payload["items"]),
        "items": payload["items"],
        # 重爬的那一轮也带这一句（内容是"为什么没复用"）。只在复用时才出现的字段，
        # 和"没记过"长得一模一样 —— 同 `_provenance` 的纪律。
        "cacheNote": cache_note,
        "provenance": (plan or {}).get("provenance") or fresh_provenance(),
    }

    try:
        result["surveyId"] = str(await _persist(
            project_id=project_id, env_id=env_id, env_name=env_name,
            build_fingerprint=build_fingerprint, route_table_hash=route_table_hash,
            roles=roles, payload=payload))
    except Exception as e:                                   # noqa: BLE001
        # **爬完了但没存下来 ≠ 这一趟成功。** 存不下来的一趟对下游等于没发生过，
        # 而它已经真的去访问过别人的环境了 —— 这两件事都得说出来，
        # 所以状态落 `failed`、消息里带上爬取本身的终态。
        logger.exception("页面枚举落库失败 task_id=%s project_id=%s", task_id, project_id)
        result["persisted"] = False
        result["crawlStatus"] = payload["status"]
        result["status"] = "failed"
        result["error"] = f"落库失败：{type(e).__name__}: {e}"
        await set_task_status(task_id, "failed",
                              message=(f"爬完了（{payload['status']}）但没存下来："
                                       f"{type(e).__name__}: {e}"),
                              result={k: v for k, v in result.items() if k != "items"})
        return result

    result["persisted"] = True
    await set_task_status(task_id, payload["status"],
                          message=(f"枚举完成：{len(payload['items'])} 个可操作项，"
                                   f"拦下 {ledger.get('writesBlocked', 0)} 个写请求"),
                          result={k: v for k, v in result.items() if k != "items"})
    return result


async def _previous(*, project_id, env_id, qa_commit_sha):
    """上一趟是哪一趟。自己开 session —— 同 `_persist`，请求那条早关了。

    **异常一律往上抛，不在这里吞成 `None`。** 吞了的话「库里查不到上一趟」和
    「查这一下失败了」会长成同一个 `{}`，而 `plan_reuse` 拿到 `{}` 说的是
    「没有上一趟：首次必须整站」—— 一句我们并没有验证过的话。
    """
    from app.deps.db import async_session_factory
    from app.services.qa_page_survey import latest_survey
    from app.services.qa_survey_cache import previous_of

    async with async_session_factory() as session:
        survey = await latest_survey(
            session, uuid.UUID(str(project_id)),
            uuid.UUID(str(env_id)) if env_id else None)
        return previous_of(survey, qa_commit_sha=qa_commit_sha)


async def _reuse(task_id: str, project_id: str, plan: dict) -> dict:
    """这一轮**不去爬别人的环境**。AD-8 省下来的就是这一趟。

    三处刻意：

    1. **终态照抄被复用那一趟的**，不另造一个 `reused` 状态，也不一律写 `done`。
       写死 `done` 的话，一趟 `partial`（有页面没打开）复用之后会渲染得跟整站
       爬完一模一样 —— 缺的那部分页面就此消失。
    2. **不给 `itemCount` / `items` / `ledger`。** 这一轮没有去数，给个 0 就是
       CLAUDE.md 里那条「新增字段渲染成假的 0」的自造版本：一个没发生过的观测
       长得像一个观测到 0 的结果。下游要 item 就该顺着 `provenance.surveyId`
       去读那一趟 —— 让它 KeyError，比让它渲染 0 好查。
    3. 消息就是 `summary` 本身：出处和结论焊在一句话里，谁都拆不开来只渲染前半句。
    """
    prov = plan["provenance"]
    result = {
        "status": prov["surveyStatus"],
        "projectId": project_id,
        "action": plan["action"],
        "recompute": plan["recompute"],
        "surveyId": prov["surveyId"],
        "provenance": prov,
        "cacheNote": plan["summary"],
        "reasons": plan["reasons"],
    }
    await set_task_status(task_id, prov["surveyStatus"],
                          message=plan["summary"], result=result)
    return result


async def _persist(*, project_id, env_id, env_name, build_fingerprint,
                   route_table_hash, roles, payload):
    """自己开 session 落库，返回 survey id。请求那条 session 早关了。"""
    from app.deps.db import async_session_factory
    from app.services.qa_page_survey import save_survey

    async with async_session_factory() as session:
        survey = await save_survey(
            session, project_id=uuid.UUID(str(project_id)),
            env_id=uuid.UUID(str(env_id)) if env_id else None, env_name=env_name,
            build_fingerprint=build_fingerprint, route_table_hash=route_table_hash,
            roles=roles, status=payload["status"], ledger=payload["ledger"],
            items=payload["items"])
        await session.commit()
        return survey.id
