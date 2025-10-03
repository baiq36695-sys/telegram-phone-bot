#!/usr/bin/env python3
"""
Telegram电话号码重复检测机器人 - Render.com版本
24/7云端运行版本
支持多国电话号码格式（中国 + 马来西亚）
修复asyncio事件循环冲突问题
"""

import asyncio
import json
import os
import re
import logging
import signal
import sys
from collections import defaultdict
from datetime import datetime
from typing import Dict, Set
import threading
import time
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

# 全局变量用于优雅关闭
shutdown_event = threading.Event()

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
    """从文本中提取电话号码 - 支持多国格式"""
    patterns = [
        # 马来西亚电话号码（按优先级排序）
        # 1. 手机号码格式 +60 11-2896 2309 (用户要求的标准格式)
        r'\+60\s+1[0-9]\s*-\s*\d{4}\s+\d{4}',       # +60 11-2896 2309 (手机)
        r'\+60\s+1[0-9]\s*-\s*\d{3,4}\s*-\s*\d{4}', # +60 11-2896-2309 (手机)
        
        # 2. 固话格式 +60 3-1234 5678 (吉隆坡等地区)
        r'\+60\s+[3-9]\s*-\s*\d{4}\s+\d{4}',        # +60 3-1234 5678 (固话)
        r'\+60\s+[3-9]\s*-\s*\d{3,4}\s*-\s*\d{4}',  # +60 3-1234-5678 (固话)
        
        # 3. 通用马来西亚格式
        r'\+60\s*1[0-9]\d{7,8}',                     # +60112896309 (手机紧凑)
        r'\+60\s*[3-9]\d{7,8}',                      # +6031234567 (固话紧凑)
        r'\+60\s*\d{1,2}\s+\d{3,4}\s+\d{4}',        # +60 11 2896 2309 (空格分隔)
        
        # 4. 不带+号的马来西亚格式
        r'60\s+1[0-9]\s*-\s*\d{4}\s+\d{4}',         # 60 11-2896 2309
        r'60\s+[3-9]\s*-\s*\d{4}\s+\d{4}',          # 60 3-1234 5678
        r'60\s*[1-9]\d{8,9}',                       # 60112896309
        
        # 中国手机号码
        r'1[3-9]\d{9}',                              # 中国手机号
        r'\+86\s*1[3-9]\d{9}',                       # 带国际区号的中国手机号
        r'\+86\s+1[3-9]\d{9}',                       # +86 138 0013 8000 格式
        
        # 通用格式（放在最后，避免误匹配）
        r'\d{3}-\d{4}-\d{4}',                        # xxx-xxxx-xxxx格式
        r'\d{3}\s\d{4}\s\d{4}',                      # xxx xxxx xxxx格式
        r'\(\d{3}\)\s*\d{3}-\d{4}',                  # (xxx) xxx-xxxx格式
    ]
    
    phone_numbers = set()
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            # 清理号码，保留数字和+号
            clean_number = re.sub(r'[\s\-\(\)]', '', match)
            
            # 验证号码长度和格式
            digit_count = len(re.sub(r'[^\d]', '', clean_number))
            
            # 马来西亚号码：9-11位数字（含区号）
            # 中国号码：11位数字
            # 其他：至少8位数字
            if digit_count >= 8:
                phone_numbers.add(clean_number)
    
    return phone_numbers

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理/start命令"""
    welcome_message = """
🤖 **电话号码重复检测机器人**

我会监控群组中的消息，检测重复发送的电话号码并发出警告。

**支持格式：**
🇲🇾 **马来西亚：**
  • `+60 11-2896 2309` （标准格式）
  • `+60 11-2896-2309` （横线分隔）
  • `+60112896309` （紧凑格式）
  • `60 11-2896 2309` （不带+号）

🇨🇳 **中国：**
  • `+86 138 0013 8000` （国际格式）
  • `13800138000` （本地格式）

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
    try:
        port = int(os.environ.get('PORT', 10000))
        logger.info(f"启动Flask服务器，端口: {port}")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        logger.error(f"Flask服务器启动失败: {e}")

def signal_handler(signum, frame):
    """信号处理器"""
    logger.info("接收到关闭信号，正在优雅关闭...")
    shutdown_event.set()
    sys.exit(0)

async def run_bot():
    """运行Telegram机器人"""
    try:
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
        
    except Exception as e:
        logger.error(f"机器人运行出错: {e}")
        raise

def main():
    """主函数"""
    # 设置信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        logger.info("正在启动应用...")
        
        # 在单独线程中启动Flask
        flask_thread = threading.Thread(target=run_flask, daemon=True, name="FlaskThread")
        flask_thread.start()
        
        # 等待Flask启动
        time.sleep(3)
        logger.info("Flask服务器已在后台启动")
        
        # 在主线程中运行Telegram机器人
        logger.info("启动Telegram机器人...")
        asyncio.run(run_bot())
        
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
        shutdown_event.set()
    except Exception as e:
        logger.error(f"程序运行出错: {e}")
        shutdown_event.set()
        raise
    finally:
        logger.info("程序正在关闭...")

if __name__ == '__main__':
    main()
