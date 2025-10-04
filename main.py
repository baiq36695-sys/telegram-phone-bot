#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电话号码查重机器人 v9.6
修复事件循环关闭导致的重启失败问题
"""

import os
import re
import logging
import time
import threading
import asyncio
from datetime import datetime
from collections import defaultdict
from typing import Dict, Set

from telegram import Update
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    ContextTypes
)
from flask import Flask, jsonify

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 全局变量
phone_numbers: Set[str] = set()
duplicate_numbers: Dict[str, int] = defaultdict(int)
stats = {
    'total_messages': 0,
    'total_numbers': 0,
    'duplicate_count': 0,
    'start_time': datetime.now(),
    'restart_count': 0
}

# 环境变量兼容性：支持两种常见的环境变量名
BOT_TOKEN = os.getenv('BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
PORT = int(os.getenv('PORT', 10000))

# Flask应用
app = Flask(__name__)

@app.route('/')
def home():
    uptime = datetime.now() - stats['start_time']
    return jsonify({
        'status': 'running',
        'bot_version': 'v9.6',
        'uptime_seconds': int(uptime.total_seconds()),
        'total_numbers': len(phone_numbers),
        'duplicate_count': stats['duplicate_count'],
        'restart_count': stats['restart_count']
    })

@app.route('/health')
def health():
    return jsonify({'status': 'healthy'})

def start_flask_server():
    """启动Flask服务器"""
    try:
        logger.info(f"Flask服务器启动，端口: {PORT}")
        app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Flask服务器启动失败: {e}")

# Telegram Bot 处理函数
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    user = update.effective_user
    start_text = f"""
🤖 <b>电话号码查重机器人 v9.6</b>

👋 欢迎使用，{user.first_name}！

📱 <b>功能说明：</b>
• 自动识别消息中的电话号码
• 实时检测重复号码
• 显示实时时间戳
• 统计分析功能

🔧 <b>可用命令：</b>
/start - 显示欢迎信息
/help - 查看帮助信息  
/stats - 查看统计信息
/clear - 清空所有数据

💡 <b>使用方法：</b>
直接发送包含电话号码的消息，机器人会自动识别并检查重复！

支持格式：13812345678、138-1234-5678、138 1234 5678 等
"""
    await update.message.reply_html(start_text, disable_web_page_preview=True)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令"""
    help_text = """
🆘 <b>帮助信息 - 电话号码查重机器人 v9.6</b>

📋 <b>主要功能：</b>
• 🔍 自动识别消息中的电话号码
• 🔄 实时检测重复号码  
• ⏰ 显示实时时间戳
• 📊 提供详细统计信息

🎯 <b>支持的号码格式：</b>
• 标准格式：13812345678
• 带横线：138-1234-5678  
• 带空格：138 1234 5678
• 带括号：(138)1234-5678
• 国际格式：+86 138 1234 5678

⚡ <b>命令列表：</b>
/start - 🚀 显示欢迎信息
/help - 🆘 查看此帮助信息
/stats - 📊 查看统计数据
/clear - 🗑️ 清空所有数据

💡 <b>使用技巧：</b>
• 可以一次发送多个号码
• 支持混合文本和号码
• 重复号码会被高亮显示
• 所有操作都有实时反馈

🔧 <b>版本信息：</b>
当前版本：v9.6
更新内容：修复事件循环重启问题

如有问题，请检查号码格式或联系管理员。
"""
    await update.message.reply_html(help_text, disable_web_page_preview=True)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /stats 命令"""
    current_time = datetime.now()
    uptime = current_time - stats['start_time']
    
    # 计算运行时间
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    uptime_str = f"{days}天 {hours}小时 {minutes}分钟 {seconds}秒"
    
    stats_text = f"""
📊 <b>机器人统计信息 v9.6</b>

⏰ <b>运行状态：</b>
• 启动时间：{stats['start_time'].strftime('%Y-%m-%d %H:%M:%S')}
• 当前时间：{current_time.strftime('%Y-%m-%d %H:%M:%S')}
• 运行时长：{uptime_str}
• 重启次数：{stats['restart_count']}

📱 <b>号码统计：</b>
• 总消息数：{stats['total_messages']}
• 唯一号码：{len(phone_numbers)}
• 重复检测：{stats['duplicate_count']} 次
• 总号码数：{stats['total_numbers']}

