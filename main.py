import os
import re
import logging
import threading
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

def format_datetime(dt):
    """格式化日期时间为易读格式"""
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def get_user_level_emoji(user_id):
    """根据用户ID生成等级表情"""
    levels = ['👤', '⭐', '🌟', '💎', '👑', '🔥', '⚡', '🚀']
    return levels[user_id % len(levels)]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始命令处理"""
    user = update.effective_user
    level_emoji = get_user_level_emoji(user.id)
    
    welcome_message = f"""
🎉 **电话号码查重机器人 v9.1** 🎉
═══════════════════════════

👋 欢迎，{level_emoji} **{user.full_name}**！

🔍 **功能特点：**
• 智能去重检测
• 实时时间显示
• 用户追踪系统
• 重复次数统计
• 国家识别标识

📱 **使用方法：**
直接发送电话号码给我，我会帮您检查是否重复！

✨ **新增功能：**
• 📅 显示首次添加时间
• ⏰ 显示当前检测时间
• 👥 显示重复用户信息
• 🔢 显示重复总次数

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
    """显示统计信息"""
    if 'phone_database' not in context.chat_data:
        await update.message.reply_text("📊 暂无数据记录。")
        return
    
    phone_database = context.chat_data['phone_database']
    total_numbers = len(phone_database)
    total_duplicates = sum(1 for info in phone_database.values() if info['count'] > 1)
    
    stats_message = f"""
📊 **数据库统计** 📊
═══════════════════════════

📱 **总记录数：** {total_numbers}
🔄 **重复号码：** {total_duplicates}
✅ **唯一号码：** {total_numbers - total_duplicates}

═══════════════════════════
💡 使用 /clear 清空数据库
"""
    
    await update.message.reply_text(stats_message, parse_mode='Markdown')

async def clear_database(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """清空数据库"""
    context.chat_data['phone_database'] = {}
    await update.message.reply_text(
        "🗑️ **数据库已清空！**\n所有记录已删除。",
        parse_mode='Markdown'
    )

def run_flask():
    """运行Flask服务器"""
    app = Flask(__name__)
    
    @app.route('/')
    def health_check():
        return "Phone Bot v9.1 is alive! 🚀", 200
    
    @app.route('/status')
    def status():
        return {
            "status": "running",
            "version": "9.1",
            "features": ["realtime_tracking", "duplicate_detection", "user_stats"]
        }, 200
    
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def main():
    """主函数"""
    try:
        # 启动Flask服务器
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info("Flask服务器已启动")
        
        # 创建Telegram应用
        application = Application.builder().token(BOT_TOKEN).build()
        
        # 添加处理器
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("stats", stats))
        application.add_handler(CommandHandler("clear", clear_database))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_phone_duplicate))
        
        logger.info("电话号码查重机器人 v9.1 启动成功！")
        
        # 启动机器人
        application.run_polling()
        
    except Exception as e:
        logger.error(f"Bot启动失败: {e}")

if __name__ == "__main__":
    main()
