#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTML电话号码重复检测机器人
版本: v7.2 - Flask兼容版
修复内容：
1. 修复重复检测逻辑bug
2. 解决线程问题
3. 使用Flask代替aiohttp（已在requirements.txt中）
"""

import logging
import re
import os
import threading
from html import unescape
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/start命令"""
    await update.message.reply_text(
        "📱 HTML电话号码重复检测机器人\n"
        "🔧 版本: v7.2 - Flask兼容版\n\n"
        "功能说明：\n"
        "• 发送包含电话号码的文本，我会检测重复\n"
        "• 使用 /clear 清除所有记录\n"
        "• 使用 /stats 查看统计信息\n\n"
        "现在您可以发送包含电话号码的消息了！"
    )

async def clear_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """清除所有存储的电话号码"""
    context.user_data.clear()
    await update.message.reply_text("🗑️ 所有电话号码记录已清除！")

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示统计信息"""
    if 'phones' not in context.user_data:
        await update.message.reply_text("📊 暂无记录数据")
        return
    
    total_phones = len(context.user_data.get('phones', set()))
    await update.message.reply_text(f"📊 统计信息\n已记录电话号码: {total_phones} 个")

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
            await update.message.reply_text("❌ 未检测到电话号码")
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
        
        # 构建回复消息
        response_parts = []
        
        if new_phones:
            response_parts.append(f"✅ 新电话号码 ({len(new_phones)} 个):")
            for phone in sorted(new_phones):
                response_parts.append(f"  📞 {phone}")
        
        if duplicate_phones:
            response_parts.append(f"⚠️ 重复电话号码 ({len(duplicate_phones)} 个):")
            for phone in sorted(duplicate_phones):
                response_parts.append(f"  🔄 {phone}")
        
        response_parts.append(f"\n📊 总计已记录: {len(chat_data['phones'])} 个电话号码")
        
        await update.message.reply_text("\n".join(response_parts))
        
    except Exception as e:
        logger.error(f"处理消息时发生错误: {e}")
        await update.message.reply_text("❌ 处理消息时发生错误，请重试")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """全局错误处理"""
    logger.error(f"Bot error: {context.error}")

# Flask应用（用于健康检查）
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health_check():
    """健康检查端点"""
    return "Bot is running!", 200

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
        logger.info(f"Flask服务器启动在端口 {os.environ.get('PORT', 10000)}")
        
        # 创建Telegram应用
        application = Application.builder().token(BOT_TOKEN).build()
        
        # 添加处理器
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("clear", clear_data))
        application.add_handler(CommandHandler("stats", show_stats))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message))
        
        # 添加错误处理器
        application.add_error_handler(error_handler)
        
        logger.info("机器人启动成功 - v7.2 Flask兼容版")
        
        # 启动机器人（主线程）
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == "__main__":
    main()
