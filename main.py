#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电话号码重复检测机器人 - Render兼容版
版本: v3.3 - 兼容旧版本python-telegram-bot
最后更新: 2025-10-05
"""

import os
import logging
import re
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional

# Telegram相关导入 - 兼容旧版本
try:
    from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    USING_OLD_VERSION = True
except ImportError:
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    USING_OLD_VERSION = False

# =============================================================================
# 配置和常量
# =============================================================================

# 日志配置
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Telegram Bot Token
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU')

# 全局数据存储
phone_data = {}  # {chat_id: {phone: count}}
user_stats = defaultdict(lambda: {'total_phones': 0, 'duplicates_found': 0, 'last_activity': datetime.now()})
bot_stats = {
    'start_time': datetime.now(),
    'total_messages': 0,
    'total_duplicates': 0,
    'total_users': 0
}

# =============================================================================
# 电话号码处理功能
# =============================================================================

def normalize_phone(phone_str: str) -> Optional[str]:
    """
    标准化电话号码 - 修复版
    支持中国大陆、马来西亚等多种格式
    """
    if not phone_str or not isinstance(phone_str, str):
        return None
    
    # 去除所有非数字字符
    clean_phone = re.sub(r'[^\d]', '', phone_str.strip())
    
    if not clean_phone:
        return None
    
    # 中国大陆手机号码处理
    if len(clean_phone) == 11 and clean_phone.startswith('1'):
        # 验证中国手机号格式
        if re.match(r'^1[3-9]\d{9}$', clean_phone):
            return f"+86{clean_phone}"
    
    # 中国大陆手机号码带86前缀
    elif len(clean_phone) == 13 and clean_phone.startswith('86'):
        mobile = clean_phone[2:]
        if len(mobile) == 11 and mobile.startswith('1'):
            if re.match(r'^1[3-9]\d{9}$', mobile):
                return f"+86{mobile}"
    
    # 马来西亚手机号码
    elif len(clean_phone) == 10 and clean_phone.startswith('01'):
        # 马来西亚手机号格式: 01X-XXXXXXX
        if re.match(r'^01[0-9]\d{7}$', clean_phone):
            return f"+60{clean_phone}"
    
    # 马来西亚手机号码带60前缀
    elif len(clean_phone) == 12 and clean_phone.startswith('60'):
        mobile = clean_phone[2:]
        if len(mobile) == 10 and mobile.startswith('01'):
            if re.match(r'^01[0-9]\d{7}$', mobile):
                return f"+60{mobile}"
    
    # 马来西亚手机号码带6前缀但缺少0
    elif len(clean_phone) == 11 and clean_phone.startswith('601'):
        mobile = clean_phone[2:]  # 去掉60前缀
        if len(mobile) == 9 and mobile.startswith('1'):
            # 补充缺失的0，形成完整的马来西亚号码
            complete_mobile = '0' + mobile
            if re.match(r'^01[0-9]\d{7}$', complete_mobile):
                return f"+60{complete_mobile}"
    
    # 国际格式处理
    elif len(clean_phone) > 7:  # 最短国际号码长度
        # 如果以+开头，保持原格式
        if phone_str.strip().startswith('+'):
            return phone_str.strip()
        # 否则假设为完整国际号码
        else:
            return f"+{clean_phone}"
    
    # 其他情况返回None
    return None

def extract_phones_from_text(text: str) -> List[str]:
    """从文本中提取所有可能的电话号码"""
    if not text:
        return []
    
    # 多种电话号码模式
    patterns = [
        r'\+?86\s*1[3-9]\d{9}',  # 中国手机号
        r'\+?60\s*1[0-9]\d{7,8}',  # 马来西亚手机号
        r'\b1[3-9]\d{9}\b',  # 纯中国手机号
        r'\b01[0-9]\d{7}\b',  # 纯马来西亚手机号
        r'\+\d{1,4}\s?\d{6,14}',  # 国际格式
        r'\b\d{10,15}\b'  # 通用数字串
    ]
    
    found_phones = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        found_phones.extend(matches)
    
    # 标准化并去重
    normalized_phones = []
    for phone in found_phones:
        normalized = normalize_phone(phone)
        if normalized and normalized not in normalized_phones:
            normalized_phones.append(normalized)
    
    return normalized_phones

def cleanup_old_data():
    """清理超过24小时的旧数据"""
    current_time = datetime.now()
    cutoff_time = current_time - timedelta(hours=24)
    
    # 清理用户统计数据
    users_to_remove = []
    for user_id, stats in user_stats.items():
        if stats['last_activity'] < cutoff_time:
            users_to_remove.append(user_id)
    
    for user_id in users_to_remove:
        del user_stats[user_id]
    
    # 清理电话数据
    chats_to_remove = []
    for chat_id in phone_data:
        if chat_id not in user_stats:
            chats_to_remove.append(chat_id)
    
    for chat_id in chats_to_remove:
        del phone_data[chat_id]
    
    logger.info(f"清理完成: 移除 {len(users_to_remove)} 个过期用户数据")

# =============================================================================
# Telegram机器人处理函数 - 兼容新旧版本
# =============================================================================

def start_command(update, context):
    """处理/start命令"""
    chat_id = update.effective_chat.id
    bot_stats['total_users'] = len(set(list(user_stats.keys()) + [chat_id]))
    
    welcome_text = """🤖 **电话号码重复检测机器人** 
    
