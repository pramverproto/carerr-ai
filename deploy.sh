#!/bin/bash
# 快速部署脚本 - 将本地修改同步到服务器并重启服务
# 用法: ./deploy.sh

SERVER="root@115.120.251.185"
REMOTE_DIR="~/career-ai"

echo "=== 上传修改的文件到服务器 ==="

scp career-agent/api.py                     $SERVER:$REMOTE_DIR/career-agent/api.py
scp career-agent/agent/agent.py             $SERVER:$REMOTE_DIR/career-agent/agent/agent.py
scp career-agent/agent/agent_config.py      $SERVER:$REMOTE_DIR/career-agent/agent/agent_config.py
scp career-agent/agent/memory/db.py         $SERVER:$REMOTE_DIR/career-agent/agent/memory/db.py

echo "=== 上传前端修改 ==="
scp career-frontend/src/pages/Chat.tsx      $SERVER:$REMOTE_DIR/career-frontend/src/pages/Chat.tsx

echo "=== 重新构建并重启服务 ==="
ssh $SERVER "cd $REMOTE_DIR && docker compose up -d --build backend frontend"

echo "=== 查看后端日志（最近50行）==="
ssh $SERVER "cd $REMOTE_DIR && docker compose logs backend --tail=50"

echo "=== 部署完成 ==="
