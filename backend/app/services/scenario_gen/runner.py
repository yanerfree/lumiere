"""生成任务运行器 — 串联真实 AI 调用链路

创建任务 → 提取需求点 → (用户确认后) 建模 → (用户确认后) 逐测试点展开用例
每一段由 API 端点触发，在后台 asyncio 任务中执行。
"""
from __future__ import annotations

import logging
import uuid

from app.deps.db import async_session_factory
from app.services.ai_config_resolver import resolve_ai_config
from app.services.scenario_gen import pipeline
from app.services.scenario_gen.preprocessor import preprocess

logger = logging.getLogger("scenario_gen.runner")


async def _refresh_progress(session, task_id: uuid.UUID, total: int) -> None:
    """把 task.progress 按 items 的**真实状态**重算一遍。

    原来 progress 只在展开开始时写了一次 {"total": N, "succeeded": 0, ...}，
    之后从头到尾没再动过 —— 成功数只加在内存变量里。结果任务跑完了，任务中心和
    详情页显示的还是「0/11」，人以为一条都没生成（实测 11 条全成功、显示 0/11）。
    统计以 DB 为准而不是内存计数：这里是 3 个协程各自的 session 并发写同一行，
    内存计数在别的协程里看不见。
    """
    from sqlalchemy import func, select

    from app.models.scenario_gen import GenerationItem, GenerationTask

    rows = (await session.execute(
        select(GenerationItem.status, func.count())
        .where(GenerationItem.task_id == task_id)
        .group_by(GenerationItem.status)
    )).all()
    counts = {s: n for s, n in rows}
    task = await session.get(GenerationTask, task_id)
    if task:
        task.progress = {
            "total": total,
            "succeeded": counts.get("succeeded", 0),
            "failed": counts.get("failed", 0),
            "skipped": counts.get("skipped", 0),
        }


async def run_extraction(task_id: uuid.UUID, project_id: uuid.UUID):
    """后台执行需求点提取 + 质量检测"""
    try:
        async with async_session_factory() as session:
            from app.models.scenario_gen import GenerationTask, RequirementDoc
            task = await session.get(GenerationTask, task_id)
            if not task or task.status != "extracting":
                return

            config = await resolve_ai_config(project_id, session, capability="scenario-gen")
            if not config:
                await pipeline.transition(session, task, "failed", error_message="AI 配置未找到，请先在项目设置中配置 AI 服务")
                await session.commit()
                return

            doc = await session.get(RequirementDoc, task.doc_id)
            if not doc:
                await pipeline.transition(session, task, "failed", error_message="需求文档未找到")
                await session.commit()
                return

            # 预处理
            prep = doc.content_meta or preprocess(doc.content_markdown)
            chunks = prep.get("chunks", [doc.content_markdown])

            # 提取需求点
            await pipeline.emit_event(session, task.id, "task_state", {"status": "extracting", "step": "提取需求点..."})
            await session.commit()

            from app.services.scenario_gen.extractor import extract_requirement_points
            points = await extract_requirement_points(
                config, doc.content_markdown, task.id, doc.id, session, project_id,
                chunks=chunks if len(chunks) > 1 else None,
            )
            logger.info("任务 %s: 提取到 %d 个需求点", task_id, len(points))

            # 质量检测
            await pipeline.emit_event(session, task.id, "task_state", {"status": "extracting", "step": "质量检测..."})
            await session.commit()

            from app.services.scenario_gen.health_check import check_requirement_quality
            health = await check_requirement_quality(config, doc.content_markdown, session, project_id)
            task.health_check = health

            # 更新进度
            task.progress = {"extracted_points": len(points)}
            await pipeline.transition(session, task, "model_ready")
            await session.commit()
            logger.info("任务 %s: 提取完成，状态 → model_ready", task_id)

    except Exception as e:
        logger.error("任务 %s 提取失败: %s", task_id, e, exc_info=True)
        try:
            async with async_session_factory() as session:
                task = await session.get(GenerationTask, task_id)
                if task and task.status == "extracting":
                    await pipeline.transition(session, task, "failed", error_message=f"提取失败：{str(e)[:300]}")
                    await session.commit()
        except Exception:
            logger.error("任务 %s 标记失败也失败了", task_id, exc_info=True)


async def run_modeling(task_id: uuid.UUID, project_id: uuid.UUID):
    """后台执行场景模型生成"""
    try:
        async with async_session_factory() as session:
            from app.models.scenario_gen import GenerationTask
            task = await session.get(GenerationTask, task_id)
            if not task or task.status != "model_ready":
                return

            config = await resolve_ai_config(project_id, session, capability="scenario-gen")
            if not config:
                await pipeline.transition(session, task, "failed", error_message="AI 配置未找到")
                await session.commit()
                return

            await pipeline.emit_event(session, task.id, "task_state", {"status": "model_ready", "step": "生成场景模型..."})
            await session.commit()

            from app.services.scenario_gen.modeler import generate_scenario_model
            model = await generate_scenario_model(config, task, session)
            tp_count = len(model.test_points) if model.test_points else 0
            logger.info("任务 %s: 场景模型生成完成，%d 个测试点", task_id, tp_count)

            await pipeline.emit_event(session, task.id, "task_state", {"status": "model_ready", "step": f"模型就绪，{tp_count} 个测试点"})
            await session.commit()

    except Exception as e:
        logger.error("任务 %s 建模失败: %s", task_id, e, exc_info=True)
        try:
            async with async_session_factory() as session:
                from app.models.scenario_gen import GenerationTask
                task = await session.get(GenerationTask, task_id)
                if task and task.status == "model_ready":
                    task.error_message = f"场景模型生成失败：{str(e)[:300]}"
                    await pipeline.emit_event(session, task.id, "task_state", {"status": "model_ready", "error": str(e)[:200]})
                    await session.commit()
        except Exception:
            pass


