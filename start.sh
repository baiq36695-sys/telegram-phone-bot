#!/bin/bash
# Render.com 启动脚本

echo "🚀 启动Telegram机器人服务..."
echo "📅 时间: $(date)"
echo "🔧 Python版本: $(python --version)"

# 设置环境变量
export PYTHONUNBUFFERED=1

# 启动应用
python main.py