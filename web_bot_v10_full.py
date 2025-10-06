import os
import re
import json
import threading
import time
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Bot, Update
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters
import pytz

# 配置
BOT_TOKEN = '8424823618:AAFwjIYQH86nKXOiJUybfBRio7sRJl-GUEU'

# 初始化 Flask 应用
app = Flask(__name__)

# 初始化机器人和调度器
bot = Bot(token=BOT_TOKEN)
dispatcher = Dispatcher(bot, None, workers=0)

class BotState:
    def __init__(self):
        self.stats = {
            'queries': 0,
            'successful_queries': 0,
            'failed_queries': 0,
            'start_time': datetime.now().isoformat()
        }
        self.user_stats = {}
        self.user_levels = {}
        self.lock = threading.Lock()
    
    def add_query(self, user_id, success=True):
        with self.lock:
            self.stats['queries'] += 1
            if success:
                self.stats['successful_queries'] += 1
            else:
                self.stats['failed_queries'] += 1
            
            # 用户统计
            if user_id not in self.user_stats:
                self.user_stats[user_id] = {'queries': 0, 'successful': 0}
            
            self.user_stats[user_id]['queries'] += 1
            if success:
                self.user_stats[user_id]['successful'] += 1
            
            # 用户等级系统
            if user_id not in self.user_levels:
                self.user_levels[user_id] = {'level': 1, 'exp': 0}
            
            self.user_levels[user_id]['exp'] += 10 if success else 5
            
            # 升级逻辑
            level_data = self.user_levels[user_id]
            required_exp = level_data['level'] * 100
            if level_data['exp'] >= required_exp:
                level_data['level'] += 1
                level_data['exp'] = 0

# 初始化状态
state = BotState()

def is_phone_number(text):
    """简化的电话号码检测"""
    # 移除所有非数字字符
    digits_only = re.sub(r'\D', '', text)
    
    # 检查是否为合理的电话号码长度 (7-15位数字)
    if 7 <= len(digits_only) <= 15:
        return True
    
    # 检查常见的电话号码格式
    phone_patterns = [
        r'^\+?\d{1,4}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}$',
        r'^\d{3}-\d{3}-\d{4}$',
        r'^\d{3}\.\d{3}\.\d{4}$',
        r'^\+\d{1,15}$'
    ]
    
    for pattern in phone_patterns:
        if re.match(pattern, text.strip()):
            return True
    
    return False

def get_simple_phone_info(phone_text):
    """简化的电话号码信息获取"""
    digits_only = re.sub(r'\D', '', phone_text)
    
    # 基于号码前缀的简单判断
    info = {
        'number': digits_only,
        'formatted': phone_text,
        'country': '未知',
        'region': '未知',
        'carrier': '未知',
        'type': '未知'
    }
    
    # 中国手机号码判断
    if len(digits_only) == 11 and digits_only.startswith('1'):
        info['country'] = '中国'
        info['type'] = '手机号码'
        
        # 基本的运营商判断
        prefix = digits_only[:3]
        if prefix in ['130', '131', '132', '155', '156', '185', '186', '145', '175', '176']:
            info['carrier'] = '中国联通'
        elif prefix in ['134', '135', '136', '137', '138', '139', '150', '151', '152', '157', '158', '159', '182', '183', '184', '187', '188', '147', '178']:
            info['carrier'] = '中国移动'
        elif prefix in ['133', '153', '180', '181', '189', '177']:
            info['carrier'] = '中国电信'
    
    # 美国号码判断
    elif len(digits_only) == 10 and not digits_only.startswith('0'):
        info['country'] = '美国'
        info['type'] = '北美号码'
        
    # 其他国际号码的基本判断
    elif digits_only.startswith('86') and len(digits_only) == 13:
        info['country'] = '中国'
        info['number'] = digits_only[2:]  # 移除国家代码
        
    return info

def start_command(update, context):
    """处理 /start 命令"""
    user_id = update.effective_user.id
    
    welcome_message = """
🤖 **电话号码查询机器人** v10.3

🔍 **功能介绍：**
• 发送电话号码，获取详细信息
• 支持多种号码格式
• 用户等级和积分系统

📱 **支持格式：**
• +86 138 0013 8000
• 138-0013-8000
• 13800138000
• (555) 123-4567

⭐ **命令列表：**
/help - 帮助信息
/stats - 系统统计
/mystats - 我的统计

直接发送电话号码开始查询！
"""
    
    update.message.reply_text(welcome_message, parse_mode='Markdown')

