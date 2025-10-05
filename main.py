#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
稳定版本 v10.0 - 解决重启后无响应问题
• 重启后延迟启动轮询，确保清理完全
• 自动检测和清理消息队列
• 增强的健康检查机制
• 智能重启延迟策略
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
import requests
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

# 从环境变量获取Bot Token
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

# 完整的国家代码到国旗的映射
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

def format_phone_display(phone):
    """格式化电话号码用于显示"""
    normalized = normalize_phone(phone)
    
    if not normalized:
        return phone
    
    # 检测国家代码
    country_code = None
    country_flag = '🌍'
    
    for code in sorted(COUNTRY_FLAGS.keys(), key=len, reverse=True):
        if normalized.startswith(code):
            country_code = code
            country_flag = COUNTRY_FLAGS[code]
            break
    
    if country_code:
        # 分离国家代码和本地号码
        local_number = normalized[len(country_code):]
        
        # 根据国家代码格式化
        if country_code == '86':  # 中国
            if len(local_number) == 11:
                return f"{country_flag} +{country_code} {local_number[:3]} {local_number[3:7]} {local_number[7:]}"
        elif country_code == '1':  # 美国/加拿大
            if len(local_number) == 10:
                return f"{country_flag} +{country_code} ({local_number[:3]}) {local_number[3:6]}-{local_number[6:]}"
        elif country_code == '44':  # 英国
            if len(local_number) >= 10:
                return f"{country_flag} +{country_code} {local_number[:4]} {local_number[4:7]} {local_number[7:]}"
        
        # 通用格式
        if len(local_number) >= 7:
            mid = len(local_number) // 2
            return f"{country_flag} +{country_code} {local_number[:mid]} {local_number[mid:]}"
        else:
            return f"{country_flag} +{country_code} {local_number}"
    
    # 无法识别国家代码的通用格式
    if len(normalized) >= 7:
        return f"🌍 {normalized[:3]} {normalized[3:6]} {normalized[6:]}"
    else:
        return f"🌍 {normalized}"

def extract_phone_numbers(text):
    """从文本中提取电话号码"""
    # 改进的正则表达式，支持更多格式
    patterns = [
        r'\+?[\d\s\-\(\)\.]{10,}',  # 国际格式和通用格式
        r'[\d\s\-\(\)\.]{10,}',     # 本地格式
    ]
    
    phones = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            normalized = normalize_phone(match)
            if len(normalized) >= 7:  # 至少7位数字
                phones.append(match.strip())
    
    return phones

def format_datetime(dt):
    """格式化日期时间"""
    return dt.strftime('%Y-%m-%d %H:%M:%S UTC')

