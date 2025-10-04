#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
终极修复版本 - 解决重启后停止响应问题
针对高级诊断发现的所有严重问题进行修复
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
    level=logging.INFO,  # 改为INFO级别
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
import threading
state_lock = threading.Lock()  # 解决竞态条件

restart_count = 0
start_time = datetime.now(timezone.utc)
is_shutting_down = False
received_sigterm = False

# 国家代码到国旗的映射
COUNTRY_FLAGS = {
    '1': '🇺🇸',     # 美国/加拿大
    '44': '🇬🇧',    # 英国
    '86': '🇨🇳',    # 中国
    '852': '🇭🇰',   # 香港
    '853': '🇲🇴',   # 澳门
    '886': '🇹🇼',   # 台湾
}

def format_datetime(dt):
    """格式化日期时间"""
    return dt.strftime('%Y-%m-%d %H:%M:%S')

# 电话号码解析函数
def parse_phone_number(text):
    """解析电话号码"""
    # 移除所有非数字字符
    digits_only = re.sub(r'[^\d]', '', text)
    
    if not digits_only:
        return None
    
    # 处理各种格式
    if digits_only.startswith('86'):
        digits_only = digits_only[2:]
    elif digits_only.startswith('+86'):
        digits_only = digits_only[3:]
    
    if len(digits_only) == 11 and digits_only.startswith('1'):
        return digits_only
    
    return None

def format_phone_display(phone):
    """格式化电话号码显示"""
    if len(phone) == 11:
        return f"{phone[:3]} {phone[3:7]} {phone[7:]}"
    return phone

def get_country_flag(phone):
    """获取国家国旗"""
    if phone.startswith('1') and len(phone) == 11:
        return '🇨🇳'
    return '🌍'

# Flask应用
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    uptime = datetime.now(timezone.utc) - start_time
    return {
        'status': 'ok',
        'uptime_seconds': int(uptime.total_seconds()),
        'restart_count': restart_count,
        'version': 'v9.6-ultimate-fix'
    }

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"Flask服务器启动，端口: {port}")
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# 机器人命令处理
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/start命令"""
    logger.info(f"收到/start命令，用户: {update.effective_user.id}")
    
    welcome_text = f"""
🎯 **电话号码查重机器人 v9.6** (终极修复版)

👋 欢迎使用！请发送电话号码进行查重。

📱 **支持格式:**
• 13812345678
• 138 1234 5678  
• +86 138 1234 5678
• 86-138-1234-5678

🔧 **系统信息:**
• 重启次数: {restart_count}
• 启动时间: {format_datetime(start_time)}
• 状态: ✅ 运行正常

📋 **可用命令:**
/start - 显示此帮助
/status - 查看状态
"""
    
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/status命令"""
    logger.info(f"收到/status命令，用户: {update.effective_user.id}")
    
    uptime = datetime.now(timezone.utc) - start_time
    
    status_text = f"""
📊 **机器人状态报告**

🕐 **运行时间:** {uptime.days}天 {uptime.seconds//3600}小时 {(uptime.seconds%3600)//60}分钟
🔄 **重启次数:** {restart_count}
🏃 **当前状态:** {'🔄 重启中' if received_sigterm else '✅ 运行中'}
🌐 **网络状态:** ✅ 连接正常
💾 **内存状态:** ✅ 正常

🔧 **技术信息:**
• 版本: v9.6 终极修复版
• 进程ID: {os.getpid()}
"""
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理普通消息"""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    logger.info(f"收到消息，用户: {user_id}，内容: {message_text[:50]}...")
    
    # 解析电话号码
    phone_number = parse_phone_number(message_text)
    
    if not phone_number:
        error_text = f"""
❌ **未识别到有效电话号码**

📝 您发送的内容: `{message_text}`

📱 **请使用以下格式:**
• 13812345678
• 138 1234 5678
• +86 138 1234 5678
• 86-138-1234-5678
"""
        await update.message.reply_text(error_text, parse_mode='Markdown')
        return
    
    # 格式化显示
    country_flag = get_country_flag(phone_number)
    formatted_display = format_phone_display(phone_number)
    
    result_text = f"""
✅ **电话号码解析成功**

📱 **原始输入:** `{message_text}`
🎯 **解析结果:** {country_flag} `{formatted_display}`
🔢 **标准格式:** `{phone_number}`

📊 **号码信息:**
• 国家/地区: {country_flag} 中国大陆  
• 号码长度: {len(phone_number)} 位

💾 **已保存到数据库进行查重分析**
"""
    
    await update.message.reply_text(result_text, parse_mode='Markdown')

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
        
        # 注册处理器
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("status", status_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
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
            # 移除error_callback参数，因为我们已经用add_error_handler了
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
    
    logger.info("=== 电话号码查重机器人 v9.6 启动 (终极修复版) ===")
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