✨ **功能特点:**
• 🔍 智能检测重复电话号码
• 🌍 支持多国格式 (中国🇨🇳 马来西亚🇲🇾)
• ⚡ 实时处理和警告
• 📊 详细统计信息
• 🛡️ 完全隐私保护

📋 **使用方法:**
直接发送包含电话号码的消息，我会自动检测重复项

🎯 **支持命令:**
/start - 显示帮助信息
/stats - 查看统计数据
/clear - 清空当前数据
/help - 获取帮助

现在就开始发送电话号码吧！"""
    
    update.message.reply_text(welcome_text, parse_mode='Markdown')

def help_command(update, context):
    """处理/help命令"""
    help_text = """📚 **详细使用说明**

🔢 **支持的号码格式:**
• 中国: +86 138XXXXXXXX 或 138XXXXXXXX
• 马来西亚: +60 1XXXXXXXX 或 01XXXXXXXX
• 国际: +[国家码][号码]

⚡ **检测功能:**
• 自动识别消息中的所有电话号码
• 实时检测重复项并发出警告
• 支持混合格式文本处理

📊 **统计功能:**
• /stats - 查看个人统计
• 显示处理总数、重复数量等

🔧 **管理功能:**
• /clear - 清空当前聊天的所有数据
• 数据自动清理(24小时)

💡 **使用技巧:**
• 可以一次发送多个号码
• 支持各种分隔符(空格、逗号、换行)
• 自动过滤无效号码"""
    
    update.message.reply_text(help_text, parse_mode='Markdown')

def stats_command(update, context):
    """处理/stats命令"""
    chat_id = update.effective_chat.id
    user_stat = user_stats[chat_id]
    
    # 计算运行时间
    uptime = datetime.now() - bot_stats['start_time']
    hours = int(uptime.total_seconds() // 3600)
    minutes = int((uptime.total_seconds() % 3600) // 60)
    
    # 当前聊天的电话号码统计
    current_phones = phone_data.get(chat_id, {})
    unique_phones = len(current_phones)
    total_entries = sum(current_phones.values())
    duplicates_in_current = sum(1 for count in current_phones.values() if count > 1)
    
    stats_text = f"""📊 **统计报告**

👤 **个人统计:**
• 处理号码总数: {user_stat['total_phones']}
• 发现重复项: {user_stat['duplicates_found']}
• 最后活动: {user_stat['last_activity'].strftime('%H:%M:%S')}

💾 **当前会话数据:**
• 唯一号码: {unique_phones}
• 总记录数: {total_entries}
• 重复号码: {duplicates_in_current}

🤖 **机器人全局统计:**
• 运行时间: {hours}小时 {minutes}分钟
• 处理消息: {bot_stats['total_messages']}
• 发现重复: {bot_stats['total_duplicates']}
• 活跃用户: {bot_stats['total_users']}

🕐 统计时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
    
    update.message.reply_text(stats_text, parse_mode='Markdown')

