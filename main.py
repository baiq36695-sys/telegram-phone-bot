#!/usr/bin/env python3
"""
国际版 Telegram电话号码重复检测机器人 - 支持多国格式
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
phone_data = defaultdict(lambda: {'count': 0, 'users': set(), 'first_seen': None})
# Flask健康检查
app = Flask(__name__)
@app.route('/')
def health():
    return {'status': 'running', 'bot': 'active', 'time': datetime.now().isoformat()}
@app.route('/stats')
def stats():
    return {
        'total_numbers': len(phone_data),
        'total_reports': sum(data['count'] for data in phone_data.values()),
        'duplicates': sum(1 for data in phone_data.values() if data['count'] > 1)
    }
def extract_phone_numbers(text: str) -> Set[str]:
    """提取电话号码 - 支持多国格式"""
    patterns = [
        # 国际格式（带国家代码）
        r'\+\d{1,4}\s*\d{6,14}',              # +60 11-2896 2309, +86 13812345678
        r'\+\d{1,4}[-\s]*\d{2,4}[-\s]*\d{6,10}',  # +60 11-2896 2309
        
        # 中国大陆
        r'1[3-9]\d{9}',                        # 13812345678
        r'\+86\s*1[3-9]\d{9}',                 # +86 13812345678
        
        # 美国/加拿大
        r'\+1\s*\d{3}\s*\d{3}\s*\d{4}',       # +1 555 123 4567
        r'\(\d{3}\)\s*\d{3}-\d{4}',           # (555) 123-4567
        
        # 英国
        r'\+44\s*\d{2,4}\s*\d{6,8}',          # +44 20 7946 0958
        
        # 澳大利亚
        r'\+61\s*\d{1}\s*\d{4}\s*\d{4}',      # +61 4 1234 5678
        
        # 马来西亚（你测试的格式）
        r'\+60\s*\d{1,2}\s*\d{7,8}',          # +60 19 6301799
        
        # 通用格式
        r'\d{3}-\d{3,4}-\d{4}',               # 123-456-7890
        r'\d{3}\s\d{3,4}\s\d{4}',             # 123 456 7890
        r'\d{10,15}',                         # 10-15位纯数字
    ]
    
    phone_numbers = set()
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            # 清理号码（移除空格、破折号、括号）
            clean_number = re.sub(r'[\s\-\(\)]', '', match)
            
            # 过滤有效长度的号码
            if len(clean_number) >= 8:  # 最少8位
                phone_numbers.add(clean_number)
    
    return phone_numbers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    message = """
🤖 **电话号码重复检测机器人已启动！**
🌍 **支持格式**：
• 中国：13812345678, +86 13812345678
• 马来西亚：+60 11-2896 2309
• 美国：+1 555 123 4567
• 英国：+44 20 7946 0958
• 澳大利亚：+61 4 1234 5678
• 其他国际格式
⚡ **功能**：
• 自动检测消息中的电话号码
• 标记重复出现的号码
• 发送警告提醒
• 支持多种国际格式
现在可以在群组中使用了！发送任何包含电话号码的消息来测试。
    """
    await update.message.reply_text(message, parse_mode='Markdown')
async def check_duplicates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """检查重复电话号码"""
    if not update.message or not update.message.text:
        return
    
    text = update.message.text
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "未知用户"
    
    # 提取电话号码
    phones = extract_phone_numbers(text)
    
    # 记录检测到的号码（用于调试）
    if phones:
        logger.info(f"检测到电话号码: {phones} (用户: {username})")
    
    for phone in phones:
        # 记录首次出现时间
        if phone_data[phone]['first_seen'] is None:
            phone_data[phone]['first_seen'] = datetime.now().isoformat()
        
        phone_data[phone]['count'] += 1
        phone_data[phone]['users'].add(user_id)
        
        # 如果是重复的电话号码，发送警告
        if phone_data[phone]['count'] > 1:
            # 隐藏部分号码以保护隐私
            if len(phone) > 8:
                masked = phone[:3] + "*" * (len(phone) - 6) + phone[-3:]
            else:
                masked = phone[:2] + "*" * (len(phone) - 4) + phone[-2:]
            
            warning = f"""
⚠️ **检测到重复电话号码！**
📞 号码：`{masked}`
🔢 出现次数：**{phone_data[phone]['count']}**
👥 涉及用户：{len(phone_data[phone]['users'])} 人
📅 首次发现：{phone_data[phone]['first_seen'][:16]}
🚨 请注意可能的重复或垃圾信息！
            """
            await update.message.reply_text(warning, parse_mode='Markdown')
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示统计信息"""
    total_numbers = len(phone_data)
    total_reports = sum(data['count'] for data in phone_data.values())
    duplicates = sum(1 for data in phone_data.values() if data['count'] > 1)
    
    stats_message = f"""
📊 **统计信息**
📱 总电话号码：{total_numbers}
📈 总报告次数：{total_reports}
⚠️ 重复号码：{duplicates}
✅ 唯一号码：{total_numbers - duplicates}
🕒 最后更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    """
    await update.message.reply_text(stats_message, parse_mode='Markdown')
def run_flask():
    """在线程中运行 Flask"""
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
def run_bot():
    """运行机器人"""
    # 创建新的事件循环
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # 创建应用
        application = Application.builder().token(BOT_TOKEN).build()
        
        # 添加处理器
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_duplicates))
        
        logger.info("🤖 国际版电话号码重复检测机器人已启动！")
        logger.info("📱 支持多国电话号码格式检测")
        logger.info("机器人正在运行中...")
        
        # 使用当前循环运行
        loop.run_until_complete(application.run_polling(drop_pending_updates=True))
        
    except Exception as e:
        logger.error(f"机器人运行错误: {e}")
    finally:
        loop.close()
def main():
    """主函数"""
    logger.info("🚀 启动国际版电话检测服务...")
    
    # Flask 在后台线程
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask 健康检查服务已启动")
    
    # 机器人在主线程
    try:
        run_bot()
    except KeyboardInterrupt:
        logger.info("👋 服务已停止")
if __name__ == '__main__':
    main()
