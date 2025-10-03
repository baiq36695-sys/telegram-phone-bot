#!/usr/bin/env python3
"""
修复版 Telegram电话号码重复检测机器人 - Render版
解决 asyncio 事件循环冲突问题
"""
import json
import os
import re
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, Set
import threading
from flask import Flask
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# 机器人令牌
BOT_TOKEN = '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU'
# 数据存储
phone_data = defaultdict(lambda: {'count': 0, 'users': set()})
# Flask健康检查
app = Flask(__name__)
@app.route('/')
def health():
    return {'status': 'running', 'bot': 'active', 'time': datetime.now().isoformat()}
@app.route('/stats')
def stats():
    return {
        'total_numbers': len(phone_data),
        'total_reports': sum(data['count'] for data in phone_data.values())
    }
def extract_phone_numbers(text: str) -> Set[str]:
    """提取电话号码"""
    patterns = [
        r'1[3-9]\d{9}',                    # 中国手机号
        r'\+86\s*1[3-9]\d{9}',             # 带国际区号
        r'\d{3}-\d{4}-\d{4}',              # xxx-xxxx-xxxx
    ]
    
    phone_numbers = set()
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            clean_number = re.sub(r'[\s\-\(\)\+]', '', match)
            if len(clean_number) >= 11:
                phone_numbers.add(clean_number)
    return phone_numbers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    message = """
🤖 **电话号码重复检测机器人已启动！**
功能：
• 自动检测消息中的电话号码
• 标记重复出现的号码
• 发送警告提醒
现在可以在群组中使用了！
    """
    await update.message.reply_text(message)
async def check_duplicates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """检查重复电话号码"""
    if not update.message or not update.message.text:
        return
    
    text = update.message.text
    phones = extract_phone_numbers(text)
    
    for phone in phones:
        phone_data[phone]['count'] += 1
        
        if phone_data[phone]['count'] > 1:
            # 隐藏部分号码以保护隐私
            masked = phone[:3] + "*" * (len(phone) - 6) + phone[-3:]
            warning = f"""
⚠️ **检测到重复电话号码！**
号码：`{masked}`
出现次数：{phone_data[phone]['count']}
请注意可能的重复或垃圾信息！
            """
            await update.message.reply_text(warning, parse_mode='Markdown')
def run_flask():
    """在线程中运行 Flask"""
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
def run_bot():
    """运行机器人 - 修复版本"""
    # 创建新的事件循环
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # 创建应用
        application = Application.builder().token(BOT_TOKEN).build()
        
        # 添加处理器
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_duplicates))
        
        logger.info("🤖 电话号码重复检测机器人已启动！")
        logger.info("机器人正在运行中...")
        
        # 使用当前循环运行
        loop.run_until_complete(application.run_polling(drop_pending_updates=True))
        
    except Exception as e:
        logger.error(f"机器人运行错误: {e}")
    finally:
        loop.close()
def main():
    """主函数 - 修复版本"""
    logger.info("🚀 启动服务...")
    
    # 方法1：Flask 在后台线程
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask 健康检查服务已启动")
    
    # 方法2：机器人在主线程（避免事件循环冲突）
    try:
        run_bot()
    except KeyboardInterrupt:
        logger.info("👋 服务已停止")
if __name__ == '__main__':
    main()
