from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.common import BaseSchema


# ───── Mock Route Schemas ─────

class MockRouteCreate(BaseSchema):
    name: str = Field(max_length=100)
    method: str = Field(default="POST", max_length=10)
    path: str = Field(default="/v1/chat/completions", max_length=500)
    enabled: bool = True
    delay_ms: int = Field(default=0, ge=0)
    status_code: int = Field(default=200)
    response_format: str = Field(default="json")
    preset_mode: str | None = None
    response_mode: str = Field(default="default")
    finish_reason: str = Field(default="stop")
    response_body: str = "This is a mock response from the LLM Mock service."
    token_mode: str = Field(default="auto")
    custom_prompt_tokens: int | None = None
    custom_completion_tokens: int | None = None
    model_mode: str = Field(default="follow_request")
    custom_model: str | None = None
    response_headers: dict | None = None
    sse_chunk_delay_ms: int = Field(default=50, ge=0)
    sse_chunk_size: int = Field(default=1, ge=1, le=4096)
    stream_mode: str = Field(default="auto", pattern="^(auto|force_stream|force_json)$")
    response_type: str = Field(default="text")
    tool_calls: list | None = None
    # 智能应答：默认关，开了才由请求里的指令决定行为（见 llm_mock_smart.py）
    smart_enabled: bool = False
    smart_role: str = Field(default="auto", pattern="^(auto|upstream|checker)$")
    smart_body_marker: str | None = Field(default=None, max_length=200)


class MockRouteUpdate(BaseSchema):
    name: str | None = None
    method: str | None = None
    path: str | None = None
    enabled: bool | None = None
    delay_ms: int | None = None
    status_code: int | None = None
    response_format: str | None = None
    preset_mode: str | None = Field(default=None)
    response_mode: str | None = None
    finish_reason: str | None = None
    response_body: str | None = None
    token_mode: str | None = None
    custom_prompt_tokens: int | None = None
    custom_completion_tokens: int | None = None
    model_mode: str | None = None
    custom_model: str | None = None
    response_headers: dict | None = None
    sse_chunk_delay_ms: int | None = None
    sse_chunk_size: int | None = Field(default=None, ge=1, le=4096)
    stream_mode: str | None = Field(default=None, pattern="^(auto|force_stream|force_json)$")
    response_type: str | None = None
    tool_calls: list | None = None
    smart_enabled: bool | None = None
    smart_role: str | None = Field(default=None, pattern="^(auto|upstream|checker)$")
    smart_body_marker: str | None = Field(default=None, max_length=200)


class MockRouteResponse(BaseSchema):
    id: uuid.UUID
    name: str
    method: str
    path: str
    enabled: bool
    locked: bool
    sort_order: int
    delay_ms: int
    status_code: int
    response_format: str
    preset_mode: str | None
    response_mode: str
    finish_reason: str
    response_body: str
    token_mode: str
    custom_prompt_tokens: int | None
    custom_completion_tokens: int | None
    model_mode: str
    custom_model: str | None
    response_headers: dict | None
    sse_chunk_delay_ms: int
    sse_chunk_size: int
    stream_mode: str
    response_type: str
    tool_calls: list | None
    smart_enabled: bool
    smart_role: str
    smart_body_marker: str | None
    hit_count: int
    last_hit_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ───── Mock Request Log Schemas ─────

class MockLogResponse(BaseSchema):
    id: uuid.UUID
    route_id: uuid.UUID | None
    timestamp: datetime
    method: str
    path: str
    caller: str | None
    ip: str | None
    status_code: int
    request_model: str | None
    response_model: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    finish_reason: str | None
    # 智能应答判定（没开智能应答时为 null）
    smart_meta: dict | None = None
    match_ms: float
    first_byte_ms: float
    body_ms: float
    total_ms: float

    model_config = {"from_attributes": True}


class MockLogDetailResponse(MockLogResponse):
    request_headers: dict | None
    request_body: dict | None
    response_body: str | None
    response_headers_out: dict | None


# ───── Mock Config / Status Schemas ─────

class MockServiceStatus(BaseSchema):
    running: bool
    port: int
    capture_enabled: bool
    routes_count: int
    routes_enabled: int
    total_requests: int


class MockServiceConfig(BaseSchema):
    port: int = 28100
    capture_enabled: bool = True
    max_log_count: int = 1000
    listen_host: str = "0.0.0.0"


# ───── Reorder ─────

class ReorderItem(BaseSchema):
    id: uuid.UUID
    sort_order: int


class ReorderRequest(BaseSchema):
    items: list[ReorderItem]
