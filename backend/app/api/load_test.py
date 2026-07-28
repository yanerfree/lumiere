"""压力测试管理 API"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps.db import get_db
from app.schemas.load_test import (
    LoadTestScenarioCreate,
    LoadTestScenarioResponse,
    LoadTestScenarioUpdate,
    LoadTestStartRequest,
    LoadTestStepCreate,
    LoadTestStepResponse,
    LoadTestStepUpdate,
    LoadTestRunResponse,
)
from app.services import load_test_service as svc
from app.services import load_test_runner as runner
from app.services import load_test_k6 as k6gen

router = APIRouter(prefix="/api/load-test", tags=["load-test"])


# ───── 场景管理 ─────

@router.get("/scenarios", response_model=list[LoadTestScenarioResponse])
async def list_scenarios(session: AsyncSession = Depends(get_db)):
    items = await svc.list_scenarios(session)
    return [LoadTestScenarioResponse.model_validate(s, from_attributes=True) for s in items]


@router.post("/scenarios", response_model=LoadTestScenarioResponse, status_code=201)
async def create_scenario(body: LoadTestScenarioCreate, session: AsyncSession = Depends(get_db)):
    scenario = await svc.create_scenario(session, body)
    return LoadTestScenarioResponse.model_validate(scenario, from_attributes=True)


@router.get("/scenarios/{scenario_id}", response_model=LoadTestScenarioResponse)
async def get_scenario(scenario_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    scenario = await svc.get_scenario(session, scenario_id)
    if not scenario:
        return JSONResponse({"error": "Scenario not found"}, status_code=404)
    return LoadTestScenarioResponse.model_validate(scenario, from_attributes=True)


@router.get("/scenarios/{scenario_id}/k6-script")
async def export_k6_script(scenario_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    """导出 k6 压测脚本 + 部署指引（不运行，供在专用压测机上执行）。"""
    scenario = await svc.get_scenario(session, scenario_id)
    if not scenario:
        return JSONResponse({"error": "Scenario not found"}, status_code=404)
    steps = await svc.list_steps(session, scenario_id)
    if not steps:
        return JSONResponse({"error": "该场景还没有配置任何步骤"}, status_code=400)
    config = {
        "concurrent_users": scenario.concurrent_users,
        "ramp_up_seconds": scenario.ramp_up_seconds,
        "total_iterations": scenario.total_iterations,
        "duration_seconds": scenario.duration_seconds,
        "variables": scenario.variables or [],
        "steps": [
            {
                "name": s.name, "method": s.method, "url": s.url, "headers": s.headers,
                "body": s.body, "body_type": s.body_type,
                "extractions": s.extractions, "assertions": s.assertions,
            }
            for s in steps
        ],
    }
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in (scenario.name or "scenario"))[:60] or "scenario"
    return {
        "filename": f"{safe}.js",
        "script": k6gen.generate_k6_script(config, scenario.name or ""),
        "deployGuide": k6gen.generate_deploy_guide(config, scenario.name or ""),
        "stepCount": len(steps),
    }


@router.put("/scenarios/{scenario_id}", response_model=LoadTestScenarioResponse)
async def update_scenario(
    scenario_id: uuid.UUID, body: LoadTestScenarioUpdate, session: AsyncSession = Depends(get_db)
):
    scenario = await svc.update_scenario(session, scenario_id, body)
    if not scenario:
        return JSONResponse({"error": "Scenario not found"}, status_code=404)
    return LoadTestScenarioResponse.model_validate(scenario, from_attributes=True)


@router.delete("/scenarios/{scenario_id}")
async def delete_scenario(scenario_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    ok = await svc.delete_scenario(session, scenario_id)
    if not ok:
        return JSONResponse({"error": "Scenario not found"}, status_code=404)
    return {"ok": True}


# ───── 步骤管理 ─────

@router.get("/scenarios/{scenario_id}/steps", response_model=list[LoadTestStepResponse])
async def list_steps(scenario_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    steps = await svc.list_steps(session, scenario_id)
    return [LoadTestStepResponse.model_validate(s, from_attributes=True) for s in steps]


@router.post("/scenarios/{scenario_id}/steps", response_model=LoadTestStepResponse, status_code=201)
async def create_step(
    scenario_id: uuid.UUID, body: LoadTestStepCreate, session: AsyncSession = Depends(get_db)
):
    step = await svc.create_step(session, scenario_id, body)
    return LoadTestStepResponse.model_validate(step, from_attributes=True)


@router.put("/steps/{step_id}", response_model=LoadTestStepResponse)
async def update_step(step_id: uuid.UUID, body: LoadTestStepUpdate, session: AsyncSession = Depends(get_db)):
    step = await svc.update_step(session, step_id, body)
    if not step:
        return JSONResponse({"error": "Step not found"}, status_code=404)
    return LoadTestStepResponse.model_validate(step, from_attributes=True)


@router.delete("/steps/{step_id}")
async def delete_step(step_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    ok = await svc.delete_step(session, step_id)
    if not ok:
        return JSONResponse({"error": "Step not found"}, status_code=404)
    return {"ok": True}


@router.put("/scenarios/{scenario_id}/steps/reorder")
async def reorder_steps(scenario_id: uuid.UUID, body: dict, session: AsyncSession = Depends(get_db)):
    items = body.get("items", [])
    await svc.reorder_steps(session, items)
    return {"ok": True}


# ───── 执行管理 ─────

@router.get("/scenarios/{scenario_id}/runs", response_model=list[LoadTestRunResponse])
async def list_runs(
    scenario_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
):
    runs = await svc.list_runs(session, scenario_id, limit=limit)
    return [LoadTestRunResponse.model_validate(r, from_attributes=True) for r in runs]


@router.get("/runs/{run_id}", response_model=LoadTestRunResponse)
async def get_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    run = await svc.get_run(session, run_id)
    if not run:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    return LoadTestRunResponse.model_validate(run, from_attributes=True)


@router.post("/scenarios/{scenario_id}/run")
async def start_run(
    scenario_id: uuid.UUID,
    body: LoadTestStartRequest | None = None,
    session: AsyncSession = Depends(get_db),
):
    overrides = body.model_dump(exclude_unset=True) if body else {}
    try:
        run_id = await runner.start_run(scenario_id, overrides or None)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, "runId": str(run_id)}


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: uuid.UUID):
    active = runner.get_runner(run_id)

    async def event_generator():
        try:
            async for event in runner.execute_run(run_id):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except ValueError as e:
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(e)}})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: uuid.UUID):
    active = runner.get_runner(run_id)
    if not active:
        return JSONResponse({"error": "Run not found or already finished"}, status_code=404)
    active.cancel()
    return {"ok": True}


@router.delete("/runs/{run_id}")
async def delete_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    ok = await svc.delete_run(session, run_id)
    if not ok:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    return {"ok": True}
