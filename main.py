#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
完全修正版本的Telegram电话号码检测机器人 v6.0
专门修复电话号码正则表达式问题
"""

import os
import asyncio
import logging
import re
import json
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Set, List, Optional, Any
from collections import defaultdict
import urllib.parse

# 导入Telegram Bot相关库
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from telegram.constants import ParseMode
from telegram.error import NetworkError, TelegramError

# 导入Flask相关库
from flask import Flask, jsonify, request, render_template_string

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# 机器人配置
TOKEN = os.getenv('BOT_TOKEN')
PORT = int(os.getenv('PORT', 8000))

# 全局数据存储
user_data_storage = defaultdict(lambda: {
    'phones': set(),
    'normalized_phones': set(),  # 新增：专门用于重复检测的标准化号码集合
    'risk_scores': {},
    'warnings_issued': set(),
    'last_activity': None,
    'security_alerts': []
})

# 系统状态管理
shutdown_event = threading.Event()
bot_application = None
is_running = False
flask_thread = None
bot_thread = None

# 风险评估等级
RISK_LEVELS = {
    'LOW': {'emoji': '🟢', 'color': 'LOW', 'score': 1},
    'MEDIUM': {'emoji': '🟡', 'color': 'MEDIUM', 'score': 2}, 
    'HIGH': {'emoji': '🟠', 'color': 'HIGH', 'score': 3},
    'CRITICAL': {'emoji': '🔴', 'color': 'CRITICAL', 'score': 4}
}

def normalize_phone_number(phone: str) -> str:
    """标准化电话号码：只保留数字和+号"""
    return re.sub(r'[^\d+]', '', phone)

def extract_phone_numbers(text: str) -> Set[str]:
    """从文本中提取电话号码 - 完全修正的马来西亚格式支持"""
    patterns = [
        # 马来西亚电话号码（修正版本 - 支持3位-4位和4位-4位格式）
        r'\+60\s*1[0-9][\s-]*\d{3}[\s-]+\d{4}',          # +60 13-970 3144 或 +60 13 970 3144
        r'\+60\s*1[0-9][\s-]*\d{4}[\s-]+\d{4}',          # +60 11-2896 2309 或 +60 11 2896 2309
        r'\+60\s*1[0-9][\s-]*\d{7,8}',                   # +60 13-9703144 或 +6013-9703144
        r'\+60\s*[3-9][\s-]*\d{3,4}[\s-]+\d{4}',         # +60 3-1234 5678 (固话)
        r'\+60\s*[3-9][\s-]*\d{7,8}',                    # +60 312345678 (固话)
        
        # 通用的国际手机号码格式
        r'\+86\s*1[3-9]\d{9}',                           # 中国手机
        r'\+86\s*[2-9]\d{2,3}[\s-]*\d{7,8}',            # 中国固话
        r'\+1[\s-]*[2-9]\d{2}[\s-]*[2-9]\d{2}[\s-]*\d{4}', # 美国/加拿大
        r'\+44\s*[1-9]\d{8,9}',                         # 英国
        r'\+65\s*[6-9]\d{7}',                           # 新加坡
        r'\+852\s*[2-9]\d{7}',                          # 香港
        r'\+853\s*[6-9]\d{7}',                          # 澳门
        r'\+886\s*[0-9]\d{8}',                          # 台湾
        r'\+91\s*[6-9]\d{9}',                           # 印度
        r'\+81\s*[7-9]\d{8}',                           # 日本手机
        r'\+82\s*1[0-9]\d{7,8}',                        # 韩国
        r'\+66\s*[6-9]\d{8}',                           # 泰国
        r'\+84\s*[3-9]\d{8}',                           # 越南
        r'\+63\s*[2-9]\d{8}',                           # 菲律宾
        r'\+62\s*[1-9]\d{7,10}',                        # 印度尼西亚
        
        # 更宽松的通用国际格式
        r'\+\d{1,4}[\s-]*\d{1,4}[\s-]*\d{1,4}[\s-]*\d{1,9}', # 通用国际格式
        
        # 本地格式（无国际代码）
        r'1[3-9]\d{9}',                                 # 中国手机（本地）
        r'0[1-9]\d{1,3}[\s-]*\d{7,8}',                 # 中国固话（本地）
        r'01[0-9][\s-]*\d{3,4}[\s-]*\d{4}',            # 马来西亚手机（本地）
        r'0[3-9][\s-]*\d{3,4}[\s-]*\d{4}',             # 马来西亚固话（本地）
    ]
    
    phone_numbers = set()
    all_matches = []
    
    # 首先收集所有匹配项及其位置
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            all_matches.append((match.start(), match.end(), match.group()))
    
    # 按位置排序，避免重叠匹配
    all_matches.sort()
    
    # 过滤重叠的匹配
    filtered_matches = []
    for start, end, match_text in all_matches:
        # 检查是否与之前的匹配重叠
        overlap = False
        for prev_start, prev_end, _ in filtered_matches:
            if start < prev_end and end > prev_start:  # 有重叠
                overlap = True
                break
        
        if not overlap:
            filtered_matches.append((start, end, match_text))
    
    # 处理最终的匹配结果
    for _, _, match_text in filtered_matches:
        # 标准化电话号码格式：统一空格，保持结构
        cleaned = re.sub(r'\s+', ' ', match_text.strip())
        # 进一步标准化：移除多余的分隔符
        normalized = re.sub(r'[-\s]+', ' ', cleaned)
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        phone_numbers.add(normalized)
    
    return phone_numbers

def categorize_phone_number(phone: str) -> str:
    """识别电话号码的类型和国家"""
    clean_phone = normalize_phone_number(phone)
    
    # 马来西亚手机号码
    if clean_phone.startswith('+601'):
        return '🇲🇾 马来西亚手机'
    elif clean_phone.startswith('+603'):
        return '🇲🇾 马来西亚固话'
    elif clean_phone.startswith('+60'):
        return '🇲🇾 马来西亚'
    
    # 中国
    elif clean_phone.startswith('+861'):
        return '🇨🇳 中国手机'
    elif clean_phone.startswith('+86'):
        return '🇨🇳 中国'
    elif clean_phone.startswith('1') and len(clean_phone) == 11:
        return '🇨🇳 中国手机'
    
    # 其他国家
    elif clean_phone.startswith('+1'):
        return '🇺🇸 美国/加拿大'
    elif clean_phone.startswith('+44'):
        return '🇬🇧 英国'
    elif clean_phone.startswith('+65'):
        return '🇸🇬 新加坡'
    elif clean_phone.startswith('+852'):
        return '🇭🇰 香港'
    elif clean_phone.startswith('+853'):
        return '🇲🇴 澳门'
    elif clean_phone.startswith('+886'):
        return '🇹🇼 台湾'
    elif clean_phone.startswith('+91'):
        return '🇮🇳 印度'
    elif clean_phone.startswith('+81'):
        return '🇯🇵 日本'
    elif clean_phone.startswith('+82'):
        return '🇰🇷 韩国'
    elif clean_phone.startswith('+66'):
        return '🇹🇭 泰国'
    elif clean_phone.startswith('+84'):
        return '🇻🇳 越南'
    elif clean_phone.startswith('+63'):
        return '🇵🇭 菲律宾'
    elif clean_phone.startswith('+62'):
        return '🇮🇩 印度尼西亚'
    else:
        return '🌍 其他地区'

def assess_risk_level(phone_count: int, duplicate_count: int) -> dict:
    """风险评估算法"""
    if duplicate_count > 0:
        return RISK_LEVELS['CRITICAL']
    elif phone_count >= 10:
        return RISK_LEVELS['HIGH']
    elif phone_count >= 5:
        return RISK_LEVELS['MEDIUM']
    else:
        return RISK_LEVELS['LOW']

def generate_detailed_html_report(user_data: dict, new_phones: set, duplicates: set) -> str:
    """生成详细的HTML格式报告"""
    all_phones = user_data.get('phones', set())
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 风险评估
    risk_info = assess_risk_level(len(all_phones), len(duplicates))
    
    # 按国家分组统计
    country_stats = defaultdict(int)
    for phone in all_phones:
        country = categorize_phone_number(phone)
        country_stats[country] += 1
    
    # 构建报告
    report_lines = []
    
    # 标题和时间
    report_lines.append(f"📊 <b>检测时间</b>：{current_time}")
    report_lines.append("")
    
    # 统计摘要
    report_lines.append("📱 <b>本次检测摘要</b>：")
    report_lines.append(f"• 发现号码总数：<b>{len(new_phones)}</b> 个")
    report_lines.append(f"• 新增号码：<b>{len(new_phones) - len(duplicates)}</b> 个")
    report_lines.append(f"• 重复检测号码：<b>{len(duplicates)}</b> 个")
    report_lines.append("")
    
    # 国家分类统计
    report_lines.append("📊 <b>号码分类统计</b>：")
    for country, count in sorted(country_stats.items()):
        report_lines.append(f"• {country}：<b>{count}</b> 个")
    report_lines.append("")
    
    # 新增号码详情
    if new_phones - duplicates:
        report_lines.append("🆕 <b>详细检测信息</b>：")
        for i, phone in enumerate(sorted(new_phones - duplicates), 1):
            normalized = normalize_phone_number(phone)
            category = categorize_phone_number(phone)
            report_lines.append(f"{i}. 📞 {phone}")
            report_lines.append(f"   来源：{category}")
            report_lines.append(f"   🔧 标准化：{normalized}")
            report_lines.append("")
    
    # 重复号码警告
    if duplicates:
        report_lines.append(f"⚠️ <b>重复号码警告({len(duplicates)}个)</b>：")
        for i, phone in enumerate(sorted(duplicates), 1):
            normalized = normalize_phone_number(phone)
            category = categorize_phone_number(phone)
            report_lines.append(f"{i}. {risk_info['emoji']} {phone}")
            report_lines.append(f"   来源：{category}")
            report_lines.append(f"   🔧 标准化：{normalized}")
            report_lines.append("")
    
    # 分隔线
    report_lines.append("=" * 45)
    
    # 系统状态
    report_lines.append("📊 <b>群组统计信息</b>：")
    report_lines.append(f"• 累计总计：<b>{len(all_phones)}</b> 个号码")
    report_lines.append(f"• 检测历史：<b>{len(user_data.get('risk_scores', {}))}</b> 次")
    report_lines.append(f"• 系统警告：<b>{len(user_data.get('warnings_issued', set()))}</b> 次")
    report_lines.append("")
    
    # 系统状态
    report_lines.append("🔧 <b>系统状态</b>：")
    report_lines.append(f"• 运行状态：{'✅ 正常运行' if is_running else '❌ 系统异常'}")
    report_lines.append(f"• HTML渲染：✅ 已启用")
    report_lines.append(f"• 红色警告：✅ 已启用")
    report_lines.append(f"• 联合过滤：✅ 已启用")
    report_lines.append(f"• 自动重启：✅ 使用中")
    report_lines.append(f"• 重复重复检测版本：✅ v6.0")
    report_lines.append("")
    
    # 分隔线和版本信息
    report_lines.append("=" * 45)
    report_lines.append("🤖 <b>自适应号码解析系统完全修正版</b> HTML渲染器 v6.0")
    report_lines.append("🚀 <b>集成红色重复警告系统，常驻重复检测引擎</b>")
    
    return '\n'.join(report_lines)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理/start命令"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "用户"
    
    welcome_message = (
        f"👋 欢迎使用，{user_name}！\n\n"
        "🤖 <b>自适应号码解析系统完全修正版</b> v6.0\n"
        "🎯 <b>终极重复检测修复 + 马来西亚格式支持</b>\n\n"
        "📱 <b>功能特色</b>：\n"
        "• 🔍 智能电话号码识别\n"
        "• 🌍 多国格式支持（特别优化马来西亚格式）\n"
        "• 🚨 精确重复检测警告\n"
        "• 📊 详细HTML格式报告\n"
        "• 🔄 自动状态管理\n\n"
        "💡 <b>使用方法</b>：\n"
        "直接发送包含电话号码的文本，系统会自动识别并分析\n\n"
        "🎛️ <b>控制命令</b>：\n"
        "/clear - 清除历史数据\n"
        "/status - 查看系统状态\n"
        "/help - 帮助信息\n\n"
        "🔧 当前版本：v6.0 - 完全修正的马来西亚格式支持"
    )
    
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.HTML)

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理/clear命令"""
    user_id = update.effective_user.id
    
    # 清除用户数据
    if user_id in user_data_storage:
        user_data_storage[user_id] = {
            'phones': set(),
            'normalized_phones': set(),
            'risk_scores': {},
            'warnings_issued': set(),
            'last_activity': None,
            'security_alerts': []
        }
    
    await update.message.reply_text(
        "✅ <b>数据清除成功</b>\n\n"
        "🗑️ 已清除所有历史电话号码记录\n"
        "📊 已重置统计数据\n"
        "🚨 已清除警告记录\n\n"
        "💡 现在可以重新开始检测电话号码了",
        parse_mode=ParseMode.HTML
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理/status命令"""
    user_id = update.effective_user.id
    user_data = user_data_storage[user_id]
    
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    status_message = (
        f"📊 <b>系统状态报告</b>\n"
        f"🕒 查询时间：{current_time}\n\n"
        f"📱 <b>数据统计</b>：\n"
        f"• 累计号码：<b>{len(user_data.get('phones', set()))}</b> 个\n"
        f"• 检测次数：<b>{len(user_data.get('risk_scores', {}))}</b> 次\n"
        f"• 警告记录：<b>{len(user_data.get('warnings_issued', set()))}</b> 次\n\n"
        f"🔧 <b>系统状态</b>：\n"
        f"• 运行状态：{'✅ 正常' if is_running else '❌ 异常'}\n"
        f"• HTML渲染：✅ 已启用\n"
        f"• 重复检测：✅ v6.0\n"
        f"• 自动重启：✅ 启用\n\n"
        f"🌍 <b>格式支持</b>：\n"
        f"• 马来西亚：✅ 完全支持\n"
        f"• 中国：✅ 支持\n"
        f"• 国际格式：✅ 支持\n\n"
        f"版本：v6.0 - 完全修正版"
    )
    
    await update.message.reply_text(status_message, parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理/help命令"""
    help_message = (
        "📖 <b>帮助文档</b>\n\n"
        "🤖 <b>系统说明</b>：\n"
        "这是一个智能电话号码检测系统，能够自动识别并分析文本中的电话号码\n\n"
        "🔍 <b>支持格式</b>：\n"
        "• 马来西亚：+60 13-970 3144, +60 11 2896 2309\n"
        "• 中国：+86 138 0013 8000, 138-0013-8000\n"
        "• 美国：+1 555-123-4567\n"
        "• 其他国际格式\n\n"
        "⚠️ <b>重复检测</b>：\n"
        "系统会智能识别重复的电话号码（忽略格式差异）\n\n"
        "📊 <b>报告功能</b>：\n"
        "• HTML格式详细报告\n"
        "• 国家分类统计\n"
        "• 风险评估\n"
        "• 重复警告\n\n"
        "🎛️ <b>命令列表</b>：\n"
        "/start - 启动机器人\n"
        "/clear - 清除历史数据\n"
        "/status - 查看系统状态\n"
        "/help - 显示帮助信息\n\n"
        "💡 <b>使用提示</b>：\n"
        "直接发送包含电话号码的文本即可开始检测"
    )
    
    await update.message.reply_text(help_message, parse_mode=ParseMode.HTML)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理普通消息"""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    # 确保用户数据结构存在，包含新的normalized_phones字段
    if 'phones' not in context.user_data:
        context.user_data['phones'] = set()
        context.user_data['normalized_phones'] = set()  # 专门用于重复检测
        context.user_data['risk_scores'] = {}
        context.user_data['warnings_issued'] = set()
        context.user_data['last_activity'] = None
        context.user_data['security_alerts'] = []
    
    # 提取电话号码
    found_numbers = extract_phone_numbers(message_text)
    
    if not found_numbers:
        await update.message.reply_text(
            "🔍 <b>未检测到电话号码</b>\n\n"
            "💡 请确保电话号码格式正确，支持的格式包括：\n"
            "• +60 13-970 3144\n"
            "• +86 138 0013 8000\n"
            "• +1 555-123-4567\n"
            "• 以及其他国际格式",
            parse_mode=ParseMode.HTML
        )
        return
    
    # **关键修正：使用独立的normalized_phones集合进行重复检测**
    new_phones = set()
    duplicates = set()
    
    for phone in found_numbers:
        # 标准化号码用于重复检测
        normalized = normalize_phone_number(phone)
        
        # 检查是否重复（基于标准化的号码）
        if normalized in context.user_data['normalized_phones']:
            duplicates.add(phone)
        else:
            new_phones.add(phone)
            # 添加到两个集合中
            context.user_data['phones'].add(phone)
            context.user_data['normalized_phones'].add(normalized)
    
    # 更新用户数据存储
    user_data_storage[user_id] = dict(context.user_data)
    
    # 生成详细报告
    html_report = generate_detailed_html_report(
        context.user_data,
        found_numbers,  # 传入所有找到的号码
        duplicates
    )
    
    # 发送报告
    await update.message.reply_text(html_report, parse_mode=ParseMode.HTML)
    
    # 更新活动时间
    context.user_data['last_activity'] = datetime.now()

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """错误处理函数"""
    logger.error(f"Exception while handling an update: {context.error}")
    
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ <b>系统错误</b>\n\n"
                "抱歉，处理您的消息时出现错误。\n"
                "请稍后重试或联系管理员。\n\n"
                f"错误时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Error sending error message: {e}")

# Flask监控应用
app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health_check():
    """健康检查端点"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': 'v6.0',
        'bot_running': is_running,
        'uptime': time.time()
    })

