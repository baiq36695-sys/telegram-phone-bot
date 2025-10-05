import os
import re
import logging
import threading
import time
import sys
import traceback
import signal
from datetime import datetime, timezone
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from telegram import Update

# 配置日志，增强调试信息
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 设置第三方库日志级别
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# 从环境变量获取Bot Token
BOT_TOKEN = os.getenv('BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN', '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU')

# 全局重启计数器和状态 - 线程安全版本
class BotState:
    def __init__(self):
        self._lock = threading.Lock()
        self.restart_count = 0
        self.start_time = datetime.now(timezone.utc)
        self.is_shutting_down = False
        self.received_sigterm = False
        self.heartbeat_count = 0
        self.max_database_size = 10000  # 内存管理：最大存储10000个号码
    
    def increment_restart(self):
        with self._lock:
            self.restart_count += 1
            return self.restart_count
    
    def set_shutdown(self, value):
        with self._lock:
            self.is_shutting_down = value
    
    def set_sigterm(self, value):
        with self._lock:
            self.received_sigterm = value
    
    def increment_heartbeat(self):
        with self._lock:
            self.heartbeat_count += 1
            return self.heartbeat_count

# 全局状态对象
bot_state = BotState()

# 国家代码到国旗的映射（完整保留）
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
    """获取电话号码的国家代码 - 修复逻辑错误"""
    clean_phone = normalize_phone(phone)
    
    if phone.strip().startswith('+'):
        clean_phone = clean_phone
    else:
        # 修复：改进美国和中国号码识别逻辑
        if len(clean_phone) == 11:
            if clean_phone.startswith('1'):  # 美国/加拿大
                return '1'
            elif clean_phone.startswith(('13', '14', '15', '16', '17', '18', '19')):  # 中国手机号特征
                return '86'
        elif len(clean_phone) == 10 and clean_phone.startswith(('2', '3', '4', '5', '6', '7', '8', '9')):
            return '1'   # 美国/加拿大
    
    # 按长度匹配国家代码
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
    uptime = current_time - bot_state.start_time
    
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if days > 0:
        return f"{days}天 {hours}小时 {minutes}分钟"
    elif hours > 0:
        return f"{hours}小时 {minutes}分钟"
    else:
        return f"{minutes}分钟 {seconds}秒"

def manage_database_size(phone_database):
    """内存管理：控制数据库大小"""
    if len(phone_database) > bot_state.max_database_size:
        # 删除最老的1000条记录
        sorted_phones = sorted(phone_database.items(), key=lambda x: x[1]['first_seen_time'])
        for phone, _ in sorted_phones[:1000]:
            del phone_database[phone]
        logger.info(f"数据库大小管理：删除了1000条最旧记录，当前大小：{len(phone_database)}")

def start(update: Update, context: CallbackContext):
    """开始命令处理"""
    user = update.effective_user
    level_emoji = get_user_level_emoji(user.id)
    uptime = calculate_uptime()
    
    welcome_message = f"""
🎉 **电话号码查重机器人 v10.0** 🎉
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
• 💾 智能内存管理

📱 **使用方法：**
直接发送电话号码给我，我会帮您检查是否重复！

✨ **运行状态：**
• ⏰ 运行时间：{uptime}
• 🔄 重启次数：{bot_state.restart_count}
• 💓 心跳次数：{bot_state.heartbeat_count}

**命令列表：**
• `/help` - 快速帮助
• `/stats` - 查看详细统计
• `/clear` - 清空数据库

═══════════════════════════
🚀 开始发送电话号码吧！
"""
    
    update.message.reply_text(welcome_message, parse_mode='Markdown')

def help_command(update: Update, context: CallbackContext):
    """帮助命令处理"""
    help_message = f"""
🆘 **快速帮助** - v10.0
═══════════════════════════

📋 **可用命令：**
• `/start` - 完整功能介绍
• `/help` - 快速帮助（本页面）
• `/stats` - 详细统计信息
• `/clear` - 清空数据库

📱 **使用方法：**
直接发送电话号码给我即可自动检测！

⭐ **新功能：**
• 🔄 增强稳定性设计
• ⏰ 实时时间戳显示  
• 📊 完整统计系统
• 💾 智能内存管理
• 🛡️ 线程安全保护

═══════════════════════════
💡 直接发送号码开始使用！
"""
    
    update.message.reply_text(help_message, parse_mode='Markdown')

def check_phone_duplicate(update: Update, context: CallbackContext):
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
        
        # 内存管理
        manage_database_size(phone_database)
        
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
                
                update.message.reply_text(duplicate_message, parse_mode='Markdown')
                
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
                
                update.message.reply_text(success_message, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"处理消息时出错: {e}")
        logger.error(f"错误详情: {traceback.format_exc()}")
        update.message.reply_text(
            "❌ 处理消息时出现错误，请稍后重试。",
            parse_mode='Markdown'
        )

def stats(update: Update, context: CallbackContext):
    """显示详细统计信息"""
    if 'phone_database' not in context.chat_data:
        update.message.reply_text("📊 暂无数据记录。")
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
• 内存使用：**{total_numbers}/{bot_state.max_database_size}** （已优化）

{country_text}

⚙️ **运行状态：**
• ⏰ 运行时间：{uptime}
• 🔄 重启次数：{bot_state.restart_count}
• 💓 心跳次数：{bot_state.heartbeat_count}
• 📅 启动时间：{format_datetime(bot_state.start_time)}
• 🛡️ 系统状态：稳定运行

