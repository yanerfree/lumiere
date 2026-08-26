# Lumiere 部署指南

## 目录

- [环境要求](#环境要求)
- [配置说明（必读）](#配置说明必读)
- [方式一：Docker 部署（推荐）](#方式一docker-部署推荐)
- [方式二：离线安装包部署](#方式二离线安装包部署)
- [初始化](#初始化)
- [常用运维命令](#常用运维命令)
- [升级](#升级)

---

## 环境要求

### Docker 部署

| 依赖 | 最低版本 |
|------|---------|
| Docker | 20.10+ |
| Docker Compose | V2+ |
| 内存 | 2GB+ |
| 磁盘 | 10GB+ |

### 离线安装包部署

| 依赖 | 最低版本 | 说明 |
|------|---------|------|
| Python | 3.11+ | 推荐 3.13 |
| PostgreSQL | 14+ | 需提前安装并创建数据库 |
| Redis | 6+ | 可选（用于任务状态缓存） |
| Nginx | 1.18+ | 前端静态文件托管 |
| Git | 2.30+ | 测试脚本同步需要 |
| 内存 | 2GB+ | |
| 磁盘 | 10GB+ | |

---

## 配置说明（必读）

### 必须修改的配置

部署前**必须**修改以下配置，否则存在安全风险：

| 配置项 | 说明 | 默认值 | 修改建议 |
|--------|------|--------|---------|
| `SECRET_KEY` | JWT 签名密钥 | `change-me-in-production` | 改为 32+ 位随机字符串 |
| `POSTGRES_PASSWORD` | 数据库密码 | `postgres` | 改为强密码 |

**生成随机密钥：**
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 可选配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `PORT` | 对外访问端口 | `80` |
| `JWT_EXPIRE_HOURS` | JWT 过期时间（小时） | `8` |
| `WORKERS` | 后端 Worker 进程数 | `2` |
| `DATABASE_URL` | 数据库连接串 | `postgresql+asyncpg://postgres:postgres@db:5432/lumiere` |
| `REDIS_URL` | Redis 连接串 | `redis://redis:6379/0` |

---

## 方式一：Docker 部署（推荐）

### 1. 获取代码

```bash
git clone https://github.com/yanerfree/lumiere.git
cd lumiere
```

### 2. 修改配置

```bash
cp .env.example .env
vim .env
```

`.env` 文件内容：

```ini
# 【必须修改】数据库密码
POSTGRES_PASSWORD=你的强密码

# 【必须修改】JWT 密钥（用上面的命令生成）
SECRET_KEY=你的随机密钥

# JWT 过期时间（小时）
JWT_EXPIRE_HOURS=8

# 对外端口（默认 80）
PORT=80

# 后端 Worker 数量（建议 CPU 核数 * 2）
WORKERS=2
```

### 3. 启动

```bash
docker compose up -d
```

首次启动会自动：
- 拉取 PostgreSQL、Redis 镜像
- 构建应用镜像（前端 + 后端 + Nginx）
- 创建数据库表（Alembic 迁移）
- 初始化默认管理员账号

### 4. 访问

```
http://服务器IP:端口
默认管理员：admin / admin123
```

**首次登录后请立即修改管理员密码。**

### 5. 查看日志

```bash
docker compose logs -f app    # 应用日志
docker compose logs -f db     # 数据库日志
```

---

## 方式二：离线安装包部署

适用于无 Docker 环境的 Linux 服务器。

### 1. 构建安装包（在开发机上执行）

```bash
bash deploy/build-package.sh
```

生成 `lumiere-v1.0.0.tar.gz`。

### 2. 上传到目标服务器

```bash
scp lumiere-v1.0.0.tar.gz user@server:/tmp/
```

### 3. 安装 PostgreSQL（如未安装）

**Ubuntu/Debian：**
```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib nginx redis-server git python3 python3-pip python3-venv
sudo systemctl start postgresql redis-server nginx
```

**CentOS/RHEL：**
```bash
sudo yum install -y postgresql-server postgresql-contrib nginx redis git python3 python3-pip
sudo postgresql-setup initdb
sudo systemctl start postgresql redis nginx
```

### 4. 创建数据库

```bash
sudo -u postgres psql << EOF
CREATE DATABASE lumiere;
ALTER USER postgres PASSWORD '你的数据库密码';
EOF
```

### 5. 解压并安装

```bash
cd /tmp
tar xzf lumiere-v1.0.0.tar.gz
cd lumiere-v1.0.0
```

修改配置：
```bash
cp .env.example .env
vim .env
```

`.env` 文件内容：

```ini
# 【必须修改】数据库连接（修改密码部分）
DATABASE_URL=postgresql+asyncpg://postgres:你的数据库密码@localhost:5432/lumiere

# 【必须修改】Redis
REDIS_URL=redis://localhost:6379/0

# 【必须修改】JWT 密钥
SECRET_KEY=你的随机密钥

# JWT 过期时间
JWT_EXPIRE_HOURS=8
```

执行安装：
```bash
sudo bash install.sh
```

### 6. 初始化数据库

```bash
cd /opt/lumiere/backend
alembic upgrade head
```

### 7. 启动服务

```bash
sudo systemctl daemon-reload
sudo systemctl enable lumiere    # 开机自启
sudo systemctl start lumiere     # 启动后端
sudo systemctl reload nginx        # 启动前端
```

### 8. 访问

```
http://服务器IP
默认管理员：admin / admin123
```

**首次登录后请立即修改管理员密码。**

---

## 初始化

首次部署后需要完成以下设置：

### 1. 修改管理员密码

登录后进入「用户管理」，修改 admin 用户密码。

### 2. 创建测试环境

进入「环境配置」，创建你的测试环境并配置变量：

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `BASE_URL` | 被测服务地址 | `http://10.0.1.100:8000` |
| `ADMIN_USERNAME` | 管理员账号 | `admin` |
| `ADMIN_PASSWORD` | 管理员密码 | `your_password` |
| `DATABASE_URL` | 测试数据库连接 | `postgresql+asyncpg://...` |

### 3. 创建项目

进入「项目列表」，创建项目并配置：
- **Git 仓库地址**：测试脚本所在的 Git 仓库 URL
- **脚本基础路径**：服务器上存放脚本的目录（如 `/home/tester/scripts`）

### 4. 同步用例

进入项目 → 用例管理 → 点击「同步用例」。

---

## 常用运维命令

### Docker 部署

```bash
# 查看状态
docker compose ps

# 重启
docker compose restart app

# 停止
docker compose down

# 查看日志
docker compose logs -f app

# 备份数据库
docker compose exec db pg_dump -U postgres lumiere > backup.sql

# 恢复数据库
cat backup.sql | docker compose exec -T db psql -U postgres lumiere
```

### 离线部署

```bash
# 查看状态
systemctl status lumiere

# 重启
systemctl restart lumiere

# 查看日志
journalctl -u lumiere -f

# 备份数据库
pg_dump -U postgres lumiere > backup.sql
```

---

## 升级

### Docker 升级

```bash
git pull
docker compose build app
docker compose up -d
```

### 离线包升级

1. 在开发机构建新版安装包
2. 上传到服务器
3. 解压后执行：
```bash
sudo bash install.sh
cd /opt/lumiere/backend && alembic upgrade head
sudo systemctl restart lumiere
```

---

## 技术支持

- 仓库地址：https://github.com/yanerfree/lumiere
- 问题反馈：https://github.com/yanerfree/lumiere/issues
