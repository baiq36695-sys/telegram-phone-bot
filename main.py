#!/usr/bin/env python3
"""
电话号码重复检测机器人 - 网络连接优化版
解决Telegram API连接问题和网络稳定性
专为Render平台优化
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
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

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
from telegram.request import HTTPXRequest
from flask import Flask, jsonify

# 优化日志配置 - 减少噪音
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 抑制一些过于详细的日志
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# 初始化Flask应用
app = Flask(__name__)

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
graceful_shutdown = False
bot_application = None
is_running = False
restart_count = 0
max_restart_attempts = 5  # 减少重试次数，避免过度重试
start_time = time.time()
last_activity = time.time()

# 风险评估等级
RISK_LEVELS = {
    'LOW': {'emoji': '🟢', 'color': 'LOW', 'score': 1},
    'MEDIUM': {'emoji': '🟡', 'color': 'MEDIUM', 'score': 2}, 
    'HIGH': {'emoji': '🟠', 'color': 'HIGH', 'score': 3},
    'CRITICAL': {'emoji': '🔴', 'color': 'CRITICAL', 'score': 4}
}

def create_robust_session():
    """创建带重试机制的requests会话"""
    session = requests.Session()
    
    # 配置重试策略
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST"]
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # 设置超时
    session.timeout = 10
    
    return session

def update_activity():
    """更新最后活动时间"""
    global last_activity
    last_activity = time.time()

def extract_phone_numbers(text: str) -> Set[str]:
    """从文本中提取电话号码 - 核心功能保持不变"""
    patterns = [
        r'\+60\s+1[0-9]\s*-?\s*\d{4}\s+\d{4}',
        r'\+60\s*1[0-9]\s*-?\s*\d{4}\s*-?\s*\d{4}',
        r'\+60\s*1[0-9]\d{7,8}',
        r'\+86\s*1[3-9]\d{9}',
        r'\+1\s*[2-9]\d{2}\s*[2-9]\d{2}\s*\d{4}',
        r'1[3-9]\d{9}',
        r'01[0-9][-\s]?\d{4}[-\s]?\d{4}',
    ]
    
    phone_numbers = set()
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            cleaned = re.sub(r'\s+', ' ', match.strip())
            phone_numbers.add(cleaned)
    
    return phone_numbers

def categorize_phone_number(phone: str) -> str:
    """识别电话号码的类型和国家"""
    clean_phone = re.sub(r'[^\d+]', '', phone)
    
    if re.match(r'\+60[1][0-9]', clean_phone):
        return "🇲🇾 马来西亚手机"
    elif re.match(r'\+86[1][3-9]', clean_phone):
        return "🇨🇳 中国手机"
    elif re.match(r'\+1[2-9]', clean_phone):
        return "🇺🇸 美国/加拿大"
    elif re.match(r'^[1][3-9]\d{9}$', clean_phone):
        return "🇨🇳 中国手机（本地）"
    elif re.match(r'^0[1-9]', clean_phone):
        return "🇲🇾 马来西亚（本地）"
    else:
        return "🌍 其他国际号码"

def assess_phone_risk(phone: str, chat_data: Dict[str, Any]) -> Tuple[str, List[str]]:
    """评估电话号码风险等级 - 简化版本"""
    warnings = []
    risk_score = 0
    
    # 基础风险检查
    if phone in chat_data['phones']:
        risk_score += 2
        warnings.append("📞 号码重复")
    
    clean_phone = re.sub(r'[^\d+]', '', phone)
    if len(clean_phone) > 16 or len(clean_phone) < 8:
        risk_score += 1
        warnings.append("📏 长度异常")
    
    # 确定风险等级
    if risk_score >= 3:
        return 'HIGH', warnings
    elif risk_score >= 1:
        return 'MEDIUM', warnings
    else:
        return 'LOW', warnings

# 保活机制 - 优化版本
def keep_alive_service():
    """轻量级保活服务"""
    session = create_robust_session()
    
    while not graceful_shutdown:
        try:
            time.sleep(900)  # 15分钟一次，减少频率
            if not graceful_shutdown:
                try:
                    port = int(os.environ.get('PORT', 10000))
                    response = session.get(f'http://localhost:{port}/health', timeout=5)
                    if response.status_code == 200:
                        logger.debug("🏓 Keep-alive successful")
                        update_activity()
                except Exception as e:
                    logger.debug(f"Keep-alive failed: {e}")
                    
        except Exception as e:
            logger.error(f"Keep-alive service error: {e}")
            break
    
    session.close()

# Flask路由 - 简化版本
@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    """简单健康检查"""
    update_activity()
    return jsonify({
        'status': 'healthy',
        'bot_running': is_running,
        'uptime': round(time.time() - start_time, 2)
    })

@app.route('/health')
def health():
    """基础健康检查"""
    update_activity()
    return jsonify({'status': 'ok'})

@app.route('/restart', methods=['POST'])
def force_restart():
    """手动重启"""
    global is_running
    logger.info("📨 收到重启请求")
    is_running = False
    return jsonify({'status': 'restarting'})

# Telegram机器人函数 - 简化版本
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    update_activity()
    user_name = update.effective_user.first_name or "朋友"
    
    help_text = f"""
