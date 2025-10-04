#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
完整功能版本 v9.6 - 保留v9.5所有功能 + v9.6修复
结合v9.5的完整功能和v9.6的关键修复
"""

import os
import re
import logging
import threading
import time
import sys
import traceback
import asyncio
import signal
import nest_asyncio  # 解决嵌套事件循环问题
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask

# 应用nest_asyncio，解决事件循环冲突
nest_asyncio.apply()

# 配置日志 - 使用INFO级别，避免DEBUG性能问题
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 设置第三方库日志级别
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# 从环境变量获取Bot Token - 修复硬编码问题
BOT_TOKEN = os.getenv('BOT_TOKEN', os.getenv('TELEGRAM_BOT_TOKEN'))

if not BOT_TOKEN:
    logger.error("❌ 未找到BOT_TOKEN环境变量")
    sys.exit(1)

# 全局重启计数器和状态 - 添加线程锁
state_lock = threading.Lock()  # 解决竞态条件

restart_count = 0
start_time = datetime.now(timezone.utc)
is_shutting_down = False
received_sigterm = False

# 完整的国家代码到国旗的映射（v9.5版本）
COUNTRY_FLAGS = {
    '1': '🇺🇸',     # 美国/加拿大
    '44': '🇬🇧',    # 英国
    '33': '🇫🇷',    # 法国
    '49': '🇩🇪',    # 德国
    '39': '🇮🇹',    # 意大利
    '34': '🇪🇸',    # 西班牙
    '7': '🇷🇺',     # 俄罗斯
    '81': '🇯🇵',    # 日本
    '82': '🇰🇷',    # 韩国
    '86': '🇨🇳',    # 中国
    '852': '🇭🇰',   # 香港
    '853': '🇲🇴',   # 澳门
    '886': '🇹🇼',   # 台湾
    '65': '🇸🇬',    # 新加坡
    '60': '🇲🇾',    # 马来西亚
    '66': '🇹🇭',    # 泰国
    '91': '🇮🇳',    # 印度
    '55': '🇧🇷',    # 巴西
    '52': '🇲🇽',    # 墨西哥
    '61': '🇦🇺',    # 澳大利亚
    '64': '🇳🇿',    # 新西兰
    '90': '🇹🇷',    # 土耳其
    '98': '🇮🇷',    # 伊朗
    '966': '🇸🇦',   # 沙特阿拉伯
    '971': '🇦🇪',   # 阿联酋
    '92': '🇵🇰',    # 巴基斯坦
    '880': '🇧🇩',   # 孟加拉国
    '94': '🇱🇰',    # 斯里兰卡
    '95': '🇲🇲',    # 缅甸
    '84': '🇻🇳',    # 越南
    '62': '🇮🇩',    # 印度尼西亚
    '63': '🇵🇭',    # 菲律宾
    '20': '🇪🇬',    # 埃及
    '27': '🇿🇦',    # 南非
    '234': '🇳🇬',   # 尼日利亚
    '254': '🇰🇪',   # 肯尼亚
    '256': '🇺🇬',   # 乌干达
    '233': '🇬🇭',   # 加纳
    '213': '🇩🇿',   # 阿尔及利亚
    '212': '🇲🇦'    # 摩洛哥
}

def normalize_phone(phone):
    """规范化电话号码，去除所有非数字字符"""
    return re.sub(r'\D', '', phone)

def get_country_code(phone):
    """获取电话号码的国家代码"""
    clean_phone = normalize_phone(phone)
    
    if phone.strip().startswith('+'):
        clean_phone = clean_phone
    else:
        if len(clean_phone) == 11 and clean_phone.startswith('1'):
            return '86'  # 中国
        elif len(clean_phone) == 10 and clean_phone.startswith(('2', '3', '4', '5', '6', '7', '8', '9')):
            return '1'   # 美国/加拿大
    
    for code_length in [4, 3, 2, 1]:
        if len(clean_phone) >= code_length:
            country_code = clean_phone[:code_length]
            if country_code in COUNTRY_FLAGS:
                return country_code
    
    return 'Unknown'

def get_country_flag(phone):
    """获取电话号码对应的国家国旗"""
    country_code = get_country_code(phone)
    return COUNTRY_FLAGS.get(country_code, '🌐')

def format_datetime(dt):
    """格式化日期时间为易读格式"""
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def get_user_level_emoji(user_id):
    """根据用户ID生成等级表情"""
    levels = ['👤', '⭐', '🌟', '💎', '👑', '🔥', '⚡', '🚀']
    return levels[user_id % len(levels)]

def calculate_uptime():
    """计算运行时间"""
    current_time = datetime.now(timezone.utc)
    uptime = current_time - start_time
    
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if days > 0:
        return f"{days}天 {hours}小时 {minutes}分钟"
    elif hours > 0:
        return f"{hours}小时 {minutes}分钟"
    else:
        return f"{minutes}分钟 {seconds}秒"

# Flask应用
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    uptime = calculate_uptime()
    return f"Phone Bot v9.6 (完整功能版) is alive! 🚀<br>Uptime: {uptime}<br>Restarts: {restart_count}", 200

@flask_app.route('/status')
def status():
    uptime = datetime.now(timezone.utc) - start_time
    return {
        'status': 'ok',
        'uptime_seconds': int(uptime.total_seconds()),
        'restart_count': restart_count,
        'version': 'v9.6-完整功能版',
        'uptime_text': calculate_uptime()
    }

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"Flask服务器启动，端口: {port}")
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# 机器人命令处理
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始命令处理"""
    user = update.effective_user
    level_emoji = get_user_level_emoji(user.id)
    uptime = calculate_uptime()
    
    welcome_message = f"""
🎉 **电话号码查重机器人 v9.6** 🎉
═══════════════════════════

👋 欢迎，{level_emoji} **{user.full_name}**！

🔍 **功能特点：**
• 智能去重检测
• 实时时间显示  
• 用户追踪系统
• 重复次数统计
• 国家识别标识
• 📊 完整统计功能
• 🔄 稳定自动重启
• 🛡️ 终极修复版本

📱 **使用方法：**
直接发送电话号码给我，我会帮您检查是否重复！

✨ **运行状态：**
• ⏰ 运行时间：{uptime}
• 🔄 重启次数：{restart_count}

**命令列表：**
• `/help` - 快速帮助
• `/stats` - 查看详细统计
• `/clear` - 清空数据库

═══════════════════════════
🚀 开始发送电话号码吧！
"""
    
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """帮助命令处理"""
    help_message = f"""
🆘 **快速帮助** - v9.6
═══════════════════════════

📋 **可用命令：**
• `/start` - 完整功能介绍
• `/help` - 快速帮助（本页面）
• `/stats` - 详细统计信息
• `/clear` - 清空数据库

📱 **使用方法：**
直接发送电话号码给我即可自动检测！

⭐ **新功能：**
• 🔄 自动重启保护
• ⏰ 实时时间戳显示  
• 📊 完整统计系统
• 🛡️ 终极修复版本

═══════════════════════════
💡 直接发送号码开始使用！
"""
    
    await update.message.reply_text(help_message, parse_mode='Markdown')

