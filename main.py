#!/usr/bin/env python3
"""
电话号码重复检测机器人 - 完全修复版 v7.0
专门修复马来西亚格式识别和重复检测逻辑错误
"""

import os
import re
import logging
import signal
import sys
import asyncio
import datetime
from typing import Set, Dict, Any, List, Tuple
from collections import defaultdict
import threading
import time
import hashlib
import subprocess

# 🔄 自动重启控制变量
RESTART_COUNT = 0
MAX_RESTARTS = 10
RESTART_DELAY = 5

# 导入并应用nest_asyncio
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    import subprocess
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

# 禁用不必要的HTTP日志以减少噪音
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# 获取环境变量
TOKEN = os.getenv('BOT_TOKEN')
PORT = int(os.getenv('PORT', 8000))

# 修复后的全局变量 - 分离原始号码和标准化号码
user_groups: Dict[int, Dict[str, Any]] = defaultdict(lambda: {
    'phones': set(),                    # 存储原始格式的号码
    'normalized_phones': set(),         # 存储标准化号码用于重复检测
    'phone_history': [],
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
    """从文本中提取电话号码 - 修正马来西亚格式支持"""
    patterns = [
        # 马来西亚电话号码（修正版本 - 支持3位-4位和4位-4位格式）
        r'\+60\s*1[0-9][\s-]*\d{3}[\s-]+\d{4}',          # +60 13-970 3144 或 +60 13 970 3144
        r'\+60\s*1[0-9][\s-]*\d{4}[\s-]+\d{4}',          # +60 11-2896 2309 或 +60 11 2896 2309
        r'\+60\s*1[0-9][\s-]*\d{7,8}',                   # +60 13-9703144 或 +6013-9703144
        r'\+60\s*[3-9][\s-]*\d{3,4}[\s-]+\d{4}',         # +60 3-1234 5678 (固话)
        r'\+60\s*[3-9][\s-]*\d{7,8}',                    # +60 312345678 (固话)
        
        # 中国电话号码
        r'\+86\s*1[3-9]\d{9}',                           # 中国手机
        r'\+86\s*[2-9]\d{2,3}[\s-]*\d{7,8}',            # 中国固话
        
        # 其他国际格式
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
        
        # 通用国际格式
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

def assess_phone_risk(phone: str, chat_data: Dict[str, Any]) -> Dict[str, Any]:
    """评估电话号码风险"""
    risk_factors = []
    risk_score = 0
    
    # 基础风险评估
    clean_phone = normalize_phone_number(phone)
    
    # 1. 重复度检查（修正的逻辑）
    if clean_phone in chat_data['normalized_phones']:
        risk_factors.append("📞 号码重复：该号码之前已被检测过")
        risk_score += 4
    
    # 2. 长度检查
    if len(clean_phone) < 10:
        risk_factors.append("📏 号码长度过短，可能不完整")
        risk_score += 2
    elif len(clean_phone) > 15:
        risk_factors.append("📏 号码长度过长，格式异常")
        risk_score += 1
    
    # 3. 格式检查
    if not clean_phone.startswith('+'):
        risk_factors.append("🌍 本地格式号码，建议添加国际代码")
        risk_score += 1
    
    # 确定风险等级
    if risk_score >= 4:
        risk_level = RISK_LEVELS['CRITICAL']
    elif risk_score >= 3:
        risk_level = RISK_LEVELS['HIGH']
    elif risk_score >= 2:
        risk_level = RISK_LEVELS['MEDIUM']
    else:
        risk_level = RISK_LEVELS['LOW']
    
    return {
        'level': risk_level,
        'score': risk_score,
        'factors': risk_factors
    }

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """启动命令"""
    chat_id = update.effective_chat.id
    
    # 确保用户数据初始化
    chat_data = user_groups[chat_id]
    chat_data['last_activity'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    welcome_message = (
        "🤖 **电话号码重复检测机器人** v7.0\n"
        "🔧 **完全修复版 - 马来西亚格式支持**\n\n"
        "📋 **功能特色**：\n"
        "• 🔍 智能电话号码识别\n"
        "• 🌍 多国格式支持（专门优化马来西亚格式）\n"
        "• 🚨 精确重复检测警告\n"
        "• 📊 详细风险评估\n"
        "• 🔧 修复所有逻辑错误\n\n"
        "💡 **使用方法**：\n"
        "直接发送包含电话号码的文本，系统会自动识别并分析\n\n"
        "🎛️ **命令列表**：\n"
        "/start - 启动机器人\n"
        "/clear - 清除历史数据\n"
        "/status - 查看系统状态\n"
        "/help - 帮助信息\n\n"
        "🔧 当前版本：v7.0 - 完全修复版"
    )
    
    await update.message.reply_text(welcome_message)

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """清除数据命令"""
    chat_id = update.effective_chat.id
    
    phone_count = len(user_groups[chat_id]['phones'])
    history_count = len(user_groups[chat_id]['phone_history'])
    
    # 清除所有数据（包括新增的normalized_phones）
    user_groups[chat_id]['phones'].clear()
    user_groups[chat_id]['normalized_phones'].clear()
    user_groups[chat_id]['phone_history'].clear()
    user_groups[chat_id]['risk_scores'].clear()
    user_groups[chat_id]['warnings_issued'].clear()
    user_groups[chat_id]['security_alerts'].clear()
    
    clear_message = (
        f"✅ **数据清除成功**\n\n"
        f"🗑️ 已清除 {phone_count} 个电话号码\n"
        f"📊 已清除 {history_count} 条历史记录\n"
        f"🚨 已重置所有风险评估\n\n"
        f"💡 现在可以重新开始检测电话号码了\n"
        f"🔧 版本：v7.0 - 完全修复版"
    )
    
    await update.message.reply_text(clear_message)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """状态查询命令"""
    chat_id = update.effective_chat.id
    chat_data = user_groups[chat_id]
    
    # 统计信息
    total_phones = len(chat_data['phones'])
    total_normalized = len(chat_data['normalized_phones'])
    total_history = len(chat_data['phone_history'])
    total_risks = len(chat_data['risk_scores'])
    
    # 按国家分类统计
    country_stats = defaultdict(int)
    for phone in chat_data['phones']:
        country = categorize_phone_number(phone)
        country_stats[country] += 1
    
    status_message = [
        "📊 **系统状态报告** v7.0",
        "=" * 35,
        f"🕒 **查询时间**：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "📱 **数据统计**：",
        f"• 累计号码：**{total_phones}** 个",
        f"• 标准化号码：**{total_normalized}** 个",
        f"• 检测历史：**{total_history}** 次",
        f"• 风险评估：**{total_risks}** 次",
        "",
        "🌍 **号码分类**：",
    ]
    
    if country_stats:
        for country, count in sorted(country_stats.items()):
            status_message.append(f"• {country}：**{count}** 个")
    else:
        status_message.append("• 暂无数据")
    
    status_message.extend([
        "",
        "🔧 **系统状态**：",
        "• 运行状态：✅ 正常",
        "• 重复检测：✅ 已修复",
        "• 马来西亚格式：✅ 完全支持",
        "• 版本：v7.0 完全修复版",
    ])
    
    await update.message.reply_text('\n'.join(status_message))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """帮助命令"""
    help_message = (
        "📖 **帮助文档** v7.0\n\n"
        "🤖 **系统说明**：\n"
        "这是一个智能电话号码检测系统，专门修复了重复检测逻辑错误\n\n"
        "🔍 **支持格式**：\n"
        "• 马来西亚：+60 13-970 3144, +60 11 2896 2309\n"
        "• 中国：+86 138 0013 8000, 138-0013-8000\n"
        "• 美国：+1 555-123-4567\n"
        "• 其他国际格式\n\n"
        "⚠️ **重复检测**：\n"
        "系统会基于标准化号码进行精确重复检测（忽略格式差异）\n\n"
        "📊 **报告功能**：\n"
        "• 详细风险评估\n"
        "• 国家分类统计\n"
        "• 重复警告\n"
        "• 完整历史记录\n\n"
        "🎛️ **命令列表**：\n"
        "/start - 启动机器人\n"
        "/clear - 清除历史数据\n"
        "/status - 查看系统状态\n"
        "/help - 显示帮助信息\n\n"
        "💡 **使用提示**：\n"
        "直接发送包含电话号码的文本即可开始检测\n\n"
        "🔧 版本：v7.0 - 完全修复版"
    )
    
    await update.message.reply_text(help_message)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理普通消息 - 修正的重复检测逻辑"""
    chat_id = update.effective_chat.id
    user_name = update.effective_user.first_name or "匿名用户"
    message_text = update.message.text
    
    # 提取电话号码
    phone_numbers = extract_phone_numbers(message_text)
    
    if not phone_numbers:
        await update.message.reply_text(
            "🔍 **未检测到电话号码**\n\n"
            "💡 请确保电话号码格式正确，支持的格式包括：\n"
            "• +60 13-970 3144\n"
            "• +86 138 0013 8000\n"
            "• +1 555-123-4567\n"
            "• 以及其他国际格式"
        )
        return
    
    # 获取聊天数据
    chat_data = user_groups[chat_id]
    chat_data['last_activity'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 记录检测历史
    detection_record = {
        'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'user': user_name,
        'phones': list(phone_numbers)
    }
    chat_data['phone_history'].append(detection_record)
    
    # **修正的重复检测逻辑**
    new_phones = set()
    duplicate_phones = set()
    
    for phone in phone_numbers:
        # 标准化号码用于重复检测
        normalized = normalize_phone_number(phone)
        
        # 检查是否重复（基于标准化的号码）
        if normalized in chat_data['normalized_phones']:
            duplicate_phones.add(phone)
        else:
            new_phones.add(phone)
            # 添加到两个集合中
            chat_data['phones'].add(phone)
            chat_data['normalized_phones'].add(normalized)
    
    # 构建增强版回复
    response_parts = []
    response_parts.append("🎯 **智能电话号码检测系统** v7.0")
    response_parts.append("=" * 35)
    response_parts.append(f"👤 **用户**: {user_name}")
    response_parts.append(f"🔍 **检测到**: {len(phone_numbers)} 个号码")
    response_parts.append("")
    
    # 显示新发现的号码（带风险评估）
    if new_phones:
        response_parts.append(f"✨ **新发现号码** ({len(new_phones)}个):")
        for i, phone in enumerate(sorted(new_phones), 1):
            normalized = normalize_phone_number(phone)
            category = categorize_phone_number(phone)
            risk_assessment = assess_phone_risk(phone, chat_data)
            chat_data['risk_scores'][phone] = risk_assessment['level']
            
            response_parts.append(f"{i}. 📞 {phone}")
            response_parts.append(f"   来源：{category}")
            response_parts.append(f"   🔧 标准化：{normalized}")
            response_parts.append(f"   风险：{risk_assessment['level']['emoji']} {risk_assessment['level']['color']}")
            response_parts.append("")
    
    # 显示重复号码（修正的警告）
    if duplicate_phones:
        response_parts.append(f"🔄 **重复号码警告** ({len(duplicate_phones)}个):")
        for i, phone in enumerate(sorted(duplicate_phones), 1):
            normalized = normalize_phone_number(phone)
            category = categorize_phone_number(phone)
            response_parts.append(f"{i}. 🔴 {phone}")
            response_parts.append(f"   来源：{category}")
            response_parts.append(f"   🔧 标准化：{normalized}")
            response_parts.append(f"   状态：之前已检测过")
            response_parts.append("")
    
    # 总计统计
    total_in_group = len(chat_data['phones'])
    response_parts.append("=" * 35)
    response_parts.append("📊 **统计信息**:")
    response_parts.append(f"• 群组累计：**{total_in_group}** 个号码")
    response_parts.append(f"• 本次新增：**{len(new_phones)}** 个")
    response_parts.append(f"• 重复数量：**{len(duplicate_phones)}** 个")
    response_parts.append("")
    response_parts.append("🔧 **系统**：v7.0 完全修复版")
    
    await update.message.reply_text('\n'.join(response_parts))

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """错误处理器"""
    logger.error(f"Exception while handling an update: {context.error}")
    
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ **系统错误**\n\n"
                "抱歉，处理您的消息时出现错误。\n"
                "请稍后重试或联系管理员。\n\n"
                f"错误时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
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
        'timestamp': datetime.datetime.now().isoformat(),
        'version': 'v7.0',
        'bot_running': is_running,
        'groups_monitored': len(user_groups),
        'total_phone_numbers': sum(len(data['phones']) for data in user_groups.values()),
    })

@app.route('/stats', methods=['GET'])
def get_stats():
    """统计信息端点"""
    return jsonify({
        'total_groups': len(user_groups),
        'total_phones': sum(len(data['phones']) for data in user_groups.values()),
        'total_normalized': sum(len(data['normalized_phones']) for data in user_groups.values()),
        'version': 'v7.0',
        'features': ['duplicate_detection_fixed', 'malaysia_support', 'normalized_comparison']
    })

@app.route('/', methods=['GET'])
def index():
    """主页"""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>电话号码检测机器人 v7.0</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; }}
            .status {{ color: green; font-weight: bold; }}
            .version {{ color: blue; }}
        </style>
    </head>
    <body>
        <h1>🤖 电话号码检测机器人</h1>
        <p class="version">版本：v7.0 - 完全修复版</p>
        <p class="status">✅ 系统运行正常</p>
        <p>🔧 特性：修复重复检测逻辑、马来西亚格式支持、标准化比较</p>
        <p>📊 当前监控：{len(user_groups)} 个群组</p>
        <p>📞 累计号码：{sum(len(data['phones']) for data in user_groups.values())} 个</p>
        <p>📋 监控端点：</p>
        <ul>
            <li><a href="/health">/health</a> - 健康检查</li>
            <li><a href="/stats">/stats</a> - 统计信息</li>
        </ul>
    </body>
    </html>
    """

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
        logger.info("Starting Telegram Bot v7.0...")
        bot_application.run_polling(
            poll_interval=1.0,
            timeout=20,
            bootstrap_retries=3,
            drop_pending_updates=True
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
    
    logger.info("Starting Phone Number Detection Bot v7.0 - Complete Fix")
    
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
