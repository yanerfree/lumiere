import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


# ═══════════════════════════════════════════════
#  WebSocket Mock
# ═══════════════════════════════════════════════

class WsMockEndpoint(Base):
    __tablename__ = "ws_mock_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False, default="/ws")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    response_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="echo")
    fixed_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    support_binary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    delay_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WsMockLog(Base):
    __tablename__ = "ws_mock_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    endpoint_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    client_ip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    message_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    message_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    direction: Mapped[str | None] = mapped_column(String(3), nullable=True)


# ═══════════════════════════════════════════════
#  TCP Mock
# ═══════════════════════════════════════════════

class TcpMockHandler(Base):
    __tablename__ = "tcp_mock_handlers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    match_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="exact")
    match_pattern: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="echo")
    response_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_hex: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    delay_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TcpMockLog(Base):
    __tablename__ = "tcp_mock_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    handler_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    client_ip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    client_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_size: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ═══════════════════════════════════════════════
#  UDP Mock
# ═══════════════════════════════════════════════

class UdpMockHandler(Base):
    __tablename__ = "udp_mock_handlers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    match_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="exact")
    match_pattern: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="echo")
    response_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_hex: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    delay_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UdpMockLog(Base):
    __tablename__ = "udp_mock_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    handler_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    client_ip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    client_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    direction: Mapped[str | None] = mapped_column(String(3), nullable=True)
    data_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_size: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ═══════════════════════════════════════════════
#  gRPC Mock
# ═══════════════════════════════════════════════

class GrpcMockService(Base):
    __tablename__ = "grpc_mock_services"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    method_name: Mapped[str] = mapped_column(String(200), nullable=False)
    method_type: Mapped[str] = mapped_column(String(20), nullable=False, default="unary")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    request_sample: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    stream_items: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    delay_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GrpcMockLog(Base):
    __tablename__ = "grpc_mock_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    service_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    method_name: Mapped[str] = mapped_column(String(200), nullable=False)
    method_type: Mapped[str] = mapped_column(String(20), nullable=False)
    client_ip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
