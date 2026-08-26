"""项目 Skill —— 从项目侧推上平台、可被其它项目取用的 Claude Code 工作流。

与 `app/skills/preset/` 下的内置 `lum-*` 是两类东西，边界是**谁执行**：

- 内置 `lum-*`（文件系统）= **平台侧执行**。skill_executor 读 SKILL.md 当 prompt
  喂给后端 LLM，每个都在「AI 能力→模型」里绑一个模型档位。
- 本表（DB）= **客户端侧执行**。跑在开发者机器的 Claude Code 里，用 Bash/Edit/
  Playwright 这些本地工具，平台只做存取，永不执行。

所以本表**不接入 skill_executor 的加载路径** —— 一个引用 Bash/Edit 的 skill 被
当 prompt 塞给后端 LLM 只会白烧 token，那些工具在后端根本不存在。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class Skill(Base):
    """一个 skill 目录的内容。

    content: SKILL.md 全文（含 frontmatter）。
    files:   附属文件，{相对路径: 文本内容}，如 {"references/api.md": "..."}。
             只存文本 —— skill 里放二进制没有意义，且会把这张表撑坏。
    visibility: public = 全平台任意项目可取用（默认，共享本来就是上传的目的）；
                project = 仅来源项目可见。
    """
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_skill_project_name"),
    )
    # created_at/updated_at 是 server_default，不 eager 取回的话 flush 后它们仍未加载，
    # 同步代码里读一下就炸 MissingGreenlet。PG 支持 RETURNING，白拿。
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="client")
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, server_default="public")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    files: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # 从哪个通道进来的：mcp（外部 Claude Code 推）/ upload（打包上传）/ ui（页面里新建）
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="mcp")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SkillVersion(Base):
    """每次覆盖写入前的快照 —— 写入通道是开放的，覆盖必须可回滚。"""
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version", name="uq_skill_version"),
    )
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    files: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
