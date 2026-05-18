#!/bin/bash
# 快速部署脚本 - 将本地修改同步到服务器并重启服务
# 用法: ./deploy.sh

SERVER="root@115.120.251.185"
REMOTE_DIR="~/career-ai"
PASS="ZYGzyg187"
SCP="sshpass -p $PASS scp -o StrictHostKeyChecking=no"
SSH="sshpass -p $PASS ssh -o StrictHostKeyChecking=no $SERVER"

set -e

echo "=== 构建前端 ==="
cd career-frontend && npm run build && cd ..

echo "=== 上传后端修改 ==="
$SCP career-agent/api.py                          $SERVER:$REMOTE_DIR/career-agent/api.py
$SCP career-agent/agent/agent_config.py           $SERVER:$REMOTE_DIR/career-agent/agent/agent_config.py
$SCP career-agent/agent/tools/career.py           $SERVER:$REMOTE_DIR/career-agent/agent/tools/career.py
$SCP career-agent/agent/tools/career_plan.py      $SERVER:$REMOTE_DIR/career-agent/agent/tools/career_plan.py
$SCP career-agent/pyproject.toml                  $SERVER:$REMOTE_DIR/career-agent/pyproject.toml
$SCP career-agent/uv.lock                         $SERVER:$REMOTE_DIR/career-agent/uv.lock

echo "=== 上传测试脚本 ==="
$SSH "mkdir -p $REMOTE_DIR/career-agent/tests"
$SCP career-agent/tests/test_career_path.py       $SERVER:$REMOTE_DIR/career-agent/tests/test_career_path.py

echo "=== 上传前端 dist ==="
# 先清空远端 dist，避免老文件残留（旧构建 hash 文件会继续被 nginx 引用）
$SSH "rm -rf $REMOTE_DIR/career-frontend/dist"
sshpass -p $PASS scp -o StrictHostKeyChecking=no -r career-frontend/dist $SERVER:$REMOTE_DIR/career-frontend/dist

echo "=== 重新构建并重启服务 ==="
# 前端 Dockerfile 用 COPY 把 dist 烘进镜像，必须 --build 才会刷新
$SSH "cd $REMOTE_DIR && docker compose up -d --build backend frontend"

echo "=== 等待服务启动 ==="
sleep 10

echo "=== 检查服务状态 ==="
$SSH "cd $REMOTE_DIR && docker compose ps"

echo "=== 查看后端日志（最近20行）==="
$SSH "docker logs career-ai-backend-1 2>&1 | tail -20"

echo "=== 部署完成 ==="