async def check_phone_duplicate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """检查电话号码是否重复"""
    try:
        message_text = update.message.text.strip()
        user = update.effective_user
        current_time = datetime.now(timezone.utc)
        
        # 检查消息是否包含电话号码
        phone_pattern = r'[\+]?[\d\s\-\(\)]{8,}'
        phone_matches = re.findall(phone_pattern, message_text)
        
        if not phone_matches:
            return
        
        # 初始化聊天数据
        if 'phone_database' not in context.chat_data:
            context.chat_data['phone_database'] = {}
        
        phone_database = context.chat_data['phone_database']
        user_level = get_user_level_emoji(user.id)
        
        for phone_match in phone_matches:
            phone_match = phone_match.strip()
            normalized_phone = normalize_phone(phone_match)
            
            # 检查是否为有效电话号码
            if len(normalized_phone) < 8:
                continue
            
            country_flag = get_country_flag(phone_match)
            
            if normalized_phone in phone_database:
                # 发现重复号码
                phone_info = phone_database[normalized_phone]
                phone_info['count'] += 1
                
                # 记录重复用户信息
                if 'duplicate_users' not in phone_info:
                    phone_info['duplicate_users'] = []
                
                duplicate_info = {
                    'user_id': user.id,
                    'user_name': user.full_name,
                    'detection_time': current_time,
                    'original_number': phone_match
                }
                phone_info['duplicate_users'].append(duplicate_info)
                
                # 构建回复消息
                first_user_level = get_user_level_emoji(phone_info['first_user_info']['id'])
                
                duplicate_message = f"""
🚨 **发现重复号码！** 🚨
═══════════════════════════

{country_flag} **号码：** `{phone_match}`

📅 **首次添加：** {format_datetime(phone_info['first_seen_time'])}
👤 **首次用户：** {first_user_level} {phone_info['first_user_info']['name']}

⏰ **当前检测：** {format_datetime(current_time)}
👤 **当前用户：** {user_level} {user.full_name}

📊 **统计信息：**
🔢 总重复次数：**{phone_info['count']}** 次
👥 涉及用户：**{len(set([phone_info['first_user_info']['id']] + [dup['user_id'] for dup in phone_info['duplicate_users']]))}** 人

═══════════════════════════
⚠️ 请注意：此号码已被使用过！
"""
                
                await update.message.reply_text(duplicate_message, parse_mode='Markdown')
                
            else:
                # 首次添加号码
                phone_database[normalized_phone] = {
                    'first_seen_time': current_time,
                    'first_user_info': {
                        'id': user.id,
                        'name': user.full_name
                    },
                    'count': 1,
                    'original_number': phone_match,
                    'duplicate_users': []
                }
                
                success_message = f"""
✅ **号码已记录！** ✅
═══════════════════════════

{country_flag} **号码：** `{phone_match}`

📅 **添加时间：** {format_datetime(current_time)}
👤 **添加用户：** {user_level} {user.full_name}

🎯 **状态：** 首次添加，无重复！

═══════════════════════════
✨ 号码已成功加入数据库！
"""
                
                await update.message.reply_text(success_message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"处理消息时出错: {e}")
        logger.error(f"错误详情: {traceback.format_exc()}")
        await update.message.reply_text(
            "❌ 处理消息时出现错误，请稍后重试。",
            parse_mode='Markdown'
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示详细统计信息"""
    if 'phone_database' not in context.chat_data:
        await update.message.reply_text("📊 暂无数据记录。")
        return
    
    phone_database = context.chat_data['phone_database']
    total_numbers = len(phone_database)
    total_duplicates = sum(1 for info in phone_database.values() if info['count'] > 1)
    unique_numbers = total_numbers - total_duplicates
    
    # 统计国家分布
    country_stats = {}
    for info in phone_database.values():
        country_code = get_country_code(info['original_number'])
        country_flag = get_country_flag(info['original_number'])
        country_key = f"{country_flag} {country_code}"
        country_stats[country_key] = country_stats.get(country_key, 0) + 1
    
    # 按数量排序
    sorted_countries = sorted(country_stats.items(), key=lambda x: x[1], reverse=True)
    top_countries = sorted_countries[:5]  # 显示前5名
    
    country_text = ""
    if top_countries:
        country_text = "\n🌍 **国家分布（Top 5）：**\n"
        for country, count in top_countries:
            country_text += f"• {country}: {count} 个号码\n"
    
    # 计算总重复次数
    total_repeat_count = sum(info['count'] for info in phone_database.values())
    
    uptime = calculate_uptime()
    
    stats_message = f"""
📊 **数据库完整统计** 📊
═══════════════════════════

📱 **号码统计：**
• 总记录数：**{total_numbers}** 个
• 重复号码：**{total_duplicates}** 个
• 唯一号码：**{unique_numbers}** 个
• 总重复次数：**{total_repeat_count}** 次

{country_text}

⚙️ **运行状态：**
• ⏰ 运行时间：{uptime}
• 🔄 重启次数：{restart_count}
• 📅 启动时间：{format_datetime(start_time)}

═══════════════════════════
💡 使用 `/clear` 清空数据库
"""
    
    await update.message.reply_text(stats_message, parse_mode='Markdown')

async def clear_database(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """清空数据库"""
    old_count = len(context.chat_data.get('phone_database', {}))
    context.chat_data['phone_database'] = {}
    
    clear_message = f"""
🗑️ **数据库已清空！** 🗑️
═══════════════════════════

📊 **清理统计：**
• 已删除：**{old_count}** 条记录
• 当前状态：数据库为空
• 清理时间：{format_datetime(datetime.now(timezone.utc))}

═══════════════════════════
✨ 可以重新开始记录号码了！
"""
    
    await update.message.reply_text(clear_message, parse_mode='Markdown')

# 错误处理回调 - 解决静默失败问题
async def error_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理所有错误"""
    logger.error(f"🚨 Update {update} caused error {context.error}")
    logger.error(f"错误详情: {traceback.format_exc()}")
    
    # 如果是用户消息引起的错误，发送友好提示
    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ 处理您的消息时遇到问题，请稍后重试。"
            )
        except Exception as e:
            logger.error(f"发送错误消息失败: {e}")

