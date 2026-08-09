import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class MockRoute(Base):
    __tablename__ = "mock_routes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False, default="POST")
    path: Mapped[str] = mapped_column(String(500), nullable=False, default="/v1/chat/completions")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 基础配置
    delay_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    response_format: Mapped[str] = mapped_column(String(10), nullable=False, default="json")

    # 响应模式
    preset_mode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    response_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="default")

    # finish_reason
    finish_reason: Mapped[str] = mapped_column(String(30), nullable=False, default="stop")

    # 响应体
    response_body: Mapped[str] = mapped_column(Text, nullable=False, default="This is a mock response from the LLM Mock service.")

    # Token 配置
    token_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="auto")
    custom_prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    custom_completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 模型配置
    model_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="follow_request")
    custom_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # 响应头
    response_headers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # SSE 配置
    sse_chunk_delay_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    # Tool Calls 配置
    response_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # 统计
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MockRequestLog(Base):
    __tablename__ = "mock_request_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    route_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 请求信息
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    request_headers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    request_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    caller: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # 响应信息
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_headers_out: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 解析字段
    request_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    response_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finish_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # 耗时
    match_ms: Mapped[float] = mapped_column(nullable=False, default=0.0)
    first_byte_ms: Mapped[float] = mapped_column(nullable=False, default=0.0)
    body_ms: Mapped[float] = mapped_column(nullable=False, default=0.0)
    total_ms: Mapped[float] = mapped_column(nullable=False, default=0.0)