def calculate_uptime():
    """计算运行时间"""
    uptime_seconds = (datetime.now(timezone.utc) - start_time).total_seconds()
    hours = int(uptime_seconds // 3600)
    minutes = int((uptime_seconds % 3600) // 60)
    return f"{hours}小时{minutes}分钟"

# ==================== 新增：消息队列清理功能 ====================

async def clear_message_queue(force=False):
    """清理Telegram消息队列 - 增强版"""
    try:
        logger.info("🧹 开始清理消息队列...")
        
        # 使用API直接清理
        api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        
        if force:
            # 强制清理所有消息
            params = {
                'offset': 999999999,
                'limit': 1,
                'timeout': 2
            }
            logger.info("🚀 强制清理模式：跳过所有待处理消息")
        else:
            # 温和清理模式
            params = {
                'offset': -1,
                'limit': 100,
                'timeout': 5
            }
            logger.info("🧽 温和清理模式：逐步处理消息")
        
        response = requests.get(api_url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                updates = data.get('result', [])
                if updates and not force:
                    # 有消息需要确认删除
                    last_update_id = updates[-1]['update_id']
                    confirm_params = {'offset': last_update_id + 1, 'limit': 1, 'timeout': 1}
                    requests.get(api_url, params=confirm_params, timeout=5)
                    logger.info(f"📤 确认删除 {len(updates)} 条消息")
                
                logger.info("✅ 消息队列清理完成")
                return True
            else:
                logger.warning(f"❌ API返回错误: {data}")
        else:
            logger.warning(f"❌ HTTP错误: {response.status_code}")
            
    except Exception as e:
        logger.error(f"❌ 清理消息队列失败: {e}")
    
    return False

async def check_message_queue_status():
    """检查消息队列状态"""
    try:
        api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo"
        response = requests.get(api_url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                result = data.get('result', {})
                pending_count = result.get('pending_update_count', 0)
                
                if pending_count > 0:
                    logger.warning(f"⚠️ 发现 {pending_count} 条待处理消息")
                    return pending_count
                else:
                    logger.info("✅ 消息队列状态正常")
                    return 0
    except Exception as e:
        logger.error(f"❌ 检查消息队列状态失败: {e}")
    
    return -1

async def smart_queue_cleanup():
    """智能队列清理 - 自动检测并清理"""
    try:
        logger.info("🔍 开始智能队列检测...")
        
        # 检查队列状态
        pending_count = await check_message_queue_status()
        
        if pending_count > 0:
            logger.info(f"🧹 检测到 {pending_count} 条待处理消息，开始清理...")
            
            # 先尝试温和清理
            success = await clear_message_queue(force=False)
            
            if not success:
                logger.info("🚀 温和清理失败，尝试强制清理...")
                success = await clear_message_queue(force=True)
            
            if success:
                # 再次检查状态
                await asyncio.sleep(2)
                final_count = await check_message_queue_status()
                if final_count == 0:
                    logger.info("✅ 智能清理成功，队列已清空")
                    return True
                else:
                    logger.warning(f"⚠️ 清理后仍有 {final_count} 条消息")
            
        return pending_count == 0
        
    except Exception as e:
        logger.error(f"❌ 智能队列清理失败: {e}")
        return False

# ==================== Flask 健康检查服务 ====================

app = Flask(__name__)

@app.route('/health')
def health_check():
    """健康检查端点"""
    return {
        'status': 'healthy',
        'uptime': calculate_uptime(),
        'restarts': restart_count,
        'start_time': format_datetime(start_time)
    }

@app.route('/')
def index():
    """根路径"""
    return f"🤖 电话号码查重机器人 v10.0 运行中！重启次数: {restart_count}"

def run_flask():
    """运行Flask服务器"""
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ==================== Telegram Bot 处理器 ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/start命令"""
    welcome_message = """
🤖 **电话号码查重机器人 v10.0** 🤖
═══════════════════════════════

👋 **欢迎使用！** 
我可以帮您检测电话号码是否重复。

📱 **使用方法：**
• 直接发送包含电话号码的消息
• 支持多种格式：+86 138 0013 8000
• 自动识别国家和格式化显示

🔧 **可用命令：**
• `/start` - 显示此帮助信息
• `/help` - 获取详细帮助  
• `/stats` - 查看统计信息
• `/clear` - 清空数据库

⚡ **新特性 v10.0：**
• 智能重启恢复机制
• 自动消息队列清理
• 增强稳定性保障

═══════════════════════════════
🚀 **立即开始：发送一个电话号码试试！**
"""
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/help命令"""
    help_message = """
📖 **详细使用说明** 📖
═══════════════════════════════

🔍 **支持的电话号码格式：**
• 国际格式：+86 138 0013 8000
• 本地格式：138-0013-8000
• 紧凑格式：13800138000
• 包含符号：(138) 001-3800

🌍 **支持的国家/地区：**
• 中国 🇨🇳、美国 🇺🇸、英国 🇬🇧
• 日本 🇯🇵、韩国 🇰🇷、香港 🇭🇰
• 新加坡 🇸🇬、马来西亚 🇲🇾
• 以及更多国家和地区...

📊 **检测结果说明：**
• ✅ 新号码 - 首次出现
• ⚠️ 重复号码 - 之前已记录
• 显示录入时间和次数

🛠️ **高级功能：**
• 自动格式化显示
• 智能国家识别  
• 重复历史追踪
• 数据统计分析

═══════════════════════════════
💡 有问题？直接发送电话号码开始使用！
"""
    await update.message.reply_text(help_message, parse_mode='Markdown')

async def check_phone_duplicate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """检查电话号码重复"""
    text = update.message.text
    phones = extract_phone_numbers(text)
    
    if not phones:
        await update.message.reply_text("📱 未检测到有效的电话号码，请重新发送。")
        return
    
    # 确保chat_data中有phone_database
    if 'phone_database' not in context.chat_data:
        context.chat_data['phone_database'] = {}
    
    results = []
    for phone in phones:
        normalized = normalize_phone(phone)
        formatted = format_phone_display(phone)
        
        if normalized in context.chat_data['phone_database']:
            # 重复号码
            first_seen = context.chat_data['phone_database'][normalized]['first_seen']
            count = context.chat_data['phone_database'][normalized]['count'] + 1
            context.chat_data['phone_database'][normalized]['count'] = count
            context.chat_data['phone_database'][normalized]['last_seen'] = datetime.now(timezone.utc)
            
            results.append(f"""
⚠️ **重复号码检测** ⚠️
━━━━━━━━━━━━━━━━━━━━━
📱 **号码：** `{formatted}`
🔄 **状态：** 重复 (第{count}次)
⏰ **首次录入：** {format_datetime(first_seen)}
📈 **出现次数：** {count}
━━━━━━━━━━━━━━━━━━━━━
""")
        else:
            # 新号码
            now = datetime.now(timezone.utc)
            context.chat_data['phone_database'][normalized] = {
                'original': phone,
                'first_seen': now,
                'last_seen': now,
                'count': 1
            }
            
            results.append(f"""
✅ **新号码录入** ✅ 
━━━━━━━━━━━━━━━━━━━━━
📱 **号码：** `{formatted}`
🆕 **状态：** 首次出现
⏰ **录入时间：** {format_datetime(now)}
━━━━━━━━━━━━━━━━━━━━━
""")
    
    # 发送结果
    final_message = '\n'.join(results) + f"\n💾 **数据库：** 已存储 {len(context.chat_data['phone_database'])} 个号码"
    await update.message.reply_text(final_message, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示统计信息"""
    if 'phone_database' not in context.chat_data:
        context.chat_data['phone_database'] = {}
    
    db = context.chat_data['phone_database']
    total_numbers = len(db)
    
    if total_numbers == 0:
        await update.message.reply_text("📊 数据库为空，还未录入任何电话号码。")
        return
    
    # 计算统计信息
    total_checks = sum(record['count'] for record in db.values())
    duplicate_numbers = sum(1 for record in db.values() if record['count'] > 1)
    uptime = calculate_uptime()
    
    stats_message = f"""
📊 **统计报告** 📊
═══════════════════════════

📈 **数据统计：**
• 📱 总号码数量：**{total_numbers}**
• 🔍 总检测次数：**{total_checks}**
• ⚠️ 重复号码：**{duplicate_numbers}**
• ✅ 唯一号码：**{total_numbers - duplicate_numbers}**

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

# 错误处理回调
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
    """创建Telegram应用程序"""
    logger.info("开始创建应用程序...")
    
    try:
        # 完整的网络超时配置
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
        
        # 添加错误处理器
        application.add_error_handler(error_callback)
        
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
        with state_lock:
            logger.info(f"收到SIGTERM信号({signum})，优雅关闭当前实例...")
            received_sigterm = True
    
    def sigint_handler(signum, frame):
        global is_shutting_down
        with state_lock:
            logger.info(f"收到SIGINT信号({signum})，用户手动终止程序...")
            is_shutting_down = True
    
    signal.signal(signal.SIGTERM, sigterm_handler)
    signal.signal(signal.SIGINT, sigint_handler)

async def run_bot():
    """运行机器人主程序 - v10.0增强版"""
    global is_shutting_down, received_sigterm
    
    application = None
    heartbeat_task = None
    
    try:
        logger.info("🔄 开始运行机器人...")
        
        # ==================== 重启后智能清理流程 ====================
        if restart_count > 1:
            logger.info("🧠 检测到重启，执行智能清理流程...")
            
            # 延迟启动，让系统稳定
            logger.info("⏳ 重启延迟：等待系统稳定...")
            await asyncio.sleep(3)
            
            # 执行智能队列清理
            cleanup_success = await smart_queue_cleanup()
            
            if cleanup_success:
                logger.info("✅ 智能清理成功，继续启动流程")
            else:
                logger.warning("⚠️ 智能清理未完全成功，但继续启动")
            
            # 额外延迟，确保清理生效
            logger.info("⏳ 清理后延迟：确保队列状态稳定...")
            await asyncio.sleep(2)
        else:
            logger.info("🚀 首次启动，执行标准清理...")
            await smart_queue_cleanup()
        
        # ==================== 创建和初始化应用程序 ====================
        application = create_application()
        logger.info(f"🎯 电话号码查重机器人 v10.0 启动成功！重启次数: {restart_count}")
        
        # 心跳监控 - 增强版
        async def enhanced_heartbeat():
            count = 0
            consecutive_queue_issues = 0
            
            while True:
                # 检查状态，如果需要停止则退出
                with state_lock:
                    if is_shutting_down or received_sigterm:
                        logger.info("💓 心跳监控收到停止信号，退出")
                        break
                
                await asyncio.sleep(300)  # 每5分钟
                count += 1
                
                # 标准心跳检查
                logger.info(f"💓 心跳检查 #{count} - 机器人运行正常")
                
                # 每30分钟检查一次队列状态
                if count % 6 == 0:  # 6 * 5分钟 = 30分钟
                    try:
                        logger.info("🔍 定期队列健康检查...")
                        pending_count = await check_message_queue_status()
                        
                        if pending_count > 0:
                            consecutive_queue_issues += 1
                            logger.warning(f"⚠️ 检测到队列阻塞，连续 {consecutive_queue_issues} 次")
                            
                            if consecutive_queue_issues >= 2:
                                logger.warning("🧹 执行预防性队列清理...")
                                await smart_queue_cleanup()
                                consecutive_queue_issues = 0
                        else:
                            consecutive_queue_issues = 0
                            
                    except Exception as e:
                        logger.error(f"❌ 队列检查失败: {e}")
        
        # 启动增强心跳任务
        heartbeat_task = asyncio.create_task(enhanced_heartbeat())
        
        # ==================== 应用程序初始化和启动 ====================
        logger.info("🚀 开始初始化应用程序...")
        await application.initialize()
        
        logger.info("🚀 开始启动应用程序...")
        await application.start()
        
        logger.info("🚀 准备启动轮询...")
        
        # 重启后额外延迟启动轮询
        if restart_count > 1:
            logger.info("⏳ 重启后轮询延迟：确保系统完全就绪...")
            await asyncio.sleep(5)  # 重启后延迟5秒启动轮询
        
        logger.info("🚀 开始轮询...")
        
        # 启动轮询 - 增强配置
        await application.updater.start_polling(
            drop_pending_updates=True,    # 丢弃待处理更新
            timeout=30,                   # 轮询超时
            bootstrap_retries=5,          # 增加重试次数
            read_timeout=30,              # 读取超时
            write_timeout=30,             # 写入超时
            connect_timeout=30,           # 连接超时
            pool_timeout=30,              # 连接池超时
        )
        
        logger.info("✅ 轮询已启动，机器人正在监听消息...")
        
        # 启动后最终确认
        await asyncio.sleep(2)
        final_status = await check_message_queue_status()
        logger.info(f"📊 启动完成，队列状态: {final_status} 条待处理消息")
        
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
        # 完整的资源清理
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
    """主函数 - v10.0增强版"""
    global restart_count, is_shutting_down, received_sigterm
    
    logger.info("=== 电话号码查重机器人 v10.0 启动 (智能稳定版) ===")
    logger.info(f"启动时间: {format_datetime(start_time)}")
    
    # 设置信号处理器
    setup_signal_handlers()
    
    # 启动Flask服务器
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"Flask服务器启动，端口: {port}")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask服务器线程已启动")
    
    # 智能重启循环
    max_restarts = 50      # 增加最大重启次数
    base_delay = 1         # 减少基础延迟
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
                    
                    # 智能重启延迟
                    if restart_count <= 5:
                        delay = 2  # 前5次快速重启
                    elif restart_count <= 10:
                        delay = 5  # 6-10次中等延迟
                    else:
                        delay = 10  # 10次以上长延迟
                    
                    logger.info(f"⏳ 智能重启延迟: {delay} 秒...")
                    time.sleep(delay)
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
            
            # 失败后的智能延迟
            if consecutive_failures <= 2:
                delay = base_delay * 2  # 2秒
            elif consecutive_failures <= 4:
                delay = base_delay * 5  # 5秒
            else:
                delay = base_delay * 10  # 10秒
            
            logger.info(f"⏱️ 失败重启延迟: {delay} 秒...")
            time.sleep(delay)
    
    logger.info("🏁 程序已退出")

if __name__ == "__main__":
    main()
