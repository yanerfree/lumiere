"""MCP Mock —— 多服务版的三张表。

原来这块是「一个 MCP 服务 + 一份 JSON 文件」，所以只能测一种接入方式。
改成多服务之后，**一个端口（28300）上按路径挂 N 个 MCP 服务**：
`http://<host>:28300/<slug>/mcp`。

⚠ 为什么不是一个服务一个端口：28xxx 那一段 9 个端口（28100~28900）在
`app/main.py` 的 lifespan 里已经全部绑掉了。再要端口就得往外扩段，而扩段是
部署那边的事（防火墙、容器端口映射），改一次牵一串。路径前缀不占新端口，
加服务不需要动任何部署配置。

⚠ `builtin` 和 `locked` 是两件事，照抄 LLM Mock 那套口径（app/api/llm_mock.py）：
  · `builtin=True` —— 平台随版本发的预置服务，**一律不许删，解锁也不行**。
    删了的效果只是「重启前它不在、重启后又回来了」，看着像平台自己乱加东西。
    不想用就停用（enabled=False），那是个能留住的状态。
  · `locked=True` —— 用户自己上的锁，锁着时不能改也不能删，解锁就恢复。
    预置服务默认锁上，是怕人误改了以为平台坏了；解锁之后照样能改。
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class McpMockServer(Base):
    """一个 MCP Mock 服务 = 一个挂载点 + 一套认证 + 一组工具。"""

    __tablename__ = "mcp_mock_servers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 路径代号，落在 URL 里：http://host:28300/<slug>/mcp
    # 唯一 —— 两个服务同一个 slug 的话，后挂的那个会把前一个整个盖掉，而且不报错。
    slug: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # MCP server instructions，客户端 initialize 时能读到
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # streamable-http | sse。**stdio 不做** —— 那是本地进程管道，
    # 不经过网络，平台这边起不了也给不出地址，页面上标成「暂不支持」。
    transport: Mapped[str] = mapped_column(String(20), nullable=False, default="streamable-http")

    # none | bearer | apikey
    auth_type: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    # bearer: {"token": "..."}；apikey: {"headerName": "X-API-Key", "apiKey": "..."}
    auth_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # strict | loose | off —— 入参校验的松紧，见 mcp_mock_validate.py
    validate_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="strict")

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 预置内容的版本号（见 services/mcp_mock_presets.py 的 PRESET_REV）。
    # 只对 builtin 服务有意义：版本号对不上 = 平台改过预置内容，启动时整套刷新。
    # **但只刷还锁着的** —— 人解锁接管之后只记版本号、不动内容，理由见那边的注释。
    preset_rev: Mapped[str | None] = mapped_column(String(32), nullable=True)

    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class McpMockTool(Base):
    """服务下的一个工具。参数是**有类型有校验**的，不再是清一色 str。"""

    __tablename__ = "mcp_mock_tools"
    __table_args__ = (UniqueConstraint("server_id", "name", name="uq_mcp_mock_tool_server_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    server_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # 参数定义，**列表**不是字典 —— 顺序在页面上和 tools/list 里都要保持稳定，
    # 而老的 {name: type} 字典靠 Python 的插入序撑着，一次 json.loads 之后就不保证了。
    # 每项：{name, type, required, description, default, enum, minimum, maximum,
    #        minLength, maxLength, itemsType}
    params: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # success | error | custom
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    success_data: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    custom_data: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    custom_is_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="Mock error: tool call failed")

    # 人为延迟，用来测客户端超时
    delay_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class McpMockLog(Base):
    """一次工具调用。按服务分，页面上每个服务看自己的。"""

    __tablename__ = "mcp_mock_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    server_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)

    # mock-server（真从 MCP 客户端打进来的）| call（页面上点「调用测试」）
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="mock-server")
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    is_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 这次调用是被什么挡下来的 —— 没挡就是 None。
    # 认证失败和参数校验失败必须分得开：页面上都是一条红日志，
    # 但一个要去改 token、一个要去改参数，混在一起人只能靠猜。
    reject_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)  # auth | validate
    reject_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    elapsed_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
