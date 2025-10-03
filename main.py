#!/usr/bin/env python3
"""
电话号码重复检测机器人 - 终极解决方案
使用nest_asyncio一次性解决所有事件循环冲突问题
"""

import os
import re
import logging
import signal
import sys
import asyncio
from typing import Set, Dict, Any
from collections import defaultdict
import threading
import time

# 首先安装并应用nest_asyncio来解决事件循环冲突
try:
    import nest_asyncio
    nest_asyncio.apply()
    logger = logging.getLogger(__name__)
    logger.info("✅ nest_asyncio已应用，事件循环冲突已解决")
except ImportError:
    # 如果没有nest_asyncio，我们手动安装
    import subprocess
    import sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "nest-asyncio"])
    import nest_asyncio
    nest_asyncio.apply()

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask, jsonify

# 设置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 初始化Flask应用
app = Flask(__name__)

# 全局变量
user_groups: Dict[int, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
shutdown_event = threading.Event()

def extract_phone_numbers(text: str) -> Set[str]:
    """从文本中提取电话号码 - 支持多国格式，特别优化马来西亚格式"""
    patterns = [
        # 马来西亚电话号码（按优先级排序）
        r'\+60\s+1[0-9]\s*-?\s*\d{4}\s+\d{4}',       # +60 11-2896 2309 或 +60 11 2896 2309
        r'\+60\s*1[0-9]\s*-?\s*\d{4}\s*-?\s*\d{4}',  # +60 11-2896-2309 或 +6011-2896-2309
        r'\+60\s*1[0-9]\d{7,8}',                     # +60 11xxxxxxxx
        r'\+60\s*[3-9]\s*-?\s*\d{4}\s+\d{4}',        # +60 3-1234 5678 (固话)
        r'\+60\s*[3-9]\d{7,8}',                      # +60 312345678 (固话)
        
        # 其他国际格式
        r'\+86\s*1[3-9]\d{9}',                       # 中国手机
        r'\+86\s*[2-9]\d{2,3}\s*\d{7,8}',           # 中国固话
        r'\+1\s*[2-9]\d{2}\s*[2-9]\d{2}\s*\d{4}',   # 美国/加拿大
        r'\+44\s*[1-9]\d{8,9}',                     # 英国
        r'\+65\s*[6-9]\d{7}',                       # 新加坡
        r'\+852\s*[2-9]\d{7}',                      # 香港
        r'\+853\s*[6-9]\d{7}',                      # 澳门
        r'\+886\s*[0-9]\d{8}',                      # 台湾
        r'\+91\s*[6-9]\d{9}',                       # 印度
        r'\+81\s*[7-9]\d{8}',                       # 日本手机
        r'\+82\s*1[0-9]\d{7,8}',                    # 韩国
        r'\+66\s*[6-9]\d{8}',                       # 泰国
        r'\+84\s*[3-9]\d{8}',                       # 越南
        r'\+63\s*[2-9]\d{8}',                       # 菲律宾
        r'\+62\s*[1-9]\d{7,10}',                    # 印度尼西亚
        
        # 通用国际格式
        r'\+\d{1,4}\s*\d{1,4}\s*\d{1,4}\s*\d{1,9}', # 通用国际格式
        
        # 本地格式（无国际代码）
        r'1[3-9]\d{9}',                             # 中国手机（本地）
        r'0[1-9]\d{1,3}[-\s]?\d{7,8}',             # 中国固话（本地）
        r'01[0-9][-\s]?\d{4}[-\s]?\d{4}',          # 马来西亚手机（本地）
        r'0[3-9][-\s]?\d{4}[-\s]?\d{4}',           # 马来西亚固话（本地）
    ]
    
    phone_numbers = set()
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            # 清理电话号码：移除多余空格，但保留格式
            cleaned = re.sub(r'\s+', ' ', match.strip())
            phone_numbers.add(cleaned)
    
    return phone_numbers

def find_duplicates(phones: Set[str]) -> Set[str]:
    """查找重复的电话号码"""
    # 创建标准化映射
    normalized_map = {}
    duplicates = set()
    
    for phone in phones:
        # 标准化：移除所有空格、连字符等格式字符，只保留数字和+号
        normalized = re.sub(r'[^\d+]', '', phone)
        
        if normalized in normalized_map:
            # 发现重复，添加原始格式和已存在的格式
            duplicates.add(phone)
            duplicates.add(normalized_map[normalized])
        else:
            normalized_map[normalized] = phone
    
    return duplicates

# Flask路由
@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    """健康检查端点"""
    return jsonify({
        'status': 'healthy',
        'service': 'telegram-phone-bot',
        'nest_asyncio': 'enabled',
        'timestamp': time.time()
    })

@app.route('/status')
def status():
    """状态端点"""
    return jsonify({
        'bot_status': 'running' if not shutdown_event.is_set() else 'stopped',
        'groups_monitored': len(user_groups),
        'total_phone_numbers': sum(len(data['phones']) for data in user_groups.values()),
        'event_loop_fix': 'nest_asyncio'
    })

# Telegram机器人函数
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    help_text = """
🤖 **电话号码重复检测机器人**

我可以帮你检测消息中的重复电话号码！

**支持的格式：**
📱 **马来西亚格式：**
• +60 11-2896 2309
• +60 11 2896 2309
• +6011-28962309
• 01-1234 5678

📞 **其他国际格式：**
• 中国: +86 138 0013 8000
• 美国: +1 555 123 4567
• 新加坡: +65 6123 4567

**使用方法：**
1. 直接发送包含电话号码的消息
2. 我会自动检测并告诉你是否有重复
3. 支持群组和私聊使用

**命令：**
/start - 显示帮助信息
/clear - 清除当前群组的电话号码记录
/stats - 查看统计信息

发送任何包含电话号码的消息开始使用吧！
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /clear 命令"""
    chat_id = update.effective_chat.id
    user_groups[chat_id]['phones'].clear()
    await update.message.reply_text("✅ 已清除所有电话号码记录")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /stats 命令"""
    chat_id = update.effective_chat.id
    phone_count = len(user_groups[chat_id]['phones'])
    
    stats_text = f"""
📊 **统计信息**

• 总电话号码数: {phone_count}
• 群组ID: {chat_id}
• 机器人状态: 运行中 ✅
• 事件循环: 已修复 🔧
"""
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理包含电话号码的消息"""
    try:
        chat_id = update.effective_chat.id
        message_text = update.message.text
        
        # 提取电话号码
        phone_numbers = extract_phone_numbers(message_text)
        
        if not phone_numbers:
            return
        
        # 检查重复
        existing_phones = user_groups[chat_id]['phones']
        new_phones = phone_numbers - existing_phones
        duplicate_phones = phone_numbers & existing_phones
        
        response_parts = []
        
        if new_phones:
            response_parts.append(f"📱 发现 {len(new_phones)} 个新电话号码:")
            for phone in sorted(new_phones):
                response_parts.append(f"• `{phone}`")
            
            # 添加到记录中
            existing_phones.update(new_phones)
        
        if duplicate_phones:
            response_parts.append(f"⚠️ 发现 {len(duplicate_phones)} 个重复电话号码:")
            for phone in sorted(duplicate_phones):
                response_parts.append(f"• `{phone}` ⚠️")
        
        # 查找内部重复
        internal_duplicates = find_duplicates(phone_numbers)
        if internal_duplicates:
            response_parts.append(f"🔄 消息内部重复 {len(internal_duplicates)} 个号码:")
            for phone in sorted(internal_duplicates):
                response_parts.append(f"• `{phone}` 🔄")
        
        if response_parts:
            response = "\n".join(response_parts)
            await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"处理消息时出错: {e}")
        await update.message.reply_text("❌ 处理消息时出现错误")

