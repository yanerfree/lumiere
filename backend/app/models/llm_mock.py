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
    # 每个分片几个字符。对接网关时分片数本身是被验证的指标，逐字符切会跟被对照的 mock 对不上
    sse_chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    # 流式模式 —— auto 跟随请求的 stream 字段；force_stream 不管请求怎么写都回事件流
    # （测网关 fail-closed：上游对 stream:false 耍赖返流）；force_json 反过来，请求要流也只给整包 JSON
    stream_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="auto")

    # 条件应答 —— 按请求内容分流：命中规则就返回规则自己的响应，都不命中才用下面的 response_body。
    # 总开关关掉则整张规则表不参与匹配（调试时想看默认响应，不必一条条禁用）。
    match_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # [{id, enabled, name, field, op, value, response_body, status_code}]
    # 内置那条「测试用例关键词 → 用例 JSON」也躺在这里，跟自建规则完全平权，可改可删。
    match_rules: Mapped[list | None] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    # 智能应答 —— 行为由请求里的指令决定（SAY: / MODE:xxx），而不是这张表上的静态配置。
    # 开着的时候接管整条链路：条件应答规则、响应内容、状态码、finish_reason、流式这些全部旁路。
    # 为什么单独一个模式而不是再加几条规则：规则的响应体是静态串，而护栏回显（要报本次
    # 待检正文的长度/开头）、MODE:LOOP（要跨轮判断）、MODE:SLOW（按分片计时）这三样
    # 的响应内容依赖请求内容，规则表达不了。
    smart_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # auto（按路径判）/ upstream（被测智能体的上游大模型）/ checker（网关护栏调用的检查模型）
    smart_role: Mapped[str] = mapped_column(String(20), nullable=False, default="auto", server_default="auto")
    # 护栏提示模板里「待检正文」的定位标记，空则用内置默认
    smart_body_marker: Mapped[str | None] = mapped_column(String(200), nullable=True)

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

    # 智能应答判定：这次请求被解析成了什么指令、走的哪种协议形状、stream 实际是什么、
    # 护栏拿到的正文多长、判决是什么。「网关到底把什么喂给了护栏」这个证据就在这里。
    smart_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 耗时
    match_ms: Mapped[float] = mapped_column(nullable=False, default=0.0)
    first_byte_ms: Mapped[float] = mapped_column(nullable=False, default=0.0)
    body_ms: Mapped[float] = mapped_column(nullable=False, default=0.0)
    total_ms: Mapped[float] = mapped_column(nullable=False, default=0.0)
