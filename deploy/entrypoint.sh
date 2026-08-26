#!/bin/bash
set -e

echo "=== Lumiere 启动中 ==="

# 等待 PostgreSQL 可用
echo "等待数据库连接..."
for i in $(seq 1 30); do
    python3 -c "
import asyncio, asyncpg, os
async def check():
    url = os.environ.get('DATABASE_URL', '').replace('+asyncpg', '').replace('postgresql+asyncpg', 'postgresql')
    if not url: url = 'postgresql://postgres:postgres@db:5432/lumiere'
    conn = await asyncpg.connect(url.replace('postgresql+asyncpg://', 'postgresql://'))
    await conn.close()
asyncio.run(check())
" 2>/dev/null && break
    echo "  等待中... ($i/30)"
    sleep 2
done

# 执行数据库迁移
echo "执行数据库迁移..."
cd /app/backend && alembic upgrade head 2>/dev/null || echo "迁移跳过（表可能已存在）"

# 启动 nginx（前端）
echo "启动前端服务..."
nginx

# 启动后端
echo "启动后端服务..."
cd /app && exec uvicorn backend.app.main:app \
    --host 0.0.0.0 \
    --port 8756 \
    --workers ${WORKERS:-2} \
    --log-level info
