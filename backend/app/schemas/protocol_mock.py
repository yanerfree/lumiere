from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.common import BaseSchema


# ═══════════════════════════════════════════════
#  WebSocket Mock Schemas
# ═══════════════════════════════════════════════

class WsEndpointCreate(BaseSchema):
    name: str = Field(max_length=100)
    path: str = Field(default="/ws", max_length=500)
    response_mode: str = Field(default="echo")
    fixed_response: str | None = None
    custom_config: dict | None = None
    error_code: int | None = None
    error_reason: str | None = None
    support_binary: bool = False
    delay_ms: int = Field(default=0, ge=0)


class WsEndpointUpdate(BaseSchema):
    name: str | None = None
    path: str | None = None
    enabled: bool | None = None
    locked: bool | None = None
    response_mode: str | None = None
    fixed_response: str | None = Field(default=None)
    custom_config: dict | None = Field(default=None)
    error_code: int | None = Field(default=None)
    error_reason: str | None = Field(default=None)
    support_binary: bool | None = None
    delay_ms: int | None = None


class WsEndpointResponse(BaseSchema):
    id: uuid.UUID
    name: str
    path: str
    enabled: bool
    locked: bool
    sort_order: int
    response_mode: str
    fixed_response: str | None
    custom_config: dict | None
    error_code: int | None
    error_reason: str | None
    support_binary: bool
    delay_ms: int
    hit_count: int
    last_hit_at: datetime | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class WsLogResponse(BaseSchema):
    id: uuid.UUID
    endpoint_id: uuid.UUID | None
    timestamp: datetime
    event_type: str
    path: str
    client_ip: str | None
    message_type: str | None
    message_preview: str | None
    message_size: int | None
    direction: str | None
    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════
#  TCP Mock Schemas
# ═══════════════════════════════════════════════

class TcpHandlerCreate(BaseSchema):
    name: str = Field(max_length=100)
    match_mode: str = Field(default="exact")
    match_pattern: str = ""
    response_mode: str = Field(default="echo")
    response_data: str | None = None
    response_hex: bool = False
    delay_ms: int = Field(default=0, ge=0)


class TcpHandlerUpdate(BaseSchema):
    name: str | None = None
    enabled: bool | None = None
    locked: bool | None = None
    match_mode: str | None = None
    match_pattern: str | None = None
    response_mode: str | None = None
    response_data: str | None = Field(default=None)
    response_hex: bool | None = None
    delay_ms: int | None = None


class TcpHandlerResponse(BaseSchema):
    id: uuid.UUID
    name: str
    enabled: bool
    locked: bool
    sort_order: int
    match_mode: str
    match_pattern: str
    response_mode: str
    response_data: str | None
    response_hex: bool
    delay_ms: int
    hit_count: int
    last_hit_at: datetime | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class TcpLogResponse(BaseSchema):
    id: uuid.UUID
    handler_id: uuid.UUID | None
    timestamp: datetime
    event_type: str
    client_ip: str | None
    client_port: int | None
    data_preview: str | None
    data_size: int | None
    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════
#  UDP Mock Schemas
# ═══════════════════════════════════════════════

class UdpHandlerCreate(BaseSchema):
    name: str = Field(max_length=100)
    match_mode: str = Field(default="exact")
    match_pattern: str = ""
    response_mode: str = Field(default="echo")
    response_data: str | None = None
    response_hex: bool = False
    delay_ms: int = Field(default=0, ge=0)


class UdpHandlerUpdate(BaseSchema):
    name: str | None = None
    enabled: bool | None = None
    locked: bool | None = None
    match_mode: str | None = None
    match_pattern: str | None = None
    response_mode: str | None = None
    response_data: str | None = Field(default=None)
    response_hex: bool | None = None
    delay_ms: int | None = None


class UdpHandlerResponse(BaseSchema):
    id: uuid.UUID
    name: str
    enabled: bool
    locked: bool
    sort_order: int
    match_mode: str
    match_pattern: str
    response_mode: str
    response_data: str | None
    response_hex: bool
    delay_ms: int
    hit_count: int
    last_hit_at: datetime | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class UdpLogResponse(BaseSchema):
    id: uuid.UUID
    handler_id: uuid.UUID | None
    timestamp: datetime
    client_ip: str | None
    client_port: int | None
    direction: str | None
    data_preview: str | None
    data_size: int | None
    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════
#  gRPC Mock Schemas
# ═══════════════════════════════════════════════

class GrpcServiceCreate(BaseSchema):
    name: str = Field(max_length=200)
    service_name: str = Field(max_length=200)
    method_name: str = Field(max_length=200)
    method_type: str = Field(default="unary")
    request_sample: str | None = None
    response_body: str = "{}"
    stream_items: list | None = None
    delay_ms: int = Field(default=0, ge=0)
    status_code: int = Field(default=0, ge=0)
    status_message: str | None = None


class GrpcServiceUpdate(BaseSchema):
    name: str | None = None
    service_name: str | None = None
    method_name: str | None = None
    method_type: str | None = None
    enabled: bool | None = None
    locked: bool | None = None
    request_sample: str | None = Field(default=None)
    response_body: str | None = None
    stream_items: list | None = Field(default=None)
    delay_ms: int | None = None
    status_code: int | None = None
    status_message: str | None = Field(default=None)


class GrpcServiceResponse(BaseSchema):
    id: uuid.UUID
    name: str
    service_name: str
    method_name: str
    method_type: str
    enabled: bool
    locked: bool
    sort_order: int
    request_sample: str | None
    response_body: str
    stream_items: list | None
    delay_ms: int
    status_code: int
    status_message: str | None
    hit_count: int
    last_hit_at: datetime | None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class GrpcLogResponse(BaseSchema):
    id: uuid.UUID
    service_id: uuid.UUID | None
    timestamp: datetime
    service_name: str
    method_name: str
    method_type: str
    client_ip: str | None
    request_body: str | None
    response_body: str | None
    status_code: int
    duration_ms: float
    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════
#  Service Status (shared pattern)
# ═══════════════════════════════════════════════

class ProtocolServiceStatus(BaseSchema):
    running: bool
    port: int
    endpoints_count: int = 0
    endpoints_enabled: int = 0
    total_logs: int = 0
    reflection_version: str | None = None  # gRPC 专用：both | v1 | v1alpha


class ProtocolServiceConfig(BaseSchema):
    port: int | None = None
    reflection_version: str | None = None  # gRPC 专用


# ═══════════════════════════════════════════════
#  Reorder (shared by ws / tcp / udp / grpc)
# ═══════════════════════════════════════════════

class ProtocolReorderItem(BaseSchema):
    id: uuid.UUID
    sort_order: int


class ProtocolReorderRequest(BaseSchema):
    items: list[ProtocolReorderItem]
