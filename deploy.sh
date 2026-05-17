#!/bin/bash
set -e

REPO_URL="git@codeup.aliyun.com:6a0554f2fa2a62bc8595f848/career-ai.git"
APP_DIR="/root/career-ai"
BRANCH="master"

echo "=============================="
echo " CareerAI 部署脚本"
echo "=============================="

# 拉取最新代码
if [ -d "$APP_DIR/.git" ]; then
  echo "[1/4] 拉取最新代码..."
  cd $APP_DIR
  git pull origin $BRANCH
else
  echo "[1/4] 首次克隆代码..."
  git clone -b $BRANCH $REPO_URL $APP_DIR
  cd $APP_DIR
fi

# 检查 .env 文件
echo "[2/4] 检查配置文件..."
if [ ! -f "career-agent/.env" ]; then
  echo "❌ 缺少 career-agent/.env，请先创建！"
  exit 1
fi
if [ ! -f "career-db-service/.env" ]; then
  echo "❌ 缺少 career-db-service/.env，请先创建！"
  exit 1
fi

# 构建并启动
echo "[3/4] 构建镜像并启动服务..."
docker compose down --remove-orphans
docker compose up -d --build

# 健康检查
echo "[4/4] 等待服务启动..."
for i in $(seq 1 30); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    echo ""
    echo "✅ 部署成功！服务已启动"
    docker compose ps
    exit 0
  fi
  echo -n "."
  sleep 2
done

echo ""
echo "❌ 服务启动失败，查看日志："
docker compose logs --tail=50 backend
exit 1
