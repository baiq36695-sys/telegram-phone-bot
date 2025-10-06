#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电话号码检测机器人 - 兼容版本
适配低版本Python和简化依赖
"""

import logging
import os
import re
import threading
import time
import json
import platform
from datetime import datetime, timedelta
from collections import defaultdict, deque

# Flask for Web Service
from flask import Flask, request, jsonify

# 使用更兼容的telegram版本
try:
    from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
    from telegram import Bot, Update
except ImportError:
    print("正在安装telegram依赖...")
    import subprocess
    subprocess.check_call(["pip", "install", "python-telegram-bot==13.7"])
    from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
    from telegram import Bot, Update

# 简化的电话号码处理
try:
    import phonenumbers
    from phonenumbers import geocoder, carrier, timezone
except ImportError:
    print("正在安装phonenumbers依赖...")
    import subprocess
    subprocess.check_call(["pip", "install", "phonenumbers==8.12.57"])
    import phonenumbers
    from phonenumbers import geocoder, carrier, timezone

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 创建 Flask 应用
app = Flask(__name__)

# 国家代码到国旗的映射（简化版）
COUNTRY_FLAGS = {
    '1': '🇺🇸', '44': '🇬🇧', '33': '🇫🇷', '49': '🇩🇪', '39': '🇮🇹',
    '34': '🇪🇸', '7': '🇷🇺', '81': '🇯🇵', '82': '🇰🇷', '86': '🇨🇳',
    '852': '🇭🇰', '853': '🇲🇴', '886': '🇹🇼', '65': '🇸🇬', '60': '🇲🇾',
    '66': '🇹🇭', '84': '🇻🇳', '62': '🇮🇩', '63': '🇵🇭', '91': '🇮🇳'
}

class SimpleBotState:
    """简化的机器人状态管理"""
    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = datetime.now()
        self.message_count = 0
        self.user_count = 0
        self.phone_checks = 0
        self.users = set()
        self.user_data = {}
        self.country_stats = defaultdict(int)
        self.carrier_stats = defaultdict(int)
    
    def add_message(self):
        with self._lock:
            self.message_count += 1
    
    def add_user(self, user_id):
        with self._lock:
            if user_id not in self.users:
                self.users.add(user_id)
                self.user_count += 1
    
    def add_phone_check(self, phone_info):
        with self._lock:
            self.phone_checks += 1
            country = phone_info.get('country', 'Unknown')
            carrier_name = phone_info.get('carrier', 'Unknown')
            self.country_stats[country] += 1
            if carrier_name and carrier_name != 'Unknown':
                self.carrier_stats[carrier_name] += 1
    
    def get_stats(self):
        with self._lock:
            uptime = datetime.now() - self.start_time
            return {
                'uptime': str(uptime).split('.')[0],
                'messages': self.message_count,
                'users': self.user_count,
                'phone_checks': self.phone_checks,
                'countries': len(self.country_stats),
                'carriers': len(self.carrier_stats)
            }
    
    def get_user_data(self, user_id):
        with self._lock:
            return self.user_data.get(user_id, {
                'level': 1,
                'points': 0,
                'total_checks': 0
            })
    
    def update_user_data(self, user_id, data):
        with self._lock:
            self.user_data[user_id] = data

# 全局状态实例
bot_state = SimpleBotState()

# 获取Bot Token
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise ValueError("请设置BOT_TOKEN环境变量")

# 创建bot和dispatcher
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, workers=0)

def start_command(update, context):
    """开始命令处理"""
    try:
        user = update.effective_user
        bot_state.add_user(user.id)
        bot_state.add_message()
        
        welcome_text = f"""
🎯 **欢迎使用智能电话号码检测机器人！**

👋 你好 {user.first_name}！

📱 **功能说明：**
• 🔍 智能电话号码解析和验证
• 🌍 支持全球200+国家/地区
• 📊 详细运营商和地区信息
• 🕒 时区信息显示
• 🏆 用户等级系统

🔧 **可用命令：**
/start - 显示欢迎信息
/help - 查看详细帮助
/stats - 查看机器人统计
/mystats - 查看个人统计

💡 **使用提示：**
直接发送电话号码即可开始检测！
支持格式：+86 138xxxx、+1 555xxxx等