def create_application():
    """创建Telegram应用程序 - 终极修复版"""
    logger.info("开始创建应用程序...")
    
    try:
        # 完整的网络超时配置 - 解决网络阻塞问题
        application = (
            Application.builder()
            .token(BOT_TOKEN)
            .connect_timeout(30)          # 连接超时
            .read_timeout(30)             # 读取超时 
            .write_timeout(30)            # 写入超时
            .get_updates_connect_timeout(30)  # 获取更新连接超时
            .get_updates_read_timeout(30)     # 获取更新读取超时
            .get_updates_write_timeout(30)    # 获取更新写入超时
            .pool_timeout(30)             # 连接池超时
            .build()
        )
        
        # 注册所有处理器
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("clear", clear_database))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_phone_duplicate))
        
        # 添加错误处理器 - 关键修复
        application.add_error_handler(error_callback)
        
        logger.info("应用程序创建成功，处理器已注册")
        return application
        
    except Exception as e:
        logger.error(f"创建应用程序失败: {e}")
        logger.error(f"详细错误: {traceback.format_exc()}")
        raise e

def setup_signal_handlers():
    """设置信号处理器 - 线程安全版本"""
    def sigterm_handler(signum, frame):
        global received_sigterm
        with state_lock:  # 线程安全
            logger.info(f"收到SIGTERM信号({signum})，优雅关闭当前实例...")
            received_sigterm = True
    
    def sigint_handler(signum, frame):
        global is_shutting_down
        with state_lock:  # 线程安全
            logger.info(f"收到SIGINT信号({signum})，用户手动终止程序...")
            is_shutting_down = True
    
    signal.signal(signal.SIGTERM, sigterm_handler)
    signal.signal(signal.SIGINT, sigint_handler)