🔄 <b>重复号码详情：</b>
"""
    
    if duplicate_numbers:
        for number, count in sorted(duplicate_numbers.items(), key=lambda x: x[1], reverse=True)[:10]:
            stats_text += f"• {number}：重复 {count} 次\n"
    else:
        stats_text += "• 暂无重复号码"
    
    stats_text += f"\n💾 <b>系统信息：</b>\n• 版本：v9.6\n• 状态：正常运行"
    
    await update.message.reply_html(stats_text, disable_web_page_preview=True)

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /clear 命令"""
    global phone_numbers, duplicate_numbers, stats
    
    # 保存重启次数
    restart_count = stats['restart_count']
    
    # 清空数据
    phone_numbers.clear()
    duplicate_numbers.clear()
    stats = {
        'total_messages': 0,
        'total_numbers': 0,
        'duplicate_count': 0,
        'start_time': datetime.now(),
        'restart_count': restart_count  # 保持重启次数
    }
    
    clear_text = """
🗑️ <b>数据清空完成</b>

✅ 已清空的数据：
• 所有电话号码记录
• 重复号码统计
• 消息计数器
• 启动时间已重置

💡 机器人继续运行，可以开始新的号码检测。
"""
    
    await update.message.reply_html(clear_text, disable_web_page_preview=True)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理包含电话号码的消息"""
    global stats
    
    message_text = update.message.text
    stats['total_messages'] += 1
    
    # 电话号码正则表达式（支持多种格式）
    phone_pattern = r'(?:\+86[-\s]?)?(?:1[3-9]\d{9}|(?:\(\d{3}\)|\d{3})[-\s]?\d{3,4}[-\s]?\d{4})'
    
    # 查找所有电话号码
    found_numbers = re.findall(phone_pattern, message_text)
    
    if not found_numbers:
        return
    
    # 标准化号码格式（移除所有非数字字符，保留11位）
    normalized_numbers = []
    for number in found_numbers:
        # 移除所有非数字字符
        clean_number = re.sub(r'\D', '', number)
        # 如果以86开头且长度为13，去掉前缀
        if clean_number.startswith('86') and len(clean_number) == 13:
            clean_number = clean_number[2:]
        # 只保留11位中国手机号
        if len(clean_number) == 11 and clean_number.startswith('1'):
            normalized_numbers.append(clean_number)
    
    if not normalized_numbers:
        return
    
    # 更新统计
    stats['total_numbers'] += len(normalized_numbers)
    
    # 检查重复号码
    new_numbers = []
    duplicate_found = []
    
    for number in normalized_numbers:
        if number in phone_numbers:
            duplicate_numbers[number] += 1
            duplicate_found.append(number)
            stats['duplicate_count'] += 1
        else:
            phone_numbers.add(number)
            new_numbers.append(number)
    
    # 构建回复消息
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    response_parts = [f"📱 <b>号码检测结果</b>\n⏰ 检测时间：{current_time}\n"]
    
    if new_numbers:
        response_parts.append(f"✅ <b>新增号码 ({len(new_numbers)}个)：</b>")
        for number in new_numbers:
            response_parts.append(f"• {number}")
    
    if duplicate_found:
        response_parts.append(f"\n🔄 <b>重复号码 ({len(duplicate_found)}个)：</b>")
        for number in duplicate_found:
            count = duplicate_numbers[number]
            response_parts.append(f"• {number} <b>(第{count+1}次出现)</b>")
    
    # 添加统计信息
    response_parts.append(f"\n📊 <b>当前统计：</b>")
    response_parts.append(f"• 唯一号码：{len(phone_numbers)}个")
    response_parts.append(f"• 重复检测：{stats['duplicate_count']}次")
    
    response_text = "\n".join(response_parts)
    
    await update.message.reply_html(response_text, disable_web_page_preview=True)

def create_application():
    """创建新的Telegram应用实例"""
    # 创建应用
    application = Application.builder().token(BOT_TOKEN).build()
    
    # 添加处理器
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    return application

def run_bot():
    """运行机器人（修复事件循环问题）"""
    global stats
    
    try:
        # 创建新的事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        logger.info("开始创建Telegram应用...")
        
        # 创建新的应用实例
        application = create_application()
        
        logger.info(f"电话号码查重机器人 v9.6 启动成功！重启次数: {stats['restart_count']}")
        logger.info("开始运行轮询...")
        
        # 在新的事件循环中运行
        application.run_polling(
            drop_pending_updates=True,  # 丢弃待处理的更新
            close_loop=False  # 不要自动关闭事件循环
        )
        
    except Exception as e:
        logger.error(f"Bot运行错误: {e}")
        logger.error(f"机器人错误详情: {traceback.format_exc()}")
        raise e

def main():
    """主函数"""
    logger.info("=== 电话号码查重机器人 v9.6 启动 ===")
    logger.info(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 启动Flask服务器
    logger.info(f"Flask服务器启动，端口: {PORT}")
    flask_thread = threading.Thread(target=start_flask_server, daemon=True)
    flask_thread.start()
    logger.info("Flask服务器线程已启动")
    
    # 自动重启机制（修复版）
    restart_count = 0
    max_restarts = 10
    base_delay = 10
    
    while restart_count < max_restarts:
        try:
            restart_count += 1
            stats['restart_count'] = restart_count
            
            logger.info(f"=== 第 {restart_count} 次启动机器人 ===")
            
            run_bot()
            
        except KeyboardInterrupt:
            logger.info("接收到中断信号，程序退出")
            break
            
        except Exception as e:
            import traceback
            logger.error(f"=== 机器人异常停止（第{restart_count}次） ===")
            logger.error(f"异常类型: {type(e).__name__}")
            logger.error(f"异常信息: {e}")
            logger.error(f"异常详情：{traceback.format_exc()}")
            
            if restart_count >= max_restarts:
                logger.error(f"已达到最大重启次数 {max_restarts}，程序退出")
                break
            
            # 渐进式延迟重启
            delay = min(base_delay * restart_count, 60)
            logger.info(f"等待 {delay} 秒后重启...")
            time.sleep(delay)

if __name__ == "__main__":
    main()