def clear_command(update, context):
    """处理/clear命令"""
    chat_id = update.effective_chat.id
    
    # 创建确认按钮
    keyboard = [
        [
            InlineKeyboardButton("✅ 确认清空", callback_data="clear_confirm"),
            InlineKeyboardButton("❌ 取消", callback_data="clear_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    current_count = len(phone_data.get(chat_id, {}))
    update.message.reply_text(
        f"⚠️ **确认清空数据**\n\n当前存储了 {current_count} 个电话号码\n\n确定要清空所有数据吗？此操作无法撤销。",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

def button_callback(update, context):
    """处理按钮回调"""
    query = update.callback_query
    query.answer()
    
    chat_id = query.effective_chat.id
    
    if query.data == "clear_confirm":
        # 清空数据
        if chat_id in phone_data:
            del phone_data[chat_id]
        if chat_id in user_stats:
            user_stats[chat_id] = {
                'total_phones': 0, 
                'duplicates_found': 0, 
                'last_activity': datetime.now()
            }
        
        query.edit_message_text(
            "✅ **数据清空完成**\n\n所有电话号码数据已清空，可以重新开始检测。",
            parse_mode='Markdown'
        )
    
    elif query.data == "clear_cancel":
        query.edit_message_text(
            "❌ **操作已取消**\n\n数据保持不变，继续使用检测功能。",
            parse_mode='Markdown'
        )

def handle_message(update, context):
    """处理包含电话号码的消息"""
    chat_id = update.effective_chat.id
    message_text = update.message.text
    
    # 更新统计
    bot_stats['total_messages'] += 1
    user_stats[chat_id]['last_activity'] = datetime.now()
    
    # 提取电话号码
    phones = extract_phones_from_text(message_text)
    
    if not phones:
        update.message.reply_text(
            "🔍 未检测到有效的电话号码\n\n请发送包含电话号码的消息，支持中国🇨🇳和马来西亚🇲🇾格式。"
        )
        return
    
    # 初始化聊天数据
    if chat_id not in phone_data:
        phone_data[chat_id] = {}
    
    # 处理检测到的号码
    new_phones = []
    duplicate_phones = []
    
    for phone in phones:
        user_stats[chat_id]['total_phones'] += 1
        
        if phone in phone_data[chat_id]:
            phone_data[chat_id][phone] += 1
            duplicate_phones.append(phone)
            user_stats[chat_id]['duplicates_found'] += 1
            bot_stats['total_duplicates'] += 1
        else:
            phone_data[chat_id][phone] = 1
            new_phones.append(phone)
    
    # 生成回复消息
    response_parts = []
    
    if new_phones:
        response_parts.append(f"✅ **新增号码** ({len(new_phones)}个):")
        for phone in new_phones:
            response_parts.append(f"• {phone}")
    
    if duplicate_phones:
        response_parts.append(f"\n⚠️ **发现重复** ({len(duplicate_phones)}个):")
        for phone in duplicate_phones:
            count = phone_data[chat_id][phone]
            response_parts.append(f"• {phone} (第{count}次)")
    
    # 添加统计信息
    total_unique = len(phone_data[chat_id])
    total_processed = len(phones)
    response_parts.append(f"\n📊 本次处理: {total_processed} | 累计唯一: {total_unique}")
    
    response_text = "\n".join(response_parts)
    update.message.reply_text(response_text, parse_mode='Markdown')
    
    # 定期清理数据
    if bot_stats['total_messages'] % 100 == 0:
        cleanup_old_data()

# =============================================================================
# 主程序
# =============================================================================

def main():
    """主函数"""
    try:
        print("🤖 电话号码重复检测机器人 - 兼容版启动中...")
        
        # 验证BOT_TOKEN
        if not BOT_TOKEN or BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
            logger.error("❌ BOT_TOKEN未设置或无效")
            return
        
        print(f"🕐 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("📊 功能状态:")
        print("   ✅ 重复检测 - 已启用")
        print("   ✅ 多格式支持 - 已启用") 
        print("   ✅ 实时警告 - 已启用")
        print("   ✅ 详细统计 - 已启用")
        print("   ✅ 版本兼容 - 已优化")
        
        if USING_OLD_VERSION:
            print("📦 使用旧版本 python-telegram-bot")
            # 旧版本使用 Updater
            updater = Updater(BOT_TOKEN, use_context=True)
            dispatcher = updater.dispatcher
            
            # 注册处理器
            dispatcher.add_handler(CommandHandler("start", start_command))
            dispatcher.add_handler(CommandHandler("help", help_command))
            dispatcher.add_handler(CommandHandler("stats", stats_command))
            dispatcher.add_handler(CommandHandler("clear", clear_command))
            dispatcher.add_handler(CallbackQueryHandler(button_callback))
            dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
            
            print("✅ 机器人启动成功！")
            print("🎯 机器人现在完全可用，兼容旧版本！")
            print("🚀 开始接收消息...")
            
            # 启动机器人
            updater.start_polling()
            updater.idle()
            
        else:
            print("📦 使用新版本 python-telegram-bot")
            # 新版本使用 Application
            application = Application.builder().token(BOT_TOKEN).build()
            
            # 注册处理器
            application.add_handler(CommandHandler("start", start_command))
            application.add_handler(CommandHandler("help", help_command))
            application.add_handler(CommandHandler("stats", stats_command))
            application.add_handler(CommandHandler("clear", clear_command))
            application.add_handler(CallbackQueryHandler(button_callback))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            
            print("✅ 机器人启动成功！")
            print("🎯 机器人现在完全可用，兼容新版本！")
            print("🚀 开始接收消息...")
            
            # 启动机器人
            application.run_polling()
        
    except Exception as e:
        logger.error(f"启动机器人时出错: {e}")
        print(f"❌ 启动失败: {e}")
        raise

if __name__ == '__main__':
    main()