def run_flask():
    """在独立线程中运行Flask"""
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"启动Flask服务器，端口: {port}")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
    )

async def run_bot():
    """运行Telegram机器人"""
    # 获取Bot Token
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("未找到TELEGRAM_BOT_TOKEN环境变量")
        return
    
    try:
        # 创建应用
        application = Application.builder().token(bot_token).build()
        
        # 添加处理器
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("clear", clear_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("🤖 电话号码重复检测机器人已启动！")
        logger.info("✅ 使用nest_asyncio解决事件循环冲突")
        
        # 运行机器人
        await application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        logger.error(f"机器人运行错误: {e}")
        shutdown_event.set()

def signal_handler(signum, frame):
    """信号处理器 - 用于优雅关闭"""
    logger.info(f"收到信号 {signum}，正在关闭...")
    shutdown_event.set()
    sys.exit(0)

def main():
    """主函数 - 终极解决方案"""
    logger.info("正在启动应用...")
    logger.info("🔧 已应用nest_asyncio，一次性解决事件循环冲突")
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 在独立线程中启动Flask
        flask_thread = threading.Thread(target=run_flask, daemon=True, name="FlaskThread")
        flask_thread.start()
        
        # 等待Flask启动
        time.sleep(3)
        logger.info("Flask服务器已在后台启动")
        
        logger.info("启动Telegram机器人...")
        
        # 现在可以安全地在主线程中运行asyncio
        asyncio.run(run_bot())
        
    except Exception as e:
        logger.error(f"程序运行错误: {e}")
        shutdown_event.set()
    
    logger.info("程序正在关闭...")

if __name__ == '__main__':
    main()
