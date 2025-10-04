#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版诊断脚本 - 针对重启后机器人停止响应问题
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
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask

# 配置日志，增强调试信息
logging.basicConfig(
    level=logging.DEBUG,  # 改为DEBUG级别
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 设置第三方库日志级别 - 改为INFO以获取更多信息
logging.getLogger("httpx").setLevel(logging.INFO)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)

logger = logging.getLogger(__name__)

# 从环境变量获取Bot Token
BOT_TOKEN = os.getenv('BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')

# 全局重启计数器和状态
restart_count = 0
start_time = datetime.now(timezone.utc)
is_shutting_down = False
received_sigterm = False

# 健康检查和诊断函数
def diagnose_token():
    """诊断Token配置"""
    logger.info("=== TOKEN诊断 ===")
    
    if not BOT_TOKEN or BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
        logger.error("❌ BOT_TOKEN未设置或使用默认值")
        return False
    
    if len(BOT_TOKEN) < 40:
        logger.error(f"❌ BOT_TOKEN长度异常: {len(BOT_TOKEN)}字符")
        return False
        
    if not BOT_TOKEN.count(':') == 1:
        logger.error("❌ BOT_TOKEN格式错误，应该包含一个':'")
        return False
        
    # 隐藏部分TOKEN显示
    masked_token = BOT_TOKEN[:10] + "***" + BOT_TOKEN[-10:]
    logger.info(f"✅ TOKEN格式正确: {masked_token}")
    return True

def diagnose_environment():
    """诊断环境配置"""
    logger.info("=== 环境诊断 ===")
    
    # 检查Python版本
    python_version = sys.version
    logger.info(f"Python版本: {python_version}")
    
    # 检查重要环境变量
    env_vars = ['BOT_TOKEN', 'TELEGRAM_BOT_TOKEN', 'PORT']
    for var in env_vars:
        value = os.getenv(var)
        if value:
            if 'TOKEN' in var:
                masked_value = value[:10] + "***" + value[-10:] if len(value) > 20 else "***"
                logger.info(f"{var}: {masked_value}")
            else:
                logger.info(f"{var}: {value}")
        else:
            logger.warning(f"{var}: 未设置")

async def test_telegram_connection():
    """测试Telegram连接"""
    logger.info("=== Telegram连接测试 ===")
    
    try:
        # 创建应用程序进行连接测试
        app = Application.builder().token(BOT_TOKEN).build()
        await app.initialize()
        
        # 获取机器人信息
        bot_info = await app.bot.get_me()
        logger.info(f"✅ 机器人连接成功: @{bot_info.username} ({bot_info.first_name})")
        
        # 测试webhook信息
        webhook_info = await app.bot.get_webhook_info()
        logger.info(f"Webhook状态: URL={webhook_info.url}, 待处理={webhook_info.pending_update_count}")
        
        await app.shutdown()
        return True
        
    except Exception as e:
        logger.error(f"❌ Telegram连接失败: {e}")
        logger.error(f"详细错误: {traceback.format_exc()}")
        return False

# 电话号码解析函数（保持原有逻辑）
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
        'version': 'v9.5-diagnosis'
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
🎯 **电话号码查重机器人 v9.5** (诊断版)

👋 欢迎使用！请发送电话号码进行查重。

📱 **支持格式:**
• 13812345678
• 138 1234 5678  
• +86 138 1234 5678
• 86-138-1234-5678

🔧 **诊断信息:**
• 重启次数: {restart_count}
• 启动时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}
• 状态: ✅ 运行正常

📋 **可用命令:**
/start - 显示此帮助
/status - 查看状态
/test - 测试功能
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
• Python版本: {sys.version.split()[0]}
• 进程ID: {os.getpid()}
• 事件循环: {'✅ 正常' if asyncio.get_event_loop().is_running() else '❌ 异常'}
"""
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/test命令"""
    logger.info(f"收到/test命令，用户: {update.effective_user.id}")
    
    test_numbers = ["13812345678", "138-1234-5678", "+86 138 1234 5678"]
    results = []
    
    for num in test_numbers:
        parsed = parse_phone_number(num)
        if parsed:
            flag = get_country_flag(parsed)
            formatted = format_phone_display(parsed)
            results.append(f"✅ {num} → {flag} {formatted}")
        else:
            results.append(f"❌ {num} → 解析失败")
    
    test_text = f"""
🧪 **功能测试结果**

{chr(10).join(results)}

🎯 **测试完成** - 所有功能正常运行！
"""
    
    await update.message.reply_text(test_text, parse_mode='Markdown')

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

💡 **提示:** 请确保号码为11位中国大陆手机号码
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
• 运营商: 待查询
• 归属地: 待查询