async def run_bot():
    """运行机器人主程序 - 终极修复版"""
    global is_shutting_down, received_sigterm
    
    application = None
    heartbeat_task = None
    
    try:
        logger.info("🔄 开始运行机器人...")
        
        # 创建应用程序
        application = create_application()
        logger.info(f"🎯 电话号码查重机器人 v9.6 启动成功！重启次数: {restart_count}")
        
        # 心跳监控 - 改进版
        async def heartbeat():
            count = 0
            while True:
                # 检查状态，如果需要停止则退出
                with state_lock:
                    if is_shutting_down or received_sigterm:
                        logger.info("💓 心跳监控收到停止信号，退出")
                        break
                        
                await asyncio.sleep(300)  # 每5分钟
                count += 1
                logger.info(f"💓 心跳检查 #{count} - 机器人运行正常")
        
        # 启动心跳任务
        heartbeat_task = asyncio.create_task(heartbeat())
        
        # 初始化和启动 - 增强错误处理
        logger.info("🚀 开始初始化应用程序...")
        await application.initialize()
        
        logger.info("🚀 开始启动应用程序...")
        await application.start()
        
        logger.info("🚀 开始轮询...")
        
        # 启动轮询 - 完全避免webhook冲突
        await application.updater.start_polling(
            drop_pending_updates=True,    # 丢弃待处理更新
            timeout=30,                   # 轮询超时
            bootstrap_retries=3,          # 重试次数
        )
        
        logger.info("✅ 轮询已启动，机器人正在监听消息...")
        
        # 改进的等待循环 - 防止立即退出
        while True:
            with state_lock:
                if is_shutting_down or received_sigterm:
                    break
            
            # 短暂等待，允许其他任务运行
            await asyncio.sleep(0.1)
                
        # 确定退出原因
        with state_lock:
            if received_sigterm:
                logger.info("🔄 收到SIGTERM，准备重启...")
            else:
                logger.info("🛑 收到停止信号，准备退出...")
                
    except Exception as e:
        logger.error(f"运行机器人时出错: {e}")
        logger.error(f"详细错误: {traceback.format_exc()}")
        raise e
    finally:
        # 完整的资源清理 - 防止阻塞
        logger.info("🧹 开始清理资源...")
        
        # 取消心跳任务
        if heartbeat_task and not heartbeat_task.done():
            heartbeat_task.cancel()
            try:
                await asyncio.wait_for(heartbeat_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        
        # 清理应用程序
        if application:
            try:
                # 设置较短的超时，避免阻塞
                logger.info("🧹 停止updater...")
                await asyncio.wait_for(application.updater.stop(), timeout=5.0)
                
                logger.info("🧹 停止application...")
                await asyncio.wait_for(application.stop(), timeout=5.0)
                
                logger.info("🧹 关闭application...")
                await asyncio.wait_for(application.shutdown(), timeout=5.0)
                
                logger.info("✅ 应用程序已优雅关闭")
            except asyncio.TimeoutError:
                logger.warning("⚠️ 资源清理超时，强制退出")
            except Exception as e:
                logger.error(f"关闭时出错: {e}")

def main():
    """主函数 - 终极修复版"""
    global restart_count, is_shutting_down, received_sigterm
    
    logger.info("=== 电话号码查重机器人 v9.6 启动 (完整功能版) ===")
    logger.info(f"启动时间: {format_datetime(start_time)}")
    
    # 设置信号处理器
    setup_signal_handlers()
    
    # 启动Flask服务器
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"Flask服务器启动，端口: {port}")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask服务器线程已启动")
    
    # 自动重启循环 - 改进版
    max_restarts = 30      # 增加最大重启次数
    base_delay = 2         # 减少基础延迟
    consecutive_failures = 0
    
    while restart_count < max_restarts:
        # 检查是否需要退出
        with state_lock:
            if is_shutting_down:
                logger.info("收到全局停止信号，退出主循环")
                break
        
        try:
            restart_count += 1
            with state_lock:
                received_sigterm = False  # 重置SIGTERM标志
                
            logger.info(f"=== 第 {restart_count} 次启动机器人 ===")
            
            # 运行机器人
            asyncio.run(run_bot())
            
            # 如果到达这里说明正常退出或收到SIGTERM
            with state_lock:
                if received_sigterm:
                    logger.info("🔄 收到SIGTERM信号，准备重启...")
                    consecutive_failures = 0  # SIGTERM不算失败
                    # 短暂延迟，让资源完全释放
                    time.sleep(1)
                else:
                    logger.warning("机器人正常退出")
                    consecutive_failures = 0
            
        except KeyboardInterrupt:
            logger.info("🛑 收到键盘中断，程序正常退出")
            with state_lock:
                is_shutting_down = True
            break
            
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"=== Bot异常停止 （第{restart_count}次） ===")
            logger.error(f"异常类型: {type(e).__name__}")
            logger.error(f"异常信息: {e}")
            logger.error(f"连续失败: {consecutive_failures} 次")
            logger.error(f"详细堆栈: {traceback.format_exc()}")
            
            if restart_count >= max_restarts:
                logger.error(f"已达到最大重启次数 ({max_restarts})，程序退出")
                break
            
            if consecutive_failures >= 5:
                logger.error("连续失败次数过多，程序退出")
                break
            
            # 动态延迟
            if consecutive_failures <= 2:
                delay = base_delay
            else:
                delay = min(base_delay * (2 ** (consecutive_failures - 1)), 30)  # 最多30秒
            
            logger.info(f"⏱️ 等待 {delay} 秒后重启...")
            time.sleep(delay)
    
    logger.info("🏁 程序已退出")

if __name__ == "__main__":
    main()