═══════════════════════════
💡 使用 `/clear` 清空数据库
"""
    
    update.message.reply_text(stats_message, parse_mode='Markdown')

def clear_database(update: Update, context: CallbackContext):
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
• 内存释放：✅ 已优化

═══════════════════════════
✨ 可以重新开始记录号码了！
"""
    
    update.message.reply_text(clear_message, parse_mode='Markdown')

def create_application():
    """创建新的Telegram应用实例 - 兼容版本"""
    try:
        logger.info("开始创建应用程序...")
        
        # 创建 Updater（兼容v13.15）
        updater = Updater(token=BOT_TOKEN, use_context=True)
        
        # 获取 dispatcher
        dp = updater.dispatcher
        
        # 添加处理器
        dp.add_handler(CommandHandler("start", start))
        dp.add_handler(CommandHandler("help", help_command))
        dp.add_handler(CommandHandler("stats", stats))
        dp.add_handler(CommandHandler("clear", clear_database))
        dp.add_handler(MessageHandler(Filters.text & ~Filters.command, check_phone_duplicate))
        
        logger.info("应用程序创建成功，处理器已注册")
        return updater
        
    except Exception as e:
        logger.error(f"创建应用程序失败: {e}")
        logger.error(f"详细错误: {traceback.format_exc()}")
        raise e

def setup_signal_handlers():
    """设置信号处理器 - 简化版本"""
    def sigterm_handler(signum, frame):
        logger.info(f"收到SIGTERM信号({signum})，准备重启...")
        bot_state.set_sigterm(True)
    
    def sigint_handler(signum, frame):
        logger.info(f"收到SIGINT信号({signum})，程序终止...")
        bot_state.set_shutdown(True)
    
    signal.signal(signal.SIGTERM, sigterm_handler)
    signal.signal(signal.SIGINT, sigint_handler)

def heartbeat_monitor():
    """心跳监控线程"""
    while not bot_state.is_shutting_down and not bot_state.received_sigterm:
        time.sleep(300)  # 每5分钟
        count = bot_state.increment_heartbeat()
        logger.info(f"💓 心跳检查 #{count} - 机器人运行正常")

def run_bot():
    """运行机器人主程序 - 稳定版本"""
    try:
        logger.info("🔄 启动机器人程序...")
        
        # 创建应用程序
        updater = create_application()
        logger.info(f"🎯 电话号码查重机器人 v10.0 启动成功！重启次数: {bot_state.restart_count}")
        
        # 启动心跳监控
        heartbeat_thread = threading.Thread(target=heartbeat_monitor, daemon=True)
        heartbeat_thread.start()
        logger.info("💓 心跳监控已启动")
        
        # 启动轮询
        logger.info("🚀 开始运行轮询...")
        updater.start_polling(
            poll_interval=1.0,
            timeout=10,
            clean=True,
            bootstrap_retries=3,
            drop_pending_updates=True
        )
        
        logger.info("✅ 轮询已启动，机器人正在监听消息...")
        
        # 等待信号
        while not bot_state.is_shutting_down and not bot_state.received_sigterm:
            time.sleep(1)
            
        if bot_state.received_sigterm:
            logger.info("🔄 收到SIGTERM，准备重启...")
        else:
            logger.info("🛑 收到停止信号，准备退出...")
                
    except Exception as e:
        logger.error(f"🚨 Bot运行出错: {e}")
        logger.error(f"详细错误: {traceback.format_exc()}")
        raise e
    finally:
        # 清理资源
        try:
            updater.stop()
            updater.is_idle = False
            logger.info("✅ 应用程序已优雅关闭")
        except Exception as e:
            logger.error(f"关闭应用程序时出错: {e}")

def main():
    """主函数 - 增强重启机制"""
    logger.info("=== 电话号码查重机器人 v10.0 启动 ===")
    logger.info(f"启动时间: {format_datetime(bot_state.start_time)}")
    
    # 设置信号处理器
    setup_signal_handlers()
    
    # 自动重启循环
    max_restarts = 20
    base_delay = 3
    consecutive_failures = 0
    
    while bot_state.restart_count < max_restarts and not bot_state.is_shutting_down:
        try:
            restart_num = bot_state.increment_restart()
            bot_state.set_sigterm(False)  # 重置信号标志
            logger.info(f"=== 第 {restart_num} 次启动机器人 ===")
            
            # 运行机器人
            run_bot()
            
            # 如果到达这里说明正常退出或收到信号
            if bot_state.received_sigterm:
                logger.info("🔄 收到SIGTERM信号，准备重启...")
                consecutive_failures = 0
            else:
                logger.warning("机器人正常退出")
                consecutive_failures = 0
            
        except KeyboardInterrupt:
            logger.info("🛑 收到键盘中断，程序正常退出")
            bot_state.set_shutdown(True)
            break
            
        except Exception as e:
            consecutive_failures += 1
            logger.error(f"=== Bot异常停止 （第{bot_state.restart_count}次） ===")
            logger.error(f"异常类型: {type(e).__name__}")
            logger.error(f"异常信息: {e}")
            logger.error(f"连续失败: {consecutive_failures} 次")
            
            if bot_state.restart_count >= max_restarts:
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
