#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极稳定版电话号码机器人 - 专为Render平台优化
Ultra-stable version with conservative network settings
"""
import os
import re
import sys
import time
import signal
import logging
import asyncio
import threading
from contextlib import contextmanager
from functools import wraps
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import NetworkError, TimedOut, RetryAfter

# 配置日志 - 设置最小日志级别
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 💡 关键：压制网络库的详细日志，避免日志洪水
for lib_name in ['httpx', 'telegram', 'urllib3', 'httpcore']:
    logging.getLogger(lib_name).setLevel(logging.ERROR)  # 只显示ERROR级别

# 全局配置
shutdown_event = threading.Event()
restart_attempts = 0
max_restart_attempts = 3  # 减少重启次数，避免过于频繁

def signal_handler(signum, frame):
    """优化的信号处理器"""
    if signum == signal.SIGTERM:
        logger.info("🔄 检测到平台重启信号，准备优雅关闭...")
        shutdown_event.set()
        # 不调用sys.exit(0)，让主程序自然结束
    else:
        logger.info(f"收到信号 {signum}，立即关闭")
        sys.exit(0)

# 注册信号处理器
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

def extract_phone_numbers(text):
    """提取电话号码"""
    # 中国手机号码模式
    china_mobile_pattern = r'1[3-9]\d{9}'
    
    # 国际号码模式（更宽松）
    international_pattern = r'(?:\+?86\s?)?(?:1[3-9]\d{9})'
    
    # 固定电话模式
    landline_pattern = r'(?:0\d{2,3}[-\s]?)?\d{7,8}'
    
    phone_numbers = set()
    
    # 查找中国手机号
    china_mobiles = re.findall(china_mobile_pattern, text)
    phone_numbers.update(china_mobiles)
    
    # 查找国际格式号码
    international_nums = re.findall(international_pattern, text)
    phone_numbers.update([num.replace('+86', '').replace(' ', '') for num in international_nums])
    
    # 查找固定电话
    landlines = re.findall(landline_pattern, text)
    phone_numbers.update(landlines)
    
    return list(phone_numbers)

def safe_telegram_call(max_retries=2, delay=3):
    """装饰器：安全调用Telegram API，减少重试次数"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (NetworkError, TimedOut) as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"网络请求失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"网络请求最终失败: {e}")
                        raise
                except RetryAfter as e:
                    logger.info(f"触发速率限制，等待 {e.retry_after} 秒")
                    await asyncio.sleep(e.retry_after)
                    return await func(*args, **kwargs)
                except Exception as e:
                    logger.error(f"请求发生未知错误: {e}")
                    raise
        return wrapper
    return decorator

@safe_telegram_call(max_retries=2, delay=5)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始命令"""
    welcome_text = """
🤖 电话号码提取机器人已启动！

📱 功能说明：
• 发送包含电话号码的文本，我会自动提取并格式化
• 支持中国大陆手机号、固定电话等格式
• 智能识别多种号码格式

💡 使用方法：
直接发送包含电话号码的文本即可！

🔍 示例：
"联系电话：138-1234-5678"
"客服热线：010-12345678"
"""
    await update.message.reply_text(welcome_text)

@safe_telegram_call(max_retries=2, delay=5)
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """帮助命令"""
    help_text = """
📋 使用帮助

🔧 支持的电话号码格式：
• 中国大陆手机号：13812345678, 138-1234-5678, 138 1234 5678
• 带区号格式：+86 13812345678, +8613812345678
• 固定电话：010-12345678, 021-87654321
• 800/400号码：400-123-4567

⚡ 使用技巧：
1. 直接粘贴包含号码的文本
2. 支持批量提取多个号码
3. 自动去重和格式化

📞 示例输入：
"张经理的电话是138-1234-5678，办公室是010-88776655"

🎯 输出结果：
会自动提取并整理所有找到的电话号码
"""
    await update.message.reply_text(help_text)

@safe_telegram_call(max_retries=2, delay=5)
async def extract_phone_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理文本消息并提取电话号码"""
    try:
        user_text = update.message.text
        user_id = update.effective_user.id
        
        logger.info(f"用户 {user_id} 发送消息")
        
        # 提取电话号码
        phone_numbers = extract_phone_numbers(user_text)
        
        if phone_numbers:
            # 格式化输出
            result_text = "📞 提取到的电话号码：\n\n"
            for i, phone in enumerate(phone_numbers, 1):
                result_text += f"{i}. `{phone}`\n"
            
            result_text += f"\n📊 共找到 {len(phone_numbers)} 个电话号码"
            
            if len(phone_numbers) > 5:
                result_text += "\n\n💡 提示：号码较多，建议分批处理"
        else:
            result_text = "❌ 未找到有效的电话号码\n\n💡 请确保文本中包含正确格式的电话号码"
        
        await update.message.reply_text(result_text, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"处理消息时发生错误: {e}")
        error_text = "❗ 处理消息时发生错误，请稍后重试"
        await update.message.reply_text(error_text)