def help_command(update, context):
    """处理 /help 命令"""
    help_text = """
🆘 **帮助信息**

**如何使用：**
1. 直接发送电话号码给我
2. 我会分析并返回详细信息

**支持的格式：**
• +86 138 0013 8000
• 138-0013-8000  
• 13800138000
• (555) 123-4567

**可用命令：**
/start - 开始使用
/help - 显示帮助
/stats - 查看系统统计
/mystats - 查看个人统计

有问题请重新发送号码或联系管理员！
"""
    update.message.reply_text(help_text, parse_mode='Markdown')

def stats_command(update, context):
    """处理 /stats 命令"""
    beijing_tz = pytz.timezone('Asia/Shanghai')
    start_time = datetime.fromisoformat(state.stats['start_time'])
    runtime = datetime.now() - start_time
    
    stats_text = f"""
📊 **系统统计信息**

🕒 **运行时间：** {runtime.days}天 {runtime.seconds//3600}小时
🔍 **总查询次数：** {state.stats['queries']}
✅ **成功查询：** {state.stats['successful_queries']}
❌ **失败查询：** {state.stats['failed_queries']}
📈 **成功率：** {(state.stats['successful_queries']/max(state.stats['queries'], 1)*100):.1f}%
👥 **活跃用户：** {len(state.user_stats)}

🤖 **版本：** v10.3
"""
    
    update.message.reply_text(stats_text, parse_mode='Markdown')

def mystats_command(update, context):
    """处理 /mystats 命令"""
    user_id = update.effective_user.id
    username = update.effective_user.username or "无用户名"
    
    if user_id not in state.user_stats:
        update.message.reply_text("您还没有查询记录，发送一个电话号码开始吧！")
        return
    
    user_data = state.user_stats[user_id]
    level_data = state.user_levels.get(user_id, {'level': 1, 'exp': 0})
    
    success_rate = (user_data['successful']/max(user_data['queries'], 1)*100)
    
    mystats_text = f"""
👤 **个人统计信息**

🏷️ **用户：** @{username}
🆔 **ID：** {user_id}
⭐ **等级：** {level_data['level']}
💎 **经验值：** {level_data['exp']}/100
🔍 **查询次数：** {user_data['queries']}
✅ **成功次数：** {user_data['successful']}
📈 **成功率：** {success_rate:.1f}%

继续查询电话号码获得更多经验值！
"""
    
    update.message.reply_text(mystats_text, parse_mode='Markdown')

def handle_message(update, context):
    """处理普通消息"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    # 检查是否为电话号码
    if not is_phone_number(text):
        update.message.reply_text("❌ 这不像是一个有效的电话号码。请发送正确的电话号码格式。")
        state.add_query(user_id, success=False)
        return
    
    # 获取电话号码信息
    try:
        phone_info = get_simple_phone_info(text)
        
        response = f"""
📱 **电话号码信息**

🔢 **号码：** {phone_info['formatted']}
🏳️ **国家/地区：** {phone_info['country']}
🏢 **运营商：** {phone_info['carrier']}
📶 **类型：** {phone_info['type']}

✅ 查询成功！
"""
        
        update.message.reply_text(response, parse_mode='Markdown')
        state.add_query(user_id, success=True)
        
    except Exception as e:
        update.message.reply_text(f"❌ 查询失败：{str(e)}")
        state.add_query(user_id, success=False)

# 注册处理器
dispatcher.add_handler(CommandHandler("start", start_command))
dispatcher.add_handler(CommandHandler("help", help_command))
dispatcher.add_handler(CommandHandler("stats", stats_command))
dispatcher.add_handler(CommandHandler("mystats", mystats_command))
dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

@app.route('/webhook', methods=['POST'])
def webhook():
    """处理 Telegram webhook"""
    try:
        json_data = request.get_json()
        update = Update.de_json(json_data, bot)
        dispatcher.process_update(update)
        return jsonify({'status': 'ok'})
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/status', methods=['GET'])
def status():
    """健康检查端点"""
    return jsonify({
        'status': 'running',
        'bot_info': bot.get_me().to_dict(),
        'stats': state.stats
    })

@app.route('/', methods=['GET'])
def home():
    """主页"""
    return jsonify({
        'message': '电话号码查询机器人 v10.3',
        'status': 'running',
        'webhook_url': '/webhook'
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
