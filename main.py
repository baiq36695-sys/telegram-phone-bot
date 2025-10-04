#!/usr/bin/env python3
"""
电话号码重复检测机器人 - 修复版
兼容旧版本python-telegram-bot库
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
import subprocess

# 全局重启控制变量
RESTART_COUNT = 0
MAX_RESTARTS = 10
RESTART_DELAY = 5

# 导入Telegram库
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# 设置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 禁用不必要的HTTP日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# 全局变量
user_groups: Dict[int, Dict[str, Any]] = defaultdict(lambda: {
    'phones': set(),
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

def extract_phone_numbers(text: str) -> Set[str]:
    """从文本中提取电话号码"""
    patterns = [
        # 马来西亚电话号码
        r'\+60\s+1[0-9]\s*-?\s*\d{4}\s+\d{4}',
        r'\+60\s*1[0-9]\s*-?\s*\d{4}\s*-?\s*\d{4}',
        r'\+60\s*1[0-9]\d{7,8}',
        r'\+60\s*[3-9]\s*-?\s*\d{4}\s+\d{4}',
        r'\+60\s*[3-9]\d{7,8}',
        
        # 其他国际格式
        r'\+86\s*1[3-9]\d{9}',
        r'\+86\s*[2-9]\d{2,3}\s*\d{7,8}',
        r'\+1\s*[2-9]\d{2}\s*[2-9]\d{2}\s*\d{4}',
        r'\+44\s*[1-9]\d{8,9}',
        r'\+65\s*[6-9]\d{7}',
        r'\+852\s*[2-9]\d{7}',
        r'\+853\s*[6-9]\d{7}',
        r'\+886\s*[0-9]\d{8}',
        r'\+91\s*[6-9]\d{9}',
        r'\+81\s*[7-9]\d{8}',
        r'\+82\s*1[0-9]\d{7,8}',
        r'\+66\s*[6-9]\d{8}',
        r'\+84\s*[3-9]\d{8}',
        r'\+63\s*[2-9]\d{8}',
        r'\+62\s*[1-9]\d{7,10}',
        
        # 通用国际格式
        r'\+\d{1,4}\s*\d{1,4}\s*\d{1,4}\s*\d{1,9}',
        
        # 本地格式
        r'1[3-9]\d{9}',
        r'0[1-9]\d{1,3}[-\s]?\d{7,8}',
        r'01[0-9][-\s]?\d{4}[-\s]?\d{4}',
        r'0[3-9][-\s]?\d{4}[-\s]?\d{4}',
    ]
    
    phone_numbers = set()
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            cleaned = re.sub(r'\s+', ' ', match.strip())
            phone_numbers.add(cleaned)
    
    return phone_numbers

def find_duplicates(phones: Set[str]) -> Set[str]:
    """查找重复的电话号码"""
    normalized_map = {}
    duplicates = set()
    
    for phone in phones:
        normalized = re.sub(r'[^\d+]', '', phone)
        if normalized in normalized_map:
            duplicates.add(phone)
            duplicates.add(normalized_map[normalized])
        else:
            normalized_map[normalized] = phone
    
    return duplicates

def categorize_phone_number(phone: str) -> str:
    """识别电话号码的类型和国家"""
    clean_phone = re.sub(r'[^\d+]', '', phone)
    
    if re.match(r'\+60[1][0-9]', clean_phone):
        return "🇲🇾 马来西亚手机"
    elif re.match(r'\+60[3-9]', clean_phone):
        return "🇲🇾 马来西亚固话"
    elif re.match(r'\+86[1][3-9]', clean_phone):
        return "🇨🇳 中国手机"
    elif re.match(r'\+86[2-9]', clean_phone):
        return "🇨🇳 中国固话"
    elif re.match(r'\+1[2-9]', clean_phone):
        return "🇺🇸 美国/加拿大"
    elif re.match(r'\+65[6-9]', clean_phone):
        return "🇸🇬 新加坡"
    elif re.match(r'\+852[2-9]', clean_phone):
        return "🇭🇰 香港"
    elif re.match(r'\+853[6-9]', clean_phone):
        return "🇲🇴 澳门"
    elif re.match(r'\+886[0-9]', clean_phone):
        return "🇹🇼 台湾"
    elif re.match(r'\+91[6-9]', clean_phone):
        return "🇮🇳 印度"
    elif re.match(r'\+81[7-9]', clean_phone):
        return "🇯🇵 日本"
    elif re.match(r'\+82[1][0-9]', clean_phone):
        return "🇰🇷 韩国"
    elif re.match(r'\+66[6-9]', clean_phone):
        return "🇹🇭 泰国"
    elif re.match(r'\+84[3-9]', clean_phone):
        return "🇻🇳 越南"
    elif re.match(r'\+63[2-9]', clean_phone):
        return "🇵🇭 菲律宾"
    elif re.match(r'\+62[1-9]', clean_phone):
        return "🇮🇩 印度尼西亚"
    elif re.match(r'\+44[1-9]', clean_phone):
        return "🇬🇧 英国"
    elif re.match(r'^[1][3-9]\d{9}$', clean_phone):
        return "🇨🇳 中国手机（本地）"
    elif re.match(r'^0[1-9]', clean_phone):
        if len(clean_phone) >= 10:
            return "🇲🇾 马来西亚（本地）"
        else:
            return "🇨🇳 中国固话（本地）"
    else:
        return "🌍 其他国际号码"

# Telegram 命令处理器
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    start_message = """
🔍 **电话号码重复检测机器人**

