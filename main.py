import os
import re
import logging
import threading
import time
import sys
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from flask import Flask

# 配置日志，减少第三方库的噪音
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 设置第三方库日志级别为WARNING，减少控制台噪音
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# 从环境变量获取Bot Token
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')

# 全局重启计数器
restart_count = 0
start_time = datetime.now(timezone.utc)

# 国家代码到国旗的映射
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
    '84': '🇻🇳',    # 越南
    '62': '🇮🇩',    # 印尼
    '63': '🇵🇭',    # 菲律宾
    '91': '🇮🇳',    # 印度
    '61': '🇦🇺',    # 澳大利亚
    '64': '🇳🇿',    # 新西兰
    '55': '🇧🇷',    # 巴西
    '52': '🇲🇽',    # 墨西哥
    '54': '🇦🇷',    # 阿根廷
    '47': '🇳🇴',    # 挪威
    '46': '🇸🇪',    # 瑞典
    '45': '🇩🇰',    # 丹麦
    '358': '🇫🇮',   # 芬兰
    '31': '🇳🇱',    # 荷兰
    '32': '🇧🇪',    # 比利时
    '41': '🇨🇭',    # 瑞士
    '43': '🇦🇹',    # 奥地利
    '420': '🇨🇿',   # 捷克
    '48': '🇵🇱',    # 波兰
    '90': '🇹🇷',    # 土耳其
    '972': '🇮🇱',   # 以色列
    '971': '🇦🇪',   # 阿联酋
    '966': '🇸🇦',   # 沙特阿拉伯
    '20': '🇪🇬',    # 埃及
    '27': '🇿🇦',    # 南非
    '234': '🇳🇬',   # 尼日利亚
    '254': '🇰🇪',   # 肯尼亚
}

def normalize_phone(phone):
    """标准化电话号码，保留数字"""
    return re.sub(r'[^\d]', '', phone)

def get_country_flag(phone):
    """根据电话号码获取国家国旗"""
    clean_phone = normalize_phone(phone)
    
    # 尝试匹配不同长度的国家代码
    for code_length in [4, 3, 2, 1]:
        if len(clean_phone) >= code_length:
            country_code = clean_phone[:code_length]
            if country_code in COUNTRY_FLAGS:
                return COUNTRY_FLAGS[country_code]
    
    return '🌍'  # 默认地球图标

def get_country_code(phone):
    """获取国家代码"""
    clean_phone = normalize_phone(phone)
    
    for code_length in [4, 3, 2, 1]:
        if len(clean_phone) >= code_length:
            country_code = clean_phone[:code_length]
            if country_code in COUNTRY_FLAGS:
                return country_code
    
    return 'Unknown'

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始命令处理"""
    user = update.effective_user
    level_emoji = get_user_level_emoji(user.id)
    uptime = calculate_uptime()
    
    welcome_message = f"""
🎉 **电话号码查重机器人 v9.2** 🎉
═══════════════════════════

👋 欢迎，{level_emoji} **{user.full_name}**！

🔍 **功能特点：**
• 智能去重检测
• 实时时间显示  
• 用户追踪系统
• 重复次数统计
• 国家识别标识
• 📊 完整统计功能
• 🔄 自动重启保护

📱 **使用方法：**
直接发送电话号码给我，我会帮您检查是否重复！

✨ **系统状态：**
• ⏰ 运行时间：{uptime}
• 🔄 重启次数：{restart_count}

**命令列表：**
• `/stats` - 查看详细统计
• `/clear` - 清空数据库
• `/system` - 查看系统状态

═══════════════════════════
🚀 开始发送电话号码吧！
"""
    
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

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
        await update.message.reply_text(
            "❌ 处理消息时出现错误，请稍后重试。",
            parse_mode='Markdown'
        )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

⚙️ **系统状态：**
• ⏰ 运行时间：{uptime}
• 🔄 重启次数：{restart_count}
• 📅 启动时间：{format_datetime(start_time)}

═══════════════════════════
💡 使用 `/clear` 清空数据库
🔧 使用 `/system` 查看系统详情
"""
    
    await update.message.reply_text(stats_message, parse_mode='Markdown')

async def system_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示系统状态"""
    uptime = calculate_uptime()
    
    # 内存和性能信息
    import psutil
    memory_info = psutil.virtual_memory()
    cpu_percent = psutil.cpu_percent(interval=1)
    
    system_message = f"""
🔧 **系统状态监控** 🔧
═══════════════════════════

⚙️ **运行状态：**
• 🟢 状态：运行正常
• ⏰ 运行时间：{uptime}
• 🔄 重启次数：{restart_count}
• 📅 启动时间：{format_datetime(start_time)}

💻 **系统资源：**
• 🧠 CPU使用率：{cpu_percent}%
• 💾 内存使用：{memory_info.percent}%
• 💾 可用内存：{memory_info.available // (1024*1024)} MB

🤖 **机器人信息：**
• 📱 版本：v9.2 稳定版
• 🔄 自动重启：已启用
• 📊 统计功能：已启用
• 🛡️ 异常保护：已启用

═══════════════════════════
✅ 所有系统正常运行中！
"""
    
    await update.message.reply_text(system_message, parse_mode='Markdown')

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

def run_flask():
    """运行Flask服务器"""
    app = Flask(__name__)
    
    @app.route('/')
    def health_check():
        uptime = calculate_uptime()
        return f"Phone Bot v9.2 is alive! 🚀<br>Uptime: {uptime}<br>Restarts: {restart_count}", 200
    
    @app.route('/status')
    def status():
        return {
            "status": "running",
            "version": "9.2",
            "uptime": calculate_uptime(),
            "restart_count": restart_count,
            "start_time": start_time.isoformat(),
            "features": ["realtime_tracking", "duplicate_detection", "user_stats", "auto_restart", "full_statistics"]
        }, 200
    
    @app.route('/health')
    def health():
        return {"healthy": True, "timestamp": datetime.now(timezone.utc).isoformat()}, 200
    
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def run_bot():
    """运行机器人主程序"""
    try:
        # 创建Telegram应用
        application = Application.builder().token(BOT_TOKEN).build()
        
        # 添加处理器
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("stats", stats))
        application.add_handler(CommandHandler("system", system_status))
        application.add_handler(CommandHandler("clear", clear_database))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_phone_duplicate))
        
        logger.info(f"电话号码查重机器人 v9.2 启动成功！重启次数: {restart_count}")
        
        # 启动机器人
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Bot运行出错: {e}")
        raise e

def main():
    """主函数 - 带自动重启功能"""
    global restart_count
    
    max_restarts = 10  # 最大重启次数
    restart_delay = 5   # 重启延迟秒数
    
    # 启动Flask服务器
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask服务器已启动")
    
    while restart_count < max_restarts:
        try:
            run_bot()
            # 如果正常退出，则跳出循环
            break
            
        except KeyboardInterrupt:
            logger.info("收到退出信号，正常关闭...")
            break
            
        except Exception as e:
            restart_count += 1
            logger.error(f"Bot异常停止 (第{restart_count}次): {e}")
            
            if restart_count >= max_restarts:
                logger.error(f"达到最大重启次数 ({max_restarts})，程序终止")
                sys.exit(1)
            
            logger.info(f"等待 {restart_delay} 秒后自动重启...")
            time.sleep(restart_delay)
            
            # 增加重启延迟，避免频繁重启
            restart_delay = min(restart_delay * 2, 60)  # 最大延迟60秒
            
            logger.info(f"正在进行第 {restart_count} 次自动重启...")
    
    logger.info("程序已退出")

if __name__ == "__main__":
    main()