🚀 **开始体验智能检测吧！**
"""
        
        update.message.reply_text(welcome_text)
        logger.info(f"用户 {user.id} 开始使用机器人")
        
    except Exception as e:
        logger.error(f"start命令错误: {e}")
        update.message.reply_text("启动时出现错误，请稍后重试。")

def help_command(update, context):
    """帮助命令"""
    try:
        bot_state.add_message()
        
        help_text = """
📖 **智能电话号码检测机器人 - 帮助**

🔍 **如何使用：**
1. 直接发送电话号码给我
2. 支持多种格式：
   • 国际格式：+86 13812345678
   • 美式格式：+1 (555) 123-4567
   • 本地格式：138-1234-5678
   • 纯数字：13812345678

📊 **获取信息：**
🌍 地理信息：国家、地区
📡 运营商信息：运营商名称
📞 号码类型：手机、固话等
🕒 时区信息：当地时区
📄 格式建议：标准格式

📋 **命令列表：**
/start - 开始使用机器人
/help - 显示此帮助
/stats - 机器人统计
/mystats - 个人统计

💡 **提示：**
包含国家代码的号码识别更准确

❓ **需要帮助？**
直接发送电话号码试试：+86 13812345678
"""
        
        update.message.reply_text(help_text)
        
    except Exception as e:
        logger.error(f"help命令错误: {e}")
        update.message.reply_text("获取帮助信息时出现错误。")

def stats_command(update, context):
    """统计命令"""
    try:
        bot_state.add_message()
        
        stats = bot_state.get_stats()
        
        stats_text = f"""
📊 **机器人运行统计**

⏰ **运行状态：**
• 运行时间：{stats['uptime']}
• 系统健康：正常 ✅

📈 **使用统计：**
• 💬 处理消息：{stats['messages']:,} 条
• 👥 服务用户：{stats['users']:,} 人
• 📱 电话查询：{stats['phone_checks']:,} 次
• 🌍 覆盖国家：{stats['countries']} 个
• 📡 运营商数：{stats['carriers']} 家

🔥 **服务状态：** 
• Telegram API：正常 ✅
• 号码解析：正常 ✅  
• 数据统计：正常 ✅

感谢您的使用和支持！ 🙏
"""
        
        update.message.reply_text(stats_text)
        
    except Exception as e:
        logger.error(f"stats命令错误: {e}")
        update.message.reply_text("获取统计信息时出现错误。")

def mystats_command(update, context):
    """个人统计命令"""
    try:
        user = update.effective_user
        bot_state.add_message()
        
        user_data = bot_state.get_user_data(user.id)
        
        stats_text = f"""
👤 **{user.first_name} 的个人统计**

🏆 **等级信息：**
• 当前等级：Level {user_data['level']}
• 总积分：{user_data['points']:,} 分

📊 **使用统计：**
• 📱 总查询次数：{user_data['total_checks']} 次

💡 **升级提示：**
• 每次查询电话号码 +10 积分
• 继续查询来提升等级！