🎯 **电话号码检测机器人 - 稳定版**

👋 欢迎，{user_name}！

📱 **支持格式**:
• 🇲🇾 马来西亚: +60 11-2896 2309
• 🇨🇳 中国: +86 138 0013 8000  
• 🇺🇸 美国: +1 555 123 4567
• 本地格式: 01-1234 5678

⚡ **功能**:
• 自动检测重复号码
• 智能风险评估
• 多国格式识别

📋 **命令**:
• /clear - 清除记录
• /stats - 查看统计
• /help - 帮助信息

💡 直接发送包含电话号码的消息开始检测！
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """清除数据"""
    update_activity()
    chat_id = update.effective_chat.id
    phone_count = len(user_groups[chat_id]['phones'])
    
    user_groups[chat_id]['phones'].clear()
    user_groups[chat_id]['phone_history'].clear()
    user_groups[chat_id]['risk_scores'].clear()
    
    await update.message.reply_text(f"🧹 已清除 {phone_count} 个号码记录")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """统计信息"""
    update_activity()
    chat_id = update.effective_chat.id
    chat_data = user_groups[chat_id]
    
    total_count = len(chat_data['phones'])
    uptime = time.time() - start_time
    
    stats_text = f"""
📊 **统计报告**

📈 **数据**:
• 总号码: {total_count} 个
• 运行时间: {uptime//3600:.0f}h {(uptime%3600)//60:.0f}m
• 重启次数: {restart_count} 次

🎯 **状态**: ✅ 运行正常
"""
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """帮助信息"""
    update_activity()
    help_text = """
🆘 **快速帮助**

📋 **命令**:
• /start - 开始使用
• /clear - 清除记录
• /stats - 查看统计
• /help - 本帮助

🚀 **使用**:
直接发送包含电话号码的消息即可开始检测

💡 **示例**: `联系我：+60 11-2896 2309`
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理消息 - 简化版本"""
    try:
        update_activity()
        chat_id = update.effective_chat.id
        message_text = update.message.text
        user_name = update.effective_user.first_name or "用户"
        chat_data = user_groups[chat_id]
        
        # 提取电话号码
        phone_numbers = extract_phone_numbers(message_text)
        
        if not phone_numbers:
            return
        
        # 检查重复
        existing_phones = chat_data['phones']
        new_phones = phone_numbers - existing_phones
        duplicate_phones = phone_numbers & existing_phones
        
        # 构建简洁回复
        response_parts = []
        response_parts.append("🎯 **号码检测结果**")
        response_parts.append(f"👤 {user_name}")
        response_parts.append("")
        
        # 新号码
        if new_phones:
            response_parts.append(f"✨ **新发现** ({len(new_phones)}个):")
            for i, phone in enumerate(sorted(new_phones), 1):
                phone_type = categorize_phone_number(phone)
                risk_level, _ = assess_phone_risk(phone, chat_data)
                risk_emoji = RISK_LEVELS[risk_level]['emoji']
                
                chat_data['risk_scores'][phone] = risk_level
                response_parts.append(f"{i}. `{phone}` {risk_emoji}")
                response_parts.append(f"   {phone_type}")
            
            existing_phones.update(new_phones)
            response_parts.append("")
        
        # 重复号码
        if duplicate_phones:
            response_parts.append(f"🔄 **重复** ({len(duplicate_phones)}个):")
            for i, phone in enumerate(sorted(duplicate_phones), 1):
                response_parts.append(f"{i}. `{phone}` 🔁")
            response_parts.append("")
        
        # 统计
        total = len(existing_phones)
        response_parts.append(f"📊 群组总计: {total} 个号码")
        response_parts.append(f"⏰ {datetime.datetime.now().strftime('%H:%M:%S')}")
        
        response = "\n".join(response_parts)
        await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"处理消息错误: {e}")
        await update.message.reply_text("❌ 处理错误，请重试")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """简化错误处理"""
    logger.error(f"Bot error: {context.error}")