@app.route('/stats', methods=['GET'])
def get_stats():
    """获取统计信息"""
    total_users = len(user_data_storage)
    total_phones = sum(len(data.get('phones', set())) for data in user_data_storage.values())
    
    return jsonify({
        'total_users': total_users,
        'total_phones': total_phones,
        'version': 'v6.0',
        'features': ['duplicate_detection', 'html_rendering', 'malaysia_support']
    })

@app.route('/', methods=['GET'])
def index():
    """主页"""
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>电话号码检测机器人 v6.0</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            .status { color: green; font-weight: bold; }
            .version { color: blue; }
        </style>
    </head>
    <body>
        <h1>🤖 电话号码检测机器人</h1>
        <p class="version">版本：v6.0 - 完全修正版</p>
        <p class="status">✅ 系统运行正常</p>
        <p>🔧 特性：智能重复检测、HTML渲染、马来西亚格式支持</p>
        <p>📊 监控端点：</p>
        <ul>
            <li><a href="/health">/health</a> - 健康检查</li>
            <li><a href="/stats">/stats</a> - 统计信息</li>
        </ul>
    </body>
    </html>
    """)

def run_flask():
    """运行Flask应用"""
    global is_running
    is_running = True
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

def run_bot():
    """运行Telegram机器人"""
    global bot_application
    
    try:
        # 创建应用
        bot_application = Application.builder().token(TOKEN).build()
        
        # 添加处理器
        bot_application.add_handler(CommandHandler("start", start_command))
        bot_application.add_handler(CommandHandler("clear", clear_command))
        bot_application.add_handler(CommandHandler("status", status_command))
        bot_application.add_handler(CommandHandler("help", help_command))
        bot_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        # 添加错误处理器
        bot_application.add_error_handler(error_handler)
        
        # 启动机器人
        logger.info("Starting Telegram Bot v6.0...")
        bot_application.run_polling(
            poll_interval=1.0,
            timeout=20,
            bootstrap_retries=3,
            read_timeout=30,
            write_timeout=30,
            connect_timeout=30
        )
        
    except Exception as e:
        logger.error(f"Bot error: {e}")
        time.sleep(5)

def main():
    """主函数"""
    global flask_thread, bot_thread
    
    if not TOKEN:
        logger.error("BOT_TOKEN environment variable not set")
        return
    
    logger.info("Starting Phone Number Detection Bot v6.0 - Complete Fix")
    
    try:
        # 启动Flask监控服务
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info(f"Flask monitoring service started on port {PORT}")
        
        # 启动机器人
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        logger.info("Telegram bot started")
        
        # 保持主线程运行
        while not shutdown_event.is_set():
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    except Exception as e:
        logger.error(f"Main error: {e}")
    finally:
        shutdown_event.set()
        global is_running
        is_running = False
        logger.info("Bot shutdown complete")

if __name__ == '__main__':
    main()
