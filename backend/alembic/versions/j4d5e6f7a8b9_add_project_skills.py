"""add skills / skill_versions (项目 Skill 写入通道)

Revision ID: j4d5e6f7a8b9
Revises: i3c4d5e6f7a8
Create Date: 2026-07-31

项目侧 Claude Code 的 skill 推上平台后存这里，与文件系统里的内置 lum-* 分开
（前者客户端执行，后者平台侧执行）。visibility=public 时其它项目可取用。
skill_versions 存覆盖前快照 —— 写入通道开放，覆盖必须可回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'j4d5e6f7a8b9'
down_revision: Union[str, None] = 'i3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'skills',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False, server_default='client'),
        sa.Column('visibility', sa.String(16), nullable=False, server_default='public'),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('files', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default='{}'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('source', sa.String(16), nullable=False, server_default='mcp'),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.UniqueConstraint('project_id', 'name', name='uq_skill_project_name'),
    )
    op.create_index('ix_skills_project_id', 'skills', ['project_id'])
    # 跨项目取用列表按 visibility 过滤，单独建索引
    op.create_index('ix_skills_visibility', 'skills', ['visibility'])

    op.create_table(
        'skill_versions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('skill_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('files', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default='{}'),
        sa.Column('note', sa.String(200), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('skill_id', 'version', name='uq_skill_version'),
    )
    op.create_index('ix_skill_versions_skill_id', 'skill_versions', ['skill_id'])


def downgrade() -> None:
    op.drop_index('ix_skill_versions_skill_id', table_name='skill_versions')
    op.drop_table('skill_versions')
    op.drop_index('ix_skills_visibility', table_name='skills')
    op.drop_index('ix_skills_project_id', table_name='skills')
    op.drop_table('skills')
