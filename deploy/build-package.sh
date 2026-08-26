#!/bin/bash
# ===========================================
# Lumiere 离线安装包构建脚本
# 用法: bash deploy/build-package.sh
# 输出: lumiere-v1.0.0.tar.gz
# ===========================================
set -e

VERSION="1.0.0"
PACKAGE_NAME="lumiere-v${VERSION}"
BUILD_DIR="/tmp/${PACKAGE_NAME}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== 构建 Lumiere v${VERSION} 离线安装包 ==="

# 清理
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"/{backend,frontend-dist,deploy}

# 1. 构建前端
echo "[1/4] 构建前端..."
cd "$PROJECT_DIR/frontend"
npm ci --registry=https://registry.npmmirror.com 2>/dev/null || npm ci
npm run build
cp -r dist/* "$BUILD_DIR/frontend-dist/"

# 2. 打包后端
echo "[2/4] 打包后端..."
cd "$PROJECT_DIR"
cp -r backend "$BUILD_DIR/"
cp -r tests "$BUILD_DIR/"
cp -r docs "$BUILD_DIR/"
cp pyproject.toml "$BUILD_DIR/"

# 3. 导出 Python 依赖
echo "[3/4] 导出 Python 依赖..."
pip download -d "$BUILD_DIR/packages" -r <(cd backend && pip freeze 2>/dev/null | grep -v '^-e') 2>/dev/null || echo "依赖导出跳过（在线安装）"

# 4. 复制部署文件
echo "[4/4] 打包部署文件..."
cp "$PROJECT_DIR/deploy/nginx.conf" "$BUILD_DIR/deploy/"
cp "$PROJECT_DIR/docker-compose.yml" "$BUILD_DIR/" 2>/dev/null || true
cp "$PROJECT_DIR/Dockerfile" "$BUILD_DIR/" 2>/dev/null || true
cp "$PROJECT_DIR/.env.example" "$BUILD_DIR/" 2>/dev/null || true

# 写入安装脚本
cat > "$BUILD_DIR/install.sh" << 'INSTALL_EOF'
#!/bin/bash
set -e

echo "========================================"
echo "  Lumiere 测试管理平台 — 安装向导"
echo "========================================"

INSTALL_DIR="${INSTALL_DIR:-/opt/lumiere}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "安装目录: $INSTALL_DIR"
echo ""

# 检测系统
if ! command -v python3 &>/dev/null; then
    echo "错误: 需要 Python 3.11+"
    exit 1
fi

if ! command -v nginx &>/dev/null; then
    echo "警告: 未安装 nginx，前端将无法通过 80 端口访问"
    echo "  安装: apt install nginx  或  yum install nginx"
fi

if ! command -v psql &>/dev/null; then
    echo "警告: 未检测到 PostgreSQL 客户端"
    echo "  确保 PostgreSQL 服务已运行并可连接"
fi

# 创建安装目录
mkdir -p "$INSTALL_DIR"
cp -r "$SCRIPT_DIR/backend" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/tests" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/docs" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/pyproject.toml" "$INSTALL_DIR/"

# 前端文件
FRONTEND_DIR="${FRONTEND_DIR:-/usr/share/nginx/lumiere}"
mkdir -p "$FRONTEND_DIR"
cp -r "$SCRIPT_DIR/frontend-dist/"* "$FRONTEND_DIR/"

# 安装 Python 依赖
echo "安装 Python 依赖..."
cd "$INSTALL_DIR"
if [ -d "$SCRIPT_DIR/packages" ] && [ "$(ls -A "$SCRIPT_DIR/packages" 2>/dev/null)" ]; then
    pip install --no-index --find-links="$SCRIPT_DIR/packages" -e backend/ 2>/dev/null || pip install -e "backend/.[dev]"
else
    pip install -e "backend/.[dev]"
fi

# 配置文件
if [ ! -f "$INSTALL_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$INSTALL_DIR/.env" 2>/dev/null || true
    echo ""
    echo "请编辑配置文件: $INSTALL_DIR/.env"
fi

# nginx 配置
NGINX_CONF="/etc/nginx/conf.d/lumiere.conf"
if command -v nginx &>/dev/null && [ ! -f "$NGINX_CONF" ]; then
    cat > "$NGINX_CONF" << NGINX_EOF
server {
    listen 80;
    server_name _;

    location / {
        root $FRONTEND_DIR;
        index index.html;
        try_files \$uri \$uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8756;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
    }
}
NGINX_EOF
    echo "nginx 配置已写入: $NGINX_CONF"
fi

# 创建 systemd 服务
SERVICE_FILE="/etc/systemd/system/lumiere.service"
if [ ! -f "$SERVICE_FILE" ]; then
    cat > "$SERVICE_FILE" << SERVICE_EOF
[Unit]
Description=Lumiere API Server
After=network.target postgresql.service

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$(which uvicorn) backend.app.main:app --host 0.0.0.0 --port 8756 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE_EOF
    echo "systemd 服务已创建: $SERVICE_FILE"
fi

echo ""
echo "========================================"
echo "  安装完成！"
echo "========================================"
echo ""
echo "启动步骤:"
echo "  1. 编辑配置:  vim $INSTALL_DIR/.env"
echo "  2. 初始化数据库: cd $INSTALL_DIR/backend && alembic upgrade head"
echo "  3. 启动后端:  systemctl start lumiere"
echo "  4. 启动前端:  systemctl reload nginx"
echo "  5. 访问:      http://$(hostname -I | awk '{print $1}')"
echo ""
echo "默认管理员: admin / admin123"
echo ""
INSTALL_EOF

chmod +x "$BUILD_DIR/install.sh"

# 打包
echo "打包..."
cd /tmp
tar -czf "${PROJECT_DIR}/${PACKAGE_NAME}.tar.gz" "$PACKAGE_NAME"
rm -rf "$BUILD_DIR"

SIZE=$(du -h "${PROJECT_DIR}/${PACKAGE_NAME}.tar.gz" | cut -f1)
echo ""
echo "=== 构建完成 ==="
echo "安装包: ${PROJECT_DIR}/${PACKAGE_NAME}.tar.gz ($SIZE)"
echo ""
echo "部署方式 1 — Docker:"
echo "  docker compose up -d"
echo ""
echo "部署方式 2 — 离线安装:"
echo "  tar xzf ${PACKAGE_NAME}.tar.gz"
echo "  cd ${PACKAGE_NAME}"
echo "  sudo bash install.sh"