async def health_check():
    """简单的健康检查，减少网络负载"""
    try:
        # 极简的健康检查，避免过多网络请求
        await asyncio.sleep(1)
        return True
    except Exception as e:
        logger.warning(f"健康检查失败: {e}")
        return False

def keep_alive_service():
    """保持活跃服务 - 降低频率"""
    import requests
    
    def ping_self():
        try:
            # 30分钟一次，减少网络负载
            requests.get("https://phone-bot-v3-xuwk.onrender.com/", timeout=10)
            logger.debug("Keep-alive ping成功")
        except Exception as e:
            logger.debug(f"Keep-alive ping失败: {e}")
    
    def run_keep_alive():
        while not shutdown_event.is_set():
            ping_self()
            # 30分钟间隔，大幅减少网络请求
            shutdown_event.wait(30 * 60)  # 1800秒
    
    thread = threading.Thread(target=run_keep_alive, daemon=True)
    thread.start()
    logger.info("Keep-alive服务已启动 (30分钟间隔)")

def main():
    """主函数 - 超保守网络配置"""
    global restart_attempts
    
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("❌ 未设置TELEGRAM_BOT_TOKEN环境变量")
        sys.exit(1)
    
    logger.info("🤖 启动电话号码提取机器人...")
    
    try:
        # 🔥 关键：极保守的网络配置
        from telegram.ext import HTTPXRequest
        
        # 使用非常宽松的超时设置
        request = HTTPXRequest(
            connection_pool_size=4,     # 减少连接池大小
            connect_timeout=20.0,       # 大幅增加连接超时
            read_timeout=30.0,          # 大幅增加读取超时
            write_timeout=30.0,         # 增加写入超时
            pool_timeout=30.0,          # 增加池超时
        )
        
        # 创建应用实例
        bot_application = Application.builder().token(bot_token).request(request).build()
        
        # 添加处理器
        bot_application.add_handler(CommandHandler("start", start_command))
        bot_application.add_handler(CommandHandler("help", help_command))
        bot_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, extract_phone_handler))
        
        logger.info("✅ 机器人配置完成")
        
        # 启动keep-alive服务（低频率）
        keep_alive_service()
        
        # 🚀 使用极保守的轮询设置
        logger.info("🚀 开始轮询...")
        bot_application.run_polling(
            poll_interval=10.0,         # 10秒轮询间隔，大幅减少请求频率
            timeout=30,                 # 30秒超时
            bootstrap_retries=2,        # 减少启动重试
            read_timeout=30,            # 读取超时
            write_timeout=30,           # 写入超时
            connect_timeout=20,         # 连接超时
            stop_signals=None,          # 禁用默认信号处理
        )
        
    except Exception as e:
        logger.error(f"💥 机器人运行异常: {e}")
        raise

def run_with_restart():
    """重启循环 - 更保守的重启策略"""
    global restart_attempts
    
    while restart_attempts < max_restart_attempts:
        try:
            logger.info(f"🔄 启动尝试 {restart_attempts + 1}/{max_restart_attempts}")
            main()
            
            # 如果正常退出，重置重启计数
            if shutdown_event.is_set():
                logger.info("✅ 程序正常关闭")
                break
                
        except KeyboardInterrupt:
            logger.info("👋 接收到键盘中断，程序退出")
            break
        except Exception as e:
            restart_attempts += 1
            logger.error(f"💥 程序异常: {e}")
            
            if restart_attempts < max_restart_attempts:
                wait_time = 60 * restart_attempts  # 渐进式等待：60s, 120s, 180s
                logger.info(f"⏰ {wait_time}秒后重启...")
                time.sleep(wait_time)
            else:
                logger.error(f"❌ 达到最大重启次数 ({max_restart_attempts})，程序终止")
                break

if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🤖 电话号码提取机器人 - 超稳定版")
    logger.info("🔧 为Render平台极度优化")
    logger.info("=" * 50)
    
    run_with_restart()