def run_flask():
    """运行Flask"""
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"启动Flask服务器，端口: {port}")
    
    try:
        app.run(
            host='0.0.0.0',
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    except Exception as e:
        logger.error(f"Flask错误: {e}")

async def run_bot():
    """运行机器人 - 网络优化版本"""
    global bot_application, is_running, restart_count
    
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("未找到TELEGRAM_BOT_TOKEN环境变量")
        return
    
    try:
        logger.info(f"🚀 启动机器人 (第 {restart_count + 1} 次)")
        
        # 创建优化的HTTP请求配置
        request = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=10.0,
            read_timeout=10.0,
            write_timeout=10.0,
            pool_timeout=5.0
        )
        
        # 创建应用，使用优化的请求配置
        bot_application = Application.builder()\
            .token(bot_token)\
            .request(request)\
            .build()
        
        # 添加处理器
        bot_application.add_error_handler(error_handler)
        bot_application.add_handler(CommandHandler("start", start_command))
        bot_application.add_handler(CommandHandler("clear", clear_command))
        bot_application.add_handler(CommandHandler("stats", stats_command))
        bot_application.add_handler(CommandHandler("help", help_command))
        bot_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        is_running = True
        logger.info("✅ 机器人启动成功")
        
        # 优化的轮询配置
        await bot_application.run_polling(
            drop_pending_updates=True,
            close_loop=False,
            stop_signals=None,
            poll_interval=5.0,     # 增加轮询间隔，减少网络压力
            timeout=20,            # 减少超时时间
            bootstrap_retries=3,   # 限制bootstrap重试
            read_timeout=10,       # 减少读取超时
            write_timeout=10,      # 减少写入超时
            connect_timeout=10,    # 减少连接超时
            pool_timeout=5         # 减少池超时
        )
        
    except Exception as e:
        logger.error(f"机器人运行错误: {e}")
        is_running = False
        raise
    finally:
        is_running = False
        logger.info("机器人停止运行")

def start_bot_thread():
    """启动机器人线程 - 简化重启逻辑"""
    global bot_thread, is_running, restart_count, graceful_shutdown
    
    def run_async_bot():
        global restart_count, is_running
        
        while restart_count < max_restart_attempts and not graceful_shutdown:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                loop.run_until_complete(run_bot())
                
                if graceful_shutdown:
                    break
                    
            except Exception as e:
                restart_count += 1
                is_running = False
                logger.error(f"机器人错误 (第 {restart_count} 次): {e}")
                
                if restart_count < max_restart_attempts and not graceful_shutdown:
                    wait_time = min(30, 5 * restart_count)
                    logger.info(f"等待 {wait_time} 秒后重启...")
                    time.sleep(wait_time)
                else:
                    logger.error("达到最大重试次数，停止重启")
                    break
            finally:
                try:
                    loop.close()
                except:
                    pass
    
    if 'bot_thread' not in globals() or not bot_thread.is_alive():
        bot_thread = threading.Thread(target=run_async_bot, daemon=True)
        bot_thread.start()
        logger.info("🔄 机器人线程已启动")

def signal_handler(signum, frame):
    """信号处理 - Render优化"""
    global graceful_shutdown, is_running
    
    logger.info(f"🛑 收到信号 {signum}")
    
    if signum == signal.SIGTERM:
        logger.info("📋 Render平台重启信号")
        graceful_shutdown = True
        is_running = False
    else:
        logger.info("⏹️ 立即关闭")
        graceful_shutdown = True
        is_running = False
        sys.exit(0)

def main():
    """主函数 - 简化版本"""
    global graceful_shutdown
    
    logger.info("🚀 启动网络优化版应用...")
    logger.info("🔧 已优化Telegram API连接")
    logger.info("🏓 启用轻量级保活机制")
    logger.info("⚡ 启用智能重启机制")
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 启动Flask
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        time.sleep(2)
        logger.info("✅ Flask服务器已启动")
        
        # 启动保活服务
        keep_alive_thread = threading.Thread(target=keep_alive_service, daemon=True)
        keep_alive_thread.start()
        logger.info("🏓 保活服务已启动")
        
        # 启动机器人
        start_bot_thread()
        
        logger.info("🎯 所有服务已启动")
        
        # 主循环
        while not graceful_shutdown:
            time.sleep(10)
        
        logger.info("📋 准备退出...")
        
    except KeyboardInterrupt:
        logger.info("⌨️ 收到中断信号")
        graceful_shutdown = True
    except Exception as e:
        logger.error(f"❌ 程序错误: {e}")
        graceful_shutdown = True
    
    logger.info("👋 程序关闭")

if __name__ == '__main__':
    main()