🚀 **功能：**
• 智能重复检测
• 风险评估
• 多国格式支持

📱 **支持格式：**
• 马来西亚: +60 11-2896 2309
• 中国: +86 138 0013 8000
• 其他国际格式

🔧 **命令：**
/clear - 清除数据
/stats - 统计信息
/help - 帮助

直接发送电话号码开始检测！📞
    """
    await update.message.reply_text(start_message, parse_mode='Markdown')

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """清除数据"""
    user_id = update.effective_user.id
    chat_data = user_groups[user_id]
    
    if not chat_data['phones']:
        await update.message.reply_text("📭 没有需要清除的数据。")
        return
    
    cleared_count = len(chat_data['phones'])
    chat_data['phones'].clear()
    chat_data['phone_history'].clear()
    chat_data['risk_scores'].clear()
    chat_data['warnings_issued'].clear()
    chat_data['security_alerts'].clear()
    
    await update.message.reply_text(f"🗑️ 已清除 {cleared_count} 条记录")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """统计信息"""
    user_id = update.effective_user.id
    chat_data = user_groups[user_id]
    
    if not chat_data['phones']:
        await update.message.reply_text("📊 暂无统计数据")
        return
    
    total_phones = len(chat_data['phones'])
    duplicates = find_duplicates(chat_data['phones'])
    duplicate_count = len(duplicates)
    
    country_stats = defaultdict(int)
    for phone in chat_data['phones']:
        country = categorize_phone_number(phone)
        country_stats[country] += 1
    
    stats_message = f"""
📊 **统计报告**

**基本数据：**
• 唯一号码：{total_phones}
• 重复号码：{duplicate_count}
• 重复率：{(duplicate_count/total_phones*100):.1f}%

**地区分布：**
"""
    
    for country, count in sorted(country_stats.items(), key=lambda x: x[1], reverse=True):
        percentage = (count / total_phones) * 100
        stats_message += f"• {country}: {count} ({percentage:.1f}%)\n"
    
    await update.message.reply_text(stats_message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """帮助信息"""
    help_message = """
📖 **使用指南**

**功能：**
• 智能重复检测
• 国际格式支持

**支持格式：**
• 马来西亚: +60 11-2896 2309
• 中国: +86 138 0013 8000
• 美国: +1 555-123-4567

**命令：**
• /start - 欢迎信息
• /clear - 清除数据
• /stats - 统计信息
• /help - 显示帮助

直接发送电话号码开始检测！
    """
    await update.message.reply_text(help_message, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理普通消息"""
    user_id = update.effective_user.id
    message_text = update.message.text
    chat_data = user_groups[user_id]
    
    phone_numbers = extract_phone_numbers(message_text)
    
    if not phone_numbers:
        await update.message.reply_text(
            "❌ 未检测到有效的电话号码格式\n\n"
            "请发送支持的格式，例如：\n"
            "• +60 11-2896 2309\n"
            "• +86 138 0013 8000\n"
            "• +1 555-123-4567"
        )
        return
    
    chat_data['last_activity'] = datetime.datetime.now()
    
    new_phones = []
    duplicate_phones = []
    
    for phone in phone_numbers:
        chat_data['phone_history'].append({
            'phone': phone,
            'timestamp': datetime.datetime.now(),
            'message_id': update.message.message_id
        })
        
        if phone in chat_data['phones']:
            duplicate_phones.append(phone)
        else:
            new_phones.append(phone)
            chat_data['phones'].add(phone)
    
    response_message = f"🔍 **检测结果**\n\n"
    response_message += f"**概述：**\n"
    response_message += f"• 检测数量：{len(phone_numbers)}\n"
    response_message += f"• 新增号码：{len(new_phones)}\n"
    response_message += f"• 重复号码：{len(duplicate_phones)}\n"
    response_message += f"• 总计存储：{len(chat_data['phones'])}\n\n"
    
    for i, phone in enumerate(phone_numbers[:3], 1):
        category = categorize_phone_number(phone)
        status = "重复" if phone in duplicate_phones else "新增"
        response_message += f"**#{i}** {phone}\n"
        response_message += f"• 类型：{category}\n"
        response_message += f"• 状态：{status}\n\n"
    
    if len(phone_numbers) > 3:
        response_message += f"... 还有 {len(phone_numbers)-3} 个号码\n"
        response_message += "使用 /stats 查看完整统计\n\n"
    
    await update.message.reply_text(response_message, parse_mode='Markdown')

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """错误处理器"""
    logger.error(f"发生错误: {context.error}")
    if update and update.message:
        await update.message.reply_text("❌ 处理请求时发生错误，请稍后重试")