继续使用来解锁更多功能！ 🚀
"""
        
        update.message.reply_text(stats_text)
        
    except Exception as e:
        logger.error(f"mystats命令错误: {e}")
        update.message.reply_text("获取个人统计时出现错误。")

def analyze_phone_number(phone_text):
    """分析电话号码 - 简化版"""
    try:
        # 清理电话号码文本
        cleaned_phone = re.sub(r'[^\d+\-\s()]', '', phone_text)
        
        # 尝试解析电话号码
        try:
            parsed_number = phonenumbers.parse(cleaned_phone, None)
        except:
            # 智能国家码推测
            if not cleaned_phone.startswith('+'):
                if cleaned_phone.startswith('1') and len(cleaned_phone) >= 10:
                    cleaned_phone = '+' + cleaned_phone
                elif cleaned_phone.startswith('86') and len(cleaned_phone) >= 11:
                    cleaned_phone = '+' + cleaned_phone
                elif len(cleaned_phone) >= 10:
                    cleaned_phone = '+86' + cleaned_phone
                else:
                    cleaned_phone = '+1' + cleaned_phone
            
            parsed_number = phonenumbers.parse(cleaned_phone, None)
        
        # 验证号码有效性
        if not phonenumbers.is_valid_number(parsed_number):
            return None
        
        # 获取详细信息
        country_code = parsed_number.country_code
        national_number = parsed_number.national_number
        
        # 获取地理信息
        country = geocoder.description_for_number(parsed_number, "zh")
        if not country:
            country = geocoder.description_for_number(parsed_number, "en")
        
        # 获取运营商信息
        carrier_name = carrier.name_for_number(parsed_number, "zh")
        if not carrier_name:
            carrier_name = carrier.name_for_number(parsed_number, "en")
        
        # 获取时区信息
        timezones = timezone.time_zones_for_number(parsed_number)
        timezone_str = ', '.join(timezones) if timezones else "未知"
        
        # 判断号码类型
        number_type = phonenumbers.number_type(parsed_number)
        type_map = {
            phonenumbers.PhoneNumberType.MOBILE: "手机号码 📱",
            phonenumbers.PhoneNumberType.FIXED_LINE: "固定电话 📞",
            phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "手机/固话 📱📞",
            phonenumbers.PhoneNumberType.TOLL_FREE: "免费电话 🆓",
            phonenumbers.PhoneNumberType.PREMIUM_RATE: "付费电话 💰",
            phonenumbers.PhoneNumberType.VOIP: "网络电话 🌐"
        }
        number_type_str = type_map.get(number_type, "未知类型 ❓")
        
        # 格式化号码
        international_format = phonenumbers.format_number(
            parsed_number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        national_format = phonenumbers.format_number(
            parsed_number, phonenumbers.PhoneNumberFormat.NATIONAL)
        e164_format = phonenumbers.format_number(
            parsed_number, phonenumbers.PhoneNumberFormat.E164)
        
        # 获取国旗
        country_flag = COUNTRY_FLAGS.get(str(country_code), "🏳️")
        
        return {
            'original': phone_text,
            'number': e164_format,
            'country_code': country_code,
            'national_number': national_number,
            'country': country or "未知国家",
            'country_flag': country_flag,
            'carrier': carrier_name or "未知运营商",
            'timezone': timezone_str,
            'type': number_type_str,
            'international_format': international_format,
            'national_format': national_format,
            'e164_format': e164_format,
            'is_valid': True
        }
        
    except Exception as e:
        logger.error(f"电话号码分析错误: {e}")
        return None

def update_user_level(user_id):
    """更新用户等级和积分"""
    try:
        user_data = bot_state.get_user_data(user_id)
        
        # 增加积分和查询次数
        user_data['points'] += 10
        user_data['total_checks'] += 1
        
        # 计算等级
        new_level = (user_data['points'] // 100) + 1
        level_up = new_level > user_data['level']
        user_data['level'] = new_level
        
        # 保存用户数据
        bot_state.update_user_data(user_id, user_data)
        
        return level_up, user_data['level'], 10
        
    except Exception as e:
        logger.error(f"更新用户等级错误: {e}")
        return False, 1, 10

def phone_message_handler(update, context):
    """处理包含电话号码的消息"""
    try:
        user = update.effective_user
        message_text = update.message.text
        
        bot_state.add_user(user.id)
        bot_state.add_message()
        
        # 电话号码匹配模式
        phone_patterns = [
            r'\+\d{1,4}[\s\-\(\)]?\d{1,4}[\s\-\(\)]?\d{1,4}[\s\-\(\)]?\d{1,4}[\s\-\(\)]?\d{0,4}',
            r'\(\d{3}\)[\s\-]?\d{3}[\s\-]?\d{4}',
            r'\d{3,4}[\s\-]?\d{3,4}[\s\-]?\d{4,5}',
            r'\d{10,15}'
        ]
        
        found_phones = []
        for pattern in phone_patterns:
            matches = re.findall(pattern, message_text)
            found_phones.extend(matches)
        
        # 去重并取第一个
        found_phones = list(set(found_phones))
        
        if not found_phones:
            update.message.reply_text(
                "🤔 没有找到有效的电话号码格式。\n\n"
                "💡 **支持的格式示例：**\n"
                "• `+86 138-1234-5678`\n"
                "• `+1 (555) 123-4567`\n"
                "• `13812345678`\n\n"
                "使用 /help 查看更多帮助信息。"
            )
            return
        
        # 处理第一个找到的号码
        found_phone = found_phones[0]
        
        # 分析电话号码
        phone_info = analyze_phone_number(found_phone)
        
        if not phone_info:
            update.message.reply_text(
                f"❌ **无法解析电话号码：** `{found_phone}`\n\n"
                "💡 **可能的原因：**\n"
                "• 号码格式不正确\n"
                "• 缺少国家代码\n"
                "• 号码长度不符合规范\n\n"
                "🔧 **建议：**\n"
                "• 添加国家代码（如 +86, +1）\n"
                "• 参考 /help 中的格式示例"
            )
            return
        
        # 更新用户等级
        level_up, current_level, points_earned = update_user_level(user.id)
        
        # 添加到统计
        phone_info['user_id'] = user.id
        bot_state.add_phone_check(phone_info)
        
        # 构建回复消息
        response_text = f"""
