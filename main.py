#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTML电话号码重复检测机器人
版本: v8.1 - 静默优化版
增强功能：
1. 简化日志输出（清爽控制台）
2. 保留所有美化功能
3. 更好的用户体验
"""

import logging
import re
import os
import threading
from html import unescape
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask

# 配置简化的日志 - 只显示重要信息
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.WARNING  # 只显示警告和错误，隐藏详细HTTP请求
)

# 进一步简化第三方库的日志
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# 机器人Token - 请替换为您的实际Token
BOT_TOKEN = "8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU"

# 更全面的电话号码匹配模式
PHONE_PATTERNS = [
    r'\+\d{1,4}[\s-]*\d{1,4}[\s-]*\d{3,4}[\s-]*\d{3,4}',  # 国际格式
    r'\+60\s*1[0-9][\s-]*\d{3}[\s-]+\d{4}',  # 马来西亚手机号格式
    r'\b1[3-9]\d{9}\b',  # 中国手机号
    r'\b\d{3}[\s-]*\d{3}[\s-]*\d{4}\b',  # 美国格式
    r'\b\d{2,4}[\s-]*\d{6,8}\b',  # 其他常见格式
]

def extract_phone_numbers(text):
    """从文本中提取电话号码"""
    text = unescape(text)
    phone_numbers = set()
    
    for pattern in PHONE_PATTERNS:
        matches = re.findall(pattern, text)
        phone_numbers.update(matches)
    
    return phone_numbers

def normalize_phone_number(phone):
    """标准化电话号码用于比较"""
    # 保留数字和开头的+号
    normalized = re.sub(r'[^\d+]', '', phone)
    # 如果没有+号开头，添加+号
    if not normalized.startswith('+'):
        normalized = '+' + normalized
    return normalized

def get_phone_type_emoji(phone):
    """根据电话号码类型返回对应表情"""
    if phone.startswith('+60'):
        return "🇲🇾"  # 马来西亚
    elif phone.startswith('+86') or (phone.startswith('+') and phone[1:].startswith('1') and len(phone) == 12):
        return "🇨🇳"  # 中国
    elif phone.startswith('+1'):
        return "🇺🇸"  # 美国/加拿大
    elif phone.startswith('+44'):
        return "🇬🇧"  # 英国
    elif phone.startswith('+81'):
        return "🇯🇵"  # 日本
    elif phone.startswith('+82'):
        return "🇰🇷"  # 韩国
    else:
        return "🌍"  # 其他国家

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/start命令"""
    welcome_msg = """
🌟 ═══════════════════════════ 🌟
        📱 智能电话号码管理系统        
🌟 ═══════════════════════════ 🌟

🚀 版本: v8.1 - 静默优化版

✨ 【核心功能】
🔍 智能识别电话号码
🛡️ 精准重复检测
🌍 支持国际号码格式
📊 实时统计分析

🎯 【操作指南】
📩 发送包含电话号码的消息
🗑️ /clear - 清空所有记录
📈 /stats - 查看详细统计
💡 /help - 获取帮助信息
🎨 /about - 关于本机器人

🎨 【特色亮点】
⚡ 实时处理，毫秒响应
🎭 智能表情，生动直观
🌈 彩色界面，赏心悦目
🔒 数据安全，隐私保护
🤫 静默运行，控制台清爽

════════════════════════════════
🎈 现在发送您的电话号码，开始体验吧！
════════════════════════════════
"""
    await update.message.reply_text(welcome_msg)

async def clear_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """清除所有存储的电话号码"""
    context.user_data.clear()
    
    clear_msg = """
🧹 ═══════ 数据清理完成 ═══════ 🧹

✅ 所有电话号码记录已清除
✅ 统计数据已重置
✅ 系统状态已恢复初始化

🆕 您现在可以重新开始录入电话号码了！

════════════════════════════════
"""
    await update.message.reply_text(clear_msg)

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示统计信息"""
    if 'phones' not in context.user_data:
        stats_msg = """
📊 ═══════ 统计报告 ═══════ 📊

