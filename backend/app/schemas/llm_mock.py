from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.common import BaseSchema


# ───── 条件应答规则 ─────

class MatchRule(BaseSchema):
    """一条「什么样的请求 → 回什么」的规则。从上往下匹配，第一条命中的生效。"""
    id: str | None = None
    enabled: bool = True
    name: str = Field(default="", max_length=100)
    # 拿请求的哪部分来比：全部消息拼接 / 最后一条用户消息 / system 消息 / 模型名
    field: str = Field(default="prompt", pattern="^(prompt|last_user|system|model)$")
    op: str = Field(default="contains_any", pattern="^(contains_any|equals|regex)$")
    value: list[str] = Field(default_factory=list)
    response_body: str = ""
    # 命中后连状态码一起改（比如"prompt 里带 xxx 就返回 429"），不填则沿用路由的
    status_code: int | None = Field(default=None, ge=100, le=599)


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
    stream_mode: str = Field(default="auto", pattern="^(auto|force_stream|force_json)$")
    match_enabled: bool = True
    # 不传则由 service 预置内置规则；显式传 [] 就是明确要一条规则都不要
    match_rules: list[MatchRule] | None = None
    response_type: str = Field(default="text")
    tool_calls: list | None = None


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
    stream_mode: str | None = Field(default=None, pattern="^(auto|force_stream|force_json)$")
    match_enabled: bool | None = None
    match_rules: list[MatchRule] | None = None
    response_type: str | None = None
    tool_calls: list | None = None


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
    stream_mode: str
    match_enabled: bool
    match_rules: list[MatchRule]
    response_type: str
    tool_calls: list | None
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