async def run_expansion(task_id: uuid.UUID, project_id: uuid.UUID):
    """后台执行用例批量展开"""
    try:
        async with async_session_factory() as session:
            from app.models.scenario_gen import GenerationTask, GenerationItem, ScenarioModel
            from sqlalchemy import select

            task = await session.get(GenerationTask, task_id)
            if not task or task.status != "generating":
                return

            config = await resolve_ai_config(project_id, session, capability="scenario-gen")
            if not config:
                await pipeline.transition(session, task, "failed", error_message="AI 配置未找到")
                await session.commit()
                return

            # 获取场景模型的测试点
            model = (await session.execute(
                select(ScenarioModel).where(ScenarioModel.task_id == task_id)
            )).scalar_one_or_none()
            if not model or not model.test_points:
                await pipeline.transition(session, task, "failed", error_message="场景模型无测试点")
                await session.commit()
                return

            # 创建 generation_items（如果还没创建）
            existing_items = (await session.execute(
                select(GenerationItem).where(GenerationItem.task_id == task_id)
            )).scalars().all()

            if not existing_items:
                for tp in model.test_points:
                    ref = tp.get("ref", f"tp-{model.test_points.index(tp)+1}")
                    item = GenerationItem(
                        task_id=task_id,
                        test_point_ref=ref,
                        point_snapshot=tp,
                        status="pending",
                    )
                    session.add(item)
                await session.flush()
                existing_items = (await session.execute(
                    select(GenerationItem).where(GenerationItem.task_id == task_id)
                )).scalars().all()

            total = len(existing_items)
            task.progress = {"total": total, "succeeded": 0, "failed": 0, "skipped": 0}
            await session.commit()

            # 逐测试点展开（小批量并发，提速）
            from app.services.scenario_gen.expander import expand_single_test_point
            import asyncio
            succeeded = 0
            failed = 0
            semaphore = asyncio.Semaphore(3)

            async def expand_one(item):
                nonlocal succeeded, failed
                async with semaphore:
                    async with async_session_factory() as s2:
                        from app.models.scenario_gen import GenerationItem as GI
                        it = await s2.get(GI, item.id)
                        if not it or it.status in ("succeeded", "skipped"):
                            return
                        it.status = "running"
                        await s2.flush()
                        await pipeline.emit_event(s2, task_id, "point_start", {
                            "ref": it.test_point_ref,
                            "title": (it.point_snapshot or {}).get("title", ""),
                        })
                        await s2.commit()

                        t = await s2.get(GenerationTask, task_id)
                        case = await expand_single_test_point(
                            config, t, it, it.point_snapshot or {}, s2,
                        )

                        if case:
                            succeeded += 1
                            await pipeline.emit_event(s2, task_id, "case_created", {
                                "case_id": str(case.id),
                                "case_code": case.case_code,
                                "title": case.title,
                                "priority": case.priority,
                            })
                        else:
                            failed += 1
                            await pipeline.emit_event(s2, task_id, "point_failed", {
                                "ref": it.test_point_ref,
                                "error_message": it.error_message[:200] if it.error_message else "展开失败",
                            })
                        await _refresh_progress(s2, task_id, total)
                        await s2.commit()

            # 并发执行
            tasks_to_expand = [it for it in existing_items if it.status not in ("succeeded", "skipped")]
            await asyncio.gather(*[expand_one(it) for it in tasks_to_expand], return_exceptions=True)

            await _refresh_progress(session, task_id, total)

            # 最终状态
            if failed == 0:
                await pipeline.transition(session, task, "completed")
            elif succeeded > 0:
                await pipeline.transition(session, task, "partial_failed",
                    error_message=f"{failed}/{total} 个测试点展开失败")
            else:
                await pipeline.transition(session, task, "failed",
                    error_message=f"全部 {total} 个测试点展开失败")
            await session.commit()
            logger.info("任务 %s: 展开完成 %d/%d（失败 %d）", task_id, succeeded, total, failed)

    except Exception as e:
        logger.error("任务 %s 展开异常: %s", task_id, e, exc_info=True)
        try:
            async with async_session_factory() as session:
                from app.models.scenario_gen import GenerationTask
                task = await session.get(GenerationTask, task_id)
                if task and task.status == "generating":
                    await pipeline.transition(session, task, "partial_failed",
                        error_message=f"展开中断：{str(e)[:300]}")
                    await session.commit()
        except Exception:
            pass
