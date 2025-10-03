#!/usr/bin/env python3
"""
Telegram电话号码重复检测机器人 - Render.com版本
24/7云端运行版本
"""

import asyncio
import json
import os
import re
import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, Set
import threading
from flask import Flask

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# 机器人令牌 - 从环境变量读取（更安全）
BOT_TOKEN = os.getenv('BOT_TOKEN', '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU')

# 数据存储文件
DATA_FILE = 'phone_numbers_data.json'

# 用于存储电话号码的字典
phone_data = defaultdict(lambda: {'count': 0, 'users': set(), 'first_seen': None})

# Flask应用 - 用于健康检查
app = Flask(__name__)

@app.route('/')
def health_check():
    """健康检查端点"""
    return {
        'status': 'running',
        'bot': 'Telegram Phone Duplicate Detector',
        'timestamp': datetime.now().isoformat(),
        'total_numbers': len(phone_data)
    }

@app.route('/stats')
def get_stats():
    """获取统计信息"""
    return {
        'total_numbers': len(phone_data),
        'total_reports': sum(data['count'] for data in phone_data.values()),
        'last_updated': datetime.now().isoformat()
    }

def load_data():
    """从文件加载数据"""
    global phone_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for phone, info in data.items():
                    phone_data[phone]['count'] = info['count']
                    phone_data[phone]['users'] = set(info['users'])
                    phone_data[phone]['first_seen'] = info['first_seen']
            logger.info(f"成功加载 {len(phone_data)} 个电话号码记录")
    except Exception as e:
        logger.error(f"加载数据时出错: {e}")

def save_data():
    """保存数据到文件"""
    try:
        data_to_save = {}
        for phone, info in phone_data.items():
            data_to_save[phone] = {
                'count': info['count'],
                'users': list(info['users']),
                'first_seen': info['first_seen']
            }
        
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        logger.info("数据已保存")
    except Exception as e:
        logger.error(f"保存数据时出错: {e}")

def extract_phone_numbers(text: str) -> Set[str]:
    """从文本中提取电话号码"""
    patterns = [
        r'1[3-9]\d{9}',                    # 中国手机号
        r'\+86\s*1[3-9]\d{9}',             # 带国际区号的中国手机号
        r'\d{3}-\d{4}-\d{4}',              # xxx-xxxx-xxxx格式
        r'\d{3}\s\d{4}\s\d{4}',            # xxx xxxx xxxx格式
        r'\(\d{3}\)\s*\d{3}-\d{4}',        # (xxx) xxx-xxxx格式
        r'\+\d{1,3}\s*\d{10,14}',          # 国际格式
    ]
    
    phone_numbers = set()
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            clean_number = re.sub(r'[\s\-\(\)\+]', '', match)
            if len(clean_number) >= 10:
                phone_numbers.add(clean_number)
    
    return phone_numbers

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理/start命令"""
    welcome_message = """
🤖 **电话号码重复检测机器人**

我会监控群组中的消息，检测重复发送的电话号码并发出警告。

**功能：**
• 自动检测消息中的电话号码
• 标记重复出现的号码
• 统计功能（管理员可用）

**命令：**
/start - 显示此帮助信息
/stats - 查看统计信息（仅管理员）
/clear - 清除所有数据（仅管理员）

现在可以在群组中使用了！
    """
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def check_for_duplicates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """检查消息中是否有重复的电话号码"""
    if not update.message or not update.message.text:
        return

    message_text = update.message.text
    user_id = update.effective_user.id
    username = update.effective_user.username or "未知用户"
    
    # 提取电话号码
    phone_numbers = extract_phone_numbers(message_text)
    
    for phone in phone_numbers:
        # 记录或更新电话号码信息
        if phone_data[phone]['first_seen'] is None:
            phone_data[phone]['first_seen'] = datetime.now().isoformat()
        
        phone_data[phone]['count'] += 1
        phone_data[phone]['users'].add(str(user_id))
        
        # 如果是重复的电话号码，发送警告
        if phone_data[phone]['count'] > 1:
            masked_phone = phone[:3] + "*" * (len(phone) - 6) + phone[-3:]
            warning_message = f"""
⚠️ **检测到重复电话号码！**

号码：`{masked_phone}`
出现次数：{phone_data[phone]['count']}
首次发现：{phone_data[phone]['first_seen'][:10]}

请注意可能的重复或垃圾信息！
            """
            await update.message.reply_text(warning_message, parse_mode='Markdown')
    
    # 保存数据
    if phone_numbers:
        save_data()

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """显示统计信息（仅管理员）"""
    user_id = update.effective_user.id
    
    # 简单的管理员检查（可以根据需要修改）
    chat_member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
    if chat_member.status not in ['creator', 'administrator']:
        await update.message.reply_text("❌ 此命令仅限管理员使用")
        return
    
    total_numbers = len(phone_data)
    total_reports = sum(data['count'] for data in phone_data.values())
    duplicates = sum(1 for data in phone_data.values() if data['count'] > 1)
    
    stats_message = f"""
📊 **统计信息**

总电话号码：{total_numbers}
总报告次数：{total_reports}
重复号码：{duplicates}
唯一号码：{total_numbers - duplicates}

🕒 最后更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    """
    await update.message.reply_text(stats_message, parse_mode='Markdown')

async def clear_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """清除所有数据（仅管理员）"""
    user_id = update.effective_user.id
    
    # 检查管理员权限
    chat_member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
    if chat_member.status not in ['creator', 'administrator']:
        await update.message.reply_text("❌ 此命令仅限管理员使用")
        return
    
    global phone_data
    phone_data.clear()
    save_data()
    
    await update.message.reply_text("✅ 所有数据已清除")

def run_flask():
    """在单独线程中运行Flask应用"""
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)

async def run_bot():
    """运行Telegram机器人"""
    # 加载数据
    load_data()
    
    # 创建应用
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 添加处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("clear", clear_data))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_for_duplicates))
    
    logger.info("🤖 电话号码重复检测机器人已启动！")
    logger.info("机器人正在运行中...")
    
    # 启动机器人
    await application.run_polling(drop_pending_updates=True)

def main():
    """主函数"""
    # 在单独线程中启动Flask
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # 运行Telegram机器人
    asyncio.run(run_bot())

if __name__ == '__main__':
    main()