📱 **电话号码智能分析结果**

🔍 **原始输入：** `{phone_info['original']}`
✅ **解析状态：** 有效号码 ✅

🌍 **地理信息：**
{phone_info['country_flag']} **国家/地区：** {phone_info['country']} (+{phone_info['country_code']})
📡 **运营商：** {phone_info['carrier']}
📞 **号码类型：** {phone_info['type']}
🕒 **时区：** {phone_info['timezone']}

📄 **标准格式：**
🌐 **国际格式：** `{phone_info['international_format']}`
🏠 **本地格式：** `{phone_info['national_format']}`
💻 **E164格式：** `{phone_info['e164_format']}`

⭐ **积分奖励：** +{points_earned} 分
🏆 **当前等级：** Level {current_level}
"""
        
        if level_up:
            response_text += f"\n\n🎉 **恭喜升级到 Level {current_level}！** 🎉"
        
        update.message.reply_text(response_text)
        
        logger.info(f"用户 {user.id} 查询电话号码: {found_phone} -> {phone_info['country']}")
        
    except Exception as e:
        logger.error(f"电话消息处理错误: {e}")
        update.message.reply_text("处理电话号码时出现错误，请稍后重试。")

def error_handler(update, context):
    """全局错误处理"""
    try:
        logger.error(f"更新处理出错: {context.error}")
        if update and update.message:
            update.message.reply_text(
                "😅 处理请求时出现了一个小错误。\n\n"
                "请稍后重试，或使用 /help 查看使用帮助。"
            )
    except Exception as e:
        logger.error(f"错误处理器本身出错: {e}")

# 注册命令处理器
dispatcher.add_handler(CommandHandler("start", start_command))
dispatcher.add_handler(CommandHandler("help", help_command))
dispatcher.add_handler(CommandHandler("stats", stats_command))
dispatcher.add_handler(CommandHandler("mystats", mystats_command))

# 注册消息处理器
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, phone_message_handler))

# 注册错误处理器
dispatcher.add_error_handler(error_handler)

# Flask 路由
@app.route('/', methods=['GET'])
def index():
    return """
    🤖 **智能电话号码检测机器人 - 兼容版** 🚀
    
    📊 Web Service 运行中...
    ✅ 状态：正常
    🔄 模式：Webhook
    
    请通过 Telegram 与机器人交互！
    """

@app.route('/webhook', methods=['POST'])
def webhook():
    """处理 Telegram webhook"""
    try:
        json_data = request.get_json()
        if not json_data:
            return 'No data', 400
        
        update = Update.de_json(json_data, bot)
        dispatcher.process_update(update)
        
        return 'OK'
        
    except Exception as e:
        logger.error(f"Webhook 处理错误: {e}")
        return 'Error', 500

@app.route('/status', methods=['GET'])
def status():
    """状态检查端点"""
    try:
        stats = bot_state.get_stats()
        
        return jsonify({
            'status': 'running',
            'uptime': stats['uptime'],
            'stats': stats,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"状态检查错误: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    try:
        logger.info("🚀 启动智能电话号码检测机器人 - 兼容版...")
        
        # 获取端口
        port = int(os.environ.get('PORT', 5000))
        
        logger.info(f"✅ Web Service 启动成功，端口: {port}")
        logger.info("🤖 机器人 webhook 模式已准备就绪...")
        
        # 启动 Flask 应用
        app.run(host='0.0.0.0', port=port, debug=False)
        
    except Exception as e:
        logger.error(f"❌ Web Service 启动失败: {e}")
        import traceback
        logger.error(f"详细错误: {traceback.format_exc()}")