📭 当前状态：无记录数据
🎯 建议：发送包含电话号码的消息开始使用

════════════════════════════════
"""
        await update.message.reply_text(stats_msg)
        return
    
    phones = context.user_data.get('phones', set())
    normalized_phones = context.user_data.get('normalized_phones', set())
    
    # 按国家分类统计
    country_stats = {}
    for phone in phones:
        emoji = get_phone_type_emoji(phone)
        country_stats[emoji] = country_stats.get(emoji, 0) + 1
    
    country_breakdown = ""
    for emoji, count in sorted(country_stats.items(), key=lambda x: x[1], reverse=True):
        country_breakdown += f"      {emoji} {count} 个号码\n"
    
    stats_msg = f"""
📊 ═══════ 统计报告 ═══════ 📊

📈 【总体数据】
   📞 总记录号码：{len(phones)} 个
   🔒 唯一号码：{len(normalized_phones)} 个
   ⏰ 统计时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}

🌍 【地区分布】
{country_breakdown}
🏆 【系统状态】
   ✅ 运行正常
   ⚡ 响应迅速
   🛡️ 数据安全
   🤫 静默运行

════════════════════════════════
"""
    await update.message.reply_text(stats_msg)

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示帮助信息"""
    help_msg = """
💡 ═══════ 帮助中心 ═══════ 💡

🎯 【基本使用】
   • 直接发送包含电话号码的文本
   • 支持多种格式：+86 138xxxx, +60 13-xxx等
   • 自动识别并分类新/重复号码

🛠️【命令列表】
   /start - 🏠 返回主页
   /clear - 🗑️ 清空所有记录
   /stats - 📊 查看统计信息
   /help - 💡 显示此帮助
   /about - ℹ️ 关于机器人

🌟 【支持格式】
   • 国际格式：+86 138 0013 8000
   • 带分隔符：+60 13-970 3144
   • 本地格式：13800138000
   • 美式格式：(555) 123-4567

🔥 【智能特性】
   • 🎭 自动国家识别
   • ⚡ 秒级重复检测
   • 🌈 可视化结果展示
   • 🔒 隐私数据保护
   • 🤫 静默运行模式

════════════════════════════════
"""
    await update.message.reply_text(help_msg)

async def show_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示关于信息"""
    about_msg = """
ℹ️ ═══════ 关于我们 ═══════ ℹ️

🤖 【机器人信息】
   名称：智能电话号码管理系统
   版本：v8.1 静默优化版
   开发：MiniMax Agent

⭐ 【核心技术】
   • Python + Telegram Bot API
   • 正则表达式引擎
   • 智能去重算法
   • 实时数据处理

🌟 【设计理念】
   • 简单易用，功能强大
   • 美观界面，用户至上
   • 数据安全，隐私第一
   • 持续改进，追求完美

🎨 【界面设计】
   • 丰富表情符号
   • 清晰结构布局
   • 动态视觉反馈
   • 个性化体验

🆕 【v8.1新特性】
   • 🤫 静默运行模式
   • 🧹 清爽控制台输出
   • ⚡ 优化响应速度
   • 🛡️ 增强稳定性

💌 感谢使用！如有建议，欢迎反馈！