async def run_bot():
    """运行Telegram机器人 - 兼容版"""
    global bot_application, is_running
    
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("未找到TELEGRAM_BOT_TOKEN环境变量")
        return
    
    try:
        logger.info("🚀 启动兼容版机器人...")
        
        # 创建Application实例
        bot_application = Application.builder().token(bot_token).build()
        
        # 重要：确保删除任何可能的webhook设置
        await bot_application.bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ 已清除webhook设置")
        
        # 添加处理器
        bot_application.add_error_handler(error_handler)
        bot_application.add_handler(CommandHandler("start", start_command))
        bot_application.add_handler(CommandHandler("clear", clear_command))
        bot_application.add_handler(CommandHandler("stats", stats_command))
        bot_application.add_handler(CommandHandler("help", help_command))
        bot_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        is_running = True
        logger.info("✅ 机器人已启动，使用轮询模式")
        logger.info("✅ 自动重启功能已激活")
        
        # 使用简单的轮询模式参数 - 兼容旧版本
        await bot_application.run_polling(
            drop_pending_updates=True,
            close_loop=False,
            stop_signals=None
        )
        
    except Exception as e:
        logger.error(f"机器人运行错误: {e}")
        is_running = False
        raise e
    finally:
        is_running = False
        logger.info("机器人已停止运行")

def restart_application():
    """重启应用程序"""
    global RESTART_COUNT
    
    if RESTART_COUNT >= MAX_RESTARTS:
        logger.error(f"已达到最大重启次数 {MAX_RESTARTS}，程序退出")
        sys.exit(1)
        
    RESTART_COUNT += 1
    logger.info(f"🔄 准备重启应用 (第{RESTART_COUNT}次)...")
    
    time.sleep(RESTART_DELAY)
    
    try:
        python = sys.executable
        subprocess.Popen([python] + sys.argv)
        logger.info("✅ 重启命令已执行")
    except Exception as e:
        logger.error(f"❌ 重启失败: {e}")
    finally:
        sys.exit(0)

def signal_handler(signum, frame):
    """信号处理器 - 自动重启版"""
    logger.info(f"📶 收到信号 {signum}，正在关闭...")
    
    shutdown_event.set()
    
    global bot_application, is_running
    is_running = False
    
    if bot_application:
        try:
            logger.info("🛑 正在停止bot应用...")
        except Exception as e:
            logger.error(f"停止bot应用时出错: {e}")
    
    logger.info("🔄 准备自动重启...")
    restart_application()

def main():
    """主函数 - 兼容版"""
    global RESTART_COUNT
    
    logger.info("=" * 60)
    logger.info(f"📱 电话号码检测机器人 - 兼容版 (重启次数: {RESTART_COUNT})")
    logger.info("✅ 自动重启功能：已启用")
    logger.info("✅ HTTP服务器：已完全移除")
    logger.info("✅ Webhook模式：已禁用")
    logger.info("✅ 轮询模式：已启用")
    logger.info("✅ 库兼容性：修复超时参数错误")
    logger.info("=" * 60)
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 直接运行机器人
        logger.info("🚀 启动机器人...")
        asyncio.run(run_bot())
        
    except KeyboardInterrupt:
        logger.info("⌨️ 收到键盘中断信号")
        shutdown_event.set()
    except Exception as e:
        logger.error(f"💥 程序运行错误: {e}")
        restart_application()
    
    logger.info("🔚 程序正在关闭...")

if __name__ == '__main__':
    main()