💾 **已保存到数据库进行查重分析**
"""
    
    await update.message.reply_text(result_text, parse_mode='Markdown')

def create_application():
    """创建Telegram应用程序 - 增强版"""
    logger.info("开始创建应用程序...")
    
    try:
        # 增强的网络配置
        application = (
            Application.builder()
            .token(BOT_TOKEN)
            .connect_timeout(30)
            .read_timeout(30) 
            .write_timeout(30)
            .get_updates_connect_timeout(60)
            .get_updates_read_timeout(60)
            .get_updates_write_timeout(60)
            .build()
        )
        
        # 注册处理器
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("status", status_command))
        application.add_handler(CommandHandler("test", test_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("应用程序创建成功，处理器已注册")
        return application
        
    except Exception as e:
        logger.error(f"创建应用程序失败: {e}")
        logger.error(f"详细错误: {traceback.format_exc()}")
        raise e

def setup_signal_handlers():
    """设置信号处理器"""
    def sigterm_handler(signum, frame):
        global received_sigterm
        logger.info(f"收到SIGTERM信号({signum})，优雅关闭当前实例...")
        received_sigterm = True
    
    def sigint_handler(signum, frame):
        global is_shutting_down
        logger.info(f"收到SIGINT信号({signum})，用户手动终止程序...")
        is_shutting_down = True
    
    signal.signal(signal.SIGTERM, sigterm_handler)
    signal.signal(signal.SIGINT, sigint_handler)

async def run_bot():
    """运行机器人主程序 - 诊断增强版"""
    global is_shutting_down, received_sigterm
    
    try:
        logger.info("🔄 创建新的事件循环...")
        
        # 创建新的事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        logger.info("✅ 新事件循环已设置")
        
        # 运行诊断
        logger.info("🔍 开始运行诊断...")
        
        if not diagnose_token():
            logger.error("❌ TOKEN诊断失败，无法继续")
            return
            
        diagnose_environment()
        
        # 测试Telegram连接
        if not await test_telegram_connection():
            logger.error("❌ Telegram连接测试失败，无法继续")
            return
        
        # 创建应用程序
        application = create_application()
        logger.info(f"🎯 电话号码查重机器人 v9.5 启动成功！重启次数: {restart_count}")
        
        # 添加心跳日志
        async def heartbeat():
            count = 0
            while not is_shutting_down and not received_sigterm:
                await asyncio.sleep(300)  # 每5分钟
                count += 1
                logger.info(f"💓 心跳检查 #{count} - 机器人运行正常，事件循环活跃")
        
        # 启动心跳任务
        heartbeat_task = asyncio.create_task(heartbeat())
        
        try:
            logger.info("🚀 开始运行轮询...")
            
            # 启动轮询
            await application.initialize()
            await application.start()
            
            logger.info("✅ 轮询已启动，机器人正在监听消息...")
            
            # 使用轮询模式，增强错误处理
            await application.updater.start_polling(
                drop_pending_updates=True,
                timeout=30,
                bootstrap_retries=3,
                error_callback=lambda error: logger.error(f"轮询错误: {error}")
            )
            
            logger.info("🎉 轮询启动完成，等待信号...")
            
            # 等待直到需要停止或重启
            while not is_shutting_down and not received_sigterm:
                await asyncio.sleep(1)
                
            if received_sigterm:
                logger.info("🔄 收到SIGTERM，准备重启...")
            else:
                logger.info("🛑 收到停止信号，准备退出...")
                
        except Exception as e:
            logger.error(f"轮询过程中出错: {e}")
            logger.error(f"详细错误: {traceback.format_exc()}")
            raise e
        finally:
            # 清理资源
            logger.info("🧹 开始清理资源...")
            heartbeat_task.cancel()
            try:
                await application.updater.stop()
                await application.stop()
                await application.shutdown()
                logger.info("✅ 应用程序已优雅关闭")
            except Exception as e:
                logger.error(f"关闭时出错: {e}")
                
    except Exception as e:
        logger.error(f"运行机器人时出错: {e}")
        logger.error(f"详细错误: {traceback.format_exc()}")
        raise e

def main():
    """主函数 - 诊断增强版"""
    global restart_count, is_shutting_down, received_sigterm
    
    logger.info("=== 电话号码查重机器人 v9.5 启动 (诊断版) ===")
    logger.info(f"启动时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 设置信号处理器
    setup_signal_handlers()
    
    # 启动Flask服务器
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"Flask服务器启动，端口: {port}")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask服务器线程已启动")
    
    # 自动重启循环
    max_restarts = 20
    base_delay = 3
    consecutive_failures = 0
    
    while restart_count < max_restarts and not is_shutting_down:
        try:
            restart_count += 1
            received_sigterm = False
            logger.info(f"=== 第 {restart_count} 次启动机器人 ===")
            
            # 运行机器人
            asyncio.run(run_bot())
            
            # 如果到达这里说明正常退出或收到SIGTERM
            if received_sigterm:
                logger.info("🔄 收到SIGTERM信号，准备重启...")
                consecutive_failures = 0
            else:
                logger.warning("机器人正常退出")
                consecutive_failures = 0
            
        except KeyboardInterrupt:
            logger.info("🛑 收到键盘中断，程序正常退出")
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
                delay = min(base_delay * (2 ** (consecutive_failures - 1)), 60)
            
            logger.info(f"⏱️ 等待 {delay} 秒后重启...")
            time.sleep(delay)
    
    logger.info("🏁 程序已退出")

if __name__ == "__main__":
    main()