════════════════════════════════
"""
    await update.message.reply_text(about_msg)

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理包含电话号码的消息"""
    try:
        message_text = update.message.text
        
        # 初始化用户数据
        if 'phones' not in context.user_data:
            context.user_data['phones'] = set()
            context.user_data['normalized_phones'] = set()
        
        # 提取电话号码
        phone_numbers = extract_phone_numbers(message_text)
        
        if not phone_numbers:
            no_phone_msg = """
❌ ═══════ 识别结果 ═══════ ❌

🔍 扫描结果：未检测到电话号码

💡 请确保您的消息包含有效的电话号码格式：
   • +86 138 0013 8000
   • +60 13-970 3144
   • (555) 123-4567
   • 13800138000

🎯 提示：支持多种国际格式！

════════════════════════════════
"""
            await update.message.reply_text(no_phone_msg)
            return
        
        # 获取已存储的数据
        chat_data = context.user_data
        
        # 分类电话号码：新号码和重复号码
        new_phones = set()
        duplicate_phones = set()
        
        # 检查每个电话号码
        for phone in phone_numbers:
            normalized = normalize_phone_number(phone)
            if normalized in chat_data['normalized_phones']:
                duplicate_phones.add(phone)
            else:
                new_phones.add(phone)
                chat_data['phones'].add(phone)
                chat_data['normalized_phones'].add(normalized)
        
        # 构建美化的回复消息
        response_parts = []
        response_parts.append("🎯 ═══════ 处理结果 ═══════ 🎯\n")
        
        if new_phones:
            response_parts.append(f"✨ 【新发现号码】({len(new_phones)} 个)")
            for phone in sorted(new_phones):
                emoji = get_phone_type_emoji(phone)
                response_parts.append(f"   {emoji} 📞 {phone}")
            response_parts.append("")
        
        if duplicate_phones:
            response_parts.append(f"⚠️ 【重复号码警告】({len(duplicate_phones)} 个)")
            for phone in sorted(duplicate_phones):
                emoji = get_phone_type_emoji(phone)
                response_parts.append(f"   {emoji} 🔄 {phone}")
            response_parts.append("")
        
        # 添加统计信息
        total_count = len(chat_data['phones'])
        if total_count <= 5:
            level_emoji = "🌱"
            level_name = "新手"
        elif total_count <= 20:
            level_emoji = "🌿"
            level_name = "进阶"
        elif total_count <= 50:
            level_emoji = "🌳"
            level_name = "专业"
        else:
            level_emoji = "🏆"
            level_name = "大师"
        
        response_parts.append(f"📊 【当前统计】")
        response_parts.append(f"   📈 总记录：{total_count} 个号码")
        response_parts.append(f"   {level_emoji} 等级：{level_name}")
        response_parts.append(f"   ⏰ 时间：{datetime.now().strftime('%H:%M')}")
        
        response_parts.append("\n════════════════════════════════")
        
        await update.message.reply_text("\n".join(response_parts))
        
    except Exception as e:
        logger.error(f"处理消息时发生错误: {e}")
        error_msg = """
❌ ═══════ 系统错误 ═══════ ❌

🚨 处理过程中发生错误
🔧 系统正在自动修复
⏳ 请稍后重试

💡 如问题持续，请联系技术支持

════════════════════════════════
"""
        await update.message.reply_text(error_msg)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """全局错误处理"""
    logger.error(f"Bot error: {context.error}")

# Flask应用（用于健康检查）
app = Flask(__name__)

# 禁用Flask的访问日志
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

@app.route('/')
@app.route('/health')
def health_check():
    """健康检查端点"""
    return """
    <html>
    <head><title>📱 电话号码管理机器人</title></head>
    <body style="font-family: Arial; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white;">
        <h1>🤖 机器人运行正常！</h1>
        <p>✅ 版本: v8.1 静默优化版</p>
        <p>⚡ 状态: 在线服务中</p>
        <p>🌟 功能: 智能电话号码管理</p>
        <p>🤫 模式: 静默运行</p>
    </body>
    </html>
    """, 200

def run_flask():
    """在后台线程运行Flask"""
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def main():
    """主函数"""
    try:
        # 在后台线程启动Flask服务器
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print(f"🤫 系统启动中... 端口: {os.environ.get('PORT', 10000)}")
        
        # 创建Telegram应用
        application = Application.builder().token(BOT_TOKEN).build()
        
        # 添加处理器
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("clear", clear_data))
        application.add_handler(CommandHandler("stats", show_stats))
        application.add_handler(CommandHandler("help", show_help))
        application.add_handler(CommandHandler("about", show_about))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message))
        
        # 添加错误处理器
        application.add_error_handler(error_handler)
        
        print("🚀 机器人启动成功 - v8.1 静默优化版")
        print("🤫 静默模式：控制台将保持清爽")
        
        # 启动机器人（主线程）
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == "__main__":
    main()